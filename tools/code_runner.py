"""Run Python code snippets in an isolated subprocess.

Used by the coding agent to actually execute the code it writes. Output is
captured and a timeout protects against runaway scripts. This is meant for
local/trusted use only.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from config.settings import settings

DEFAULT_TIMEOUT = 30  # seconds — room for normal scripts to finish


def _strip_interrupt_traceback(text: str) -> str:
    """Remove a KeyboardInterrupt traceback a half-killed child can leak.

    A subprocess terminated mid-run (e.g. the dev server reloads, or Ctrl+C)
    may dump a KeyboardInterrupt traceback to stderr. That noise must never
    reach the user, so we strip the trailing traceback block here.
    """
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
    # No proper traceback header — just drop bare KeyboardInterrupt lines.
    return "\n".join(ln for ln in lines if ln.strip() != "KeyboardInterrupt").rstrip()


def _kill_and_drain(proc: subprocess.Popen) -> Tuple[str, str]:
    """Kill a still-running child and collect whatever it already produced."""
    try:
        proc.kill()
    except Exception:
        pass
    try:
        out, err = proc.communicate(timeout=5)
    except Exception:
        out, err = "", ""
    return out or "", err or ""


def run_python(code: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Execute Python `code` and return {success, stdout, stderr, returncode}.

    The child is always terminated and reaped, both stdout and stderr are
    captured even when the run is cut short, and no raw KeyboardInterrupt
    traceback is ever returned.
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, dir=settings.OUTPUT_DIR, encoding="utf-8"
    )
    proc: Optional[subprocess.Popen] = None
    try:
        tmp.write(code)
        tmp.close()

        # -u = unbuffered, so partial stdout/stderr survives if we kill the child.
        proc = subprocess.Popen(
            [sys.executable, "-u", tmp.name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(settings.OUTPUT_DIR),
        )

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Runaway script — kill it but keep whatever it printed so far.
            stdout, stderr = _kill_and_drain(proc)
            partial_err = _strip_interrupt_traceback(stderr)
            msg = f"Code stopped: took longer than {timeout}s"
            return {
                "success": False,
                "stdout": _strip_interrupt_traceback(stdout),
                "stderr": f"{msg}\n{partial_err}".strip() if partial_err else msg,
                "returncode": -1,
            }

        return {
            "success": proc.returncode == 0,
            "stdout": _strip_interrupt_traceback(stdout),
            "stderr": _strip_interrupt_traceback(stderr),
            "returncode": proc.returncode if proc.returncode is not None else -1,
        }

    except KeyboardInterrupt:
        # The run was interrupted (Ctrl+C, or the dev server reloading).
        # Clean up the child and report plainly — never surface a traceback.
        stdout = ""
        if proc is not None:
            stdout, _ = _kill_and_drain(proc)
        return {
            "success": False,
            "stdout": _strip_interrupt_traceback(stdout),
            "stderr": "Code run was interrupted",
            "returncode": -1,
        }

    except Exception as exc:  # pragma: no cover
        if proc is not None and proc.poll() is None:
            _kill_and_drain(proc)
        return {"success": False, "stdout": "", "stderr": str(exc), "returncode": -1}

    finally:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            pass
