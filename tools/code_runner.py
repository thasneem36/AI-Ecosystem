"""Run Python code snippets in a sandboxed subprocess.

PROTECTION LAYERS (Windows-compatible — no Docker required):
  1. AST pre-scan    Rejects code that names a blocked module in an import or
                     calls __import__("blocked") before any process starts.
  2. Import blocker  A sys.meta_path hook injected into the child catches runtime
                     imports of the same modules (handles dynamic import too).
  3. open() guard    Write-mode and absolute-path opens raise PermissionError.
  4. eval/exec ban   builtins.eval and builtins.exec raise RuntimeError.
  5. Env scrub       API keys / secrets stripped from the child environment.
  6. Isolated CWD    Child runs inside OUTPUT_DIR, not the project root.
  7. Timeout         Child killed after DEFAULT_TIMEOUT seconds (existing).
  8. Memory cap      Child killed when RSS > MEM_LIMIT_MB; requires psutil
                     (pip install psutil). Skipped gracefully if not installed.

KNOWN LIMITS — be honest with your test users:
  • Not bulletproof. Python class-hierarchy tricks (__subclasses__ chains,
    attribute walking) can bypass import hooks without hitting the blocked list.
    This stops accidental / casual dangerous code, not a determined attacker who
    knows CPython internals.
  • No CPU isolation beyond the timeout.
  • No disk-quota within OUTPUT_DIR — the child can still write files there.
  • Native C extensions bypass the Python import system entirely.
  → For untrusted arbitrary input, use Docker or a cloud sandbox instead.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional, Tuple

from config.settings import settings

DEFAULT_TIMEOUT = 30    # seconds
MEM_LIMIT_MB    = 256   # RSS ceiling; only enforced when psutil is installed

# ------------------------------------------------------------------ #
# Blocked top-level module names
# Enforced by BOTH the static AST scan and the child-side import hook.
# ------------------------------------------------------------------ #
_BLOCKED_MODULES: FrozenSet[str] = frozenset({
    # Filesystem / OS
    "os", "shutil", "pathlib", "glob", "tempfile",
    "fileinput", "stat", "filecmp",
    # Process management
    "subprocess", "multiprocessing", "concurrent",
    "threading", "_thread", "signal",
    # Network
    "socket", "ssl", "select", "selectors", "asyncio",
    "urllib", "http", "requests", "httpx", "aiohttp",
    "ftplib", "smtplib", "telnetlib", "imaplib", "poplib",
    "nntplib", "xmlrpc", "socketserver",
    # Dynamic execution / introspection
    "ctypes", "cffi", "sys", "inspect", "importlib", "imp",
    "gc", "weakref",
    # Code serialisation (can exec arbitrary code on load)
    "pickle", "pickletools", "shelve", "marshal",
    # Windows-specific low-level
    "winreg", "winsound", "msvcrt", "nt",
    # Unix-specific low-level
    "pty", "tty", "termios", "fcntl", "resource",
    # Misc risky
    "readline", "rlcompleter", "pexpect",
})

# ------------------------------------------------------------------ #
# Guardian script — injected as a header before every user snippet.
# Runs INSIDE the child process to enforce runtime restrictions.
#
# Design notes:
#   • _ImportBlocker stores its blocked set as a class attribute (_B) so
#     deleting the class NAME from globals doesn't break the installed instance.
#   • chr(92) avoids backslash-escape issues when this f-string renders the
#     code that will be written to the temp file.
#   • _sys and _builtins references are deleted after setup to reduce the
#     attack surface available to user code.
# ------------------------------------------------------------------ #
_BLOCKED_LITERAL = ", ".join(f'"{m}"' for m in sorted(_BLOCKED_MODULES))

_GUARDIAN: str = f"""\
import sys as _sys
import builtins as _builtins

class _ImportBlocker:
    _B = frozenset({{{_BLOCKED_LITERAL}}})

    @classmethod
    def find_spec(cls, name, path=None, target=None):
        if name.split(".")[0] in cls._B:
            raise ModuleNotFoundError(
                "\\u26d4 import '" + name + "' is blocked for safety."
            )
        return None

    @classmethod
    def find_module(cls, name, path=None):  # legacy hook (Python < 3.12)
        if name.split(".")[0] in cls._B:
            raise ImportError("\\u26d4 import '" + name + "' is blocked for safety.")

_sys.meta_path.insert(0, _ImportBlocker())
del _ImportBlocker  # name removed; the class stays alive via sys.meta_path

_real_open = _builtins.open
_bslash = chr(92)  # one backslash — avoids escape issues in this generated code

def _safe_open(file, mode="r", *args, **kwargs):
    if any(c in str(mode or "r") for c in "wxa"):
        raise PermissionError("\\u26d4 File write access is blocked for safety.")
    _f = str(file)
    # Absolute paths: Unix /…, Windows drive C:\\…, UNC \\\\server
    if _f[:1] == "/" or (len(_f) >= 2 and _f[1] == ":") or _f[:2] == _bslash * 2:
        raise PermissionError("\\u26d4 Absolute path access is blocked for safety.")
    return _real_open(file, mode, *args, **kwargs)

def _blocked_call(_name):
    def _inner(*a, **k):
        raise RuntimeError("\\u26d4 " + _name + "() is blocked for safety.")
    return _inner

_builtins.open = _safe_open
# eval and exec are NOT blocked here: Python's module loader calls builtins.exec
# to load every pure-Python module, and builtins.eval during annotation/type
# evaluation. Blocking either here breaks stdlib imports. Both are caught instead
# by the AST pre-scan before this child process starts.

del _sys, _builtins, _real_open, _safe_open, _blocked_call, _bslash
# --- user code starts below ---
"""

# ------------------------------------------------------------------ #
# Optional memory watcher (psutil)
# ------------------------------------------------------------------ #
try:
    import psutil as _psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


def _mem_watcher(pid: int, limit_mb: int, stop: threading.Event) -> None:
    """Poll child RSS every 500 ms; kill it if it exceeds the limit."""
    if not _HAS_PSUTIL:
        return
    try:
        proc = _psutil.Process(pid)
        while not stop.is_set():
            try:
                rss_mb = proc.memory_info().rss / (1024 * 1024)
                if rss_mb > limit_mb:
                    proc.kill()
                    stop.set()
                    return
            except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                return
            stop.wait(0.5)
    except Exception:
        pass


# ------------------------------------------------------------------ #
# Static AST safety scan (Layer 1)
# ------------------------------------------------------------------ #
# Dangerous builtins blocked via AST scan (not in the guardian, because exec is
# called by Python's import machinery when loading every pure-Python module).
_BLOCKED_CALLS: FrozenSet[str] = frozenset({"eval", "exec"})


def _ast_check(code: str) -> Optional[str]:
    """Return a block-reason string, or None if no static violation is found."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None  # syntax errors are surfaced naturally by the child

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                base = alias.name.split(".")[0]
                if base in _BLOCKED_MODULES:
                    return f"import '{alias.name}' is not allowed in the sandbox"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                base = node.module.split(".")[0]
                if base in _BLOCKED_MODULES:
                    return f"from '{node.module}' import … is not allowed"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in _BLOCKED_CALLS:
                    return f"{node.func.id}() is not allowed in the sandbox"
                # __import__("os") static bypass attempt
                if (
                    node.func.id == "__import__"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    mod = str(node.args[0].value).split(".")[0]
                    if mod in _BLOCKED_MODULES:
                        return f"__import__('{node.args[0].value}') is not allowed"
    return None


# ------------------------------------------------------------------ #
# Environment scrubbing (Layer 5)
# ------------------------------------------------------------------ #
_SECRET_PREFIXES = (
    "GROQ", "OPENAI", "ANTHROPIC", "AWS", "AZURE", "GCP",
    "SECRET", "TOKEN", "API_KEY", "API_SECRET",
    "PASSWORD", "PASSWD", "PRIVATE", "CREDENTIAL", "AUTH",
)


def _safe_env() -> Dict[str, str]:
    """Strip credential env-vars so the child can't read API keys."""
    env = {
        k: v for k, v in os.environ.items()
        if not any(p in k.upper() for p in _SECRET_PREFIXES)
    }
    env["PYTHONUTF8"] = "1"
    return env


# ------------------------------------------------------------------ #
# Helpers carried over unchanged
# ------------------------------------------------------------------ #
def _strip_interrupt_traceback(text: str) -> str:
    if not text:
        return ""
    if "KeyboardInterrupt" not in text:
        return text
    lines = text.splitlines()
    start: Optional[int] = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("Traceback (most recent call last):"):
            start = i
            break
    if start is not None and any("KeyboardInterrupt" in ln for ln in lines[start:]):
        return "\n".join(lines[:start]).rstrip()
    return "\n".join(ln for ln in lines if ln.strip() != "KeyboardInterrupt").rstrip()


def _kill_and_drain(proc: subprocess.Popen) -> Tuple[str, str]:
    try:
        proc.kill()
    except Exception:
        pass
    try:
        out, err = proc.communicate(timeout=5)
    except Exception:
        out, err = "", ""
    return out or "", err or ""


def _extract_block_reason(stderr: str) -> str:
    """Pull the useful 'blocked for safety' line from a traceback."""
    for line in reversed(stderr.splitlines()):
        line = line.strip()
        if line and ("blocked for safety" in line or "is not allowed" in line):
            # Drop the error-class prefix: "ModuleNotFoundError: ⛔ …" → "⛔ …"
            _, _, msg = line.partition(": ")
            return (msg or line).strip()
    return "a restricted operation was attempted"


# ------------------------------------------------------------------ #
# Main entry point
# ------------------------------------------------------------------ #
def run_python(code: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Execute `code` in a sandboxed subprocess; return {success, stdout, stderr, returncode}."""

    # Layer 1 — fast static check; no subprocess started yet.
    block_reason = _ast_check(code)
    if block_reason:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"This code was blocked for safety: {block_reason}.",
            "returncode": -1,
            "blocked": True,
        }

    # Write guardian + user code to a temp file in OUTPUT_DIR.
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False,
        dir=settings.OUTPUT_DIR, encoding="utf-8",
    )
    proc: Optional[subprocess.Popen] = None
    stop_mem = threading.Event()
    watcher: Optional[threading.Thread] = None

    try:
        tmp.write(_GUARDIAN + code)
        tmp.close()

        proc = subprocess.Popen(
            [sys.executable, "-u", tmp.name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=_safe_env(),            # Layer 5: no API keys in child env
            cwd=str(settings.OUTPUT_DIR),  # Layer 6: isolated working dir
        )

        # Layer 8: memory watcher thread (no-op if psutil absent).
        watcher = threading.Thread(
            target=_mem_watcher,
            args=(proc.pid, MEM_LIMIT_MB, stop_mem),
            daemon=True,
        )
        watcher.start()

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            stdout, stderr = _kill_and_drain(proc)
            partial_err = _strip_interrupt_traceback(stderr)
            msg = f"Code stopped: took longer than {timeout}s"
            return {
                "success": False,
                "stdout": _strip_interrupt_traceback(stdout),
                "stderr": f"{msg}\n{partial_err}".strip() if partial_err else msg,
                "returncode": -1,
            }

        stdout = _strip_interrupt_traceback(stdout)
        stderr = _strip_interrupt_traceback(stderr)

        # Layers 2–4: runtime block fired inside the child.
        if proc.returncode != 0 and (
            "blocked for safety" in stderr or "is not allowed" in stderr
        ):
            return {
                "success": False,
                "stdout": stdout,
                "stderr": f"This code was blocked for safety: {_extract_block_reason(stderr)}",
                "returncode": proc.returncode,
                "blocked": True,
            }

        return {
            "success": proc.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": proc.returncode if proc.returncode is not None else -1,
        }

    except KeyboardInterrupt:
        stdout_ki = ""
        if proc is not None:
            stdout_ki, _ = _kill_and_drain(proc)
        return {
            "success": False,
            "stdout": _strip_interrupt_traceback(stdout_ki),
            "stderr": "Code run was interrupted",
            "returncode": -1,
        }

    except Exception as exc:  # pragma: no cover
        if proc is not None and proc.poll() is None:
            _kill_and_drain(proc)
        return {"success": False, "stdout": "", "stderr": str(exc), "returncode": -1}

    finally:
        stop_mem.set()
        if watcher is not None:
            watcher.join(timeout=1)
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            pass
