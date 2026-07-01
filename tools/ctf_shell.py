"""
CTF shell execution tool — personal use only.

Runs a single shell command inside Kali-WSL and returns the REAL stdout,
stderr, and exit code.  Never fabricates or summarises output.

Completely separate from tools/code_runner.py (sandboxed Python runner).
Do NOT import or reuse anything from that module here.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from typing import Optional

# When running directly as a script (python tools/ctf_shell.py), Python puts
# the tools/ directory on sys.path instead of the project root, so package
# imports like `from auth...` fail.  Fix it before any project imports below.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from auth.auth_manager import auth_manager

# ── Distro name — change here if yours differs ────────────────────────────────
WSL_DISTRO = "kali-linux"

# ── Auth ──────────────────────────────────────────────────────────────────────
_sec = HTTPBearer(auto_error=False)


def _require_admin(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_sec),
):
    # PERSONAL ACCOUNT ONLY — never expose to multi-user/Railway deployment
    # No bootstrap-mode bypass: shell access always requires a valid admin token.
    if not creds:
        raise HTTPException(401, "Not authenticated")
    acc = auth_manager.verify_session(creds.credentials)
    if not acc:
        raise HTTPException(401, "Invalid or expired session")
    if acc.get("role") != "admin":
        raise HTTPException(403, "Admin access required — personal use only")
    return acc


# ── Core function ─────────────────────────────────────────────────────────────
def _is_windows() -> bool:
    return platform.system() == "Windows"


def run_shell_command(command: str, timeout: int = 60) -> dict:
    """Run *command* in Kali-WSL (or locally when already inside Linux/WSL).

    Returns the REAL output — stdout, stderr, exit_code, timed_out.
    On timeout the process is killed and whatever was captured is returned.
    """
    if _is_windows():
        # -u root: run as root so privileged tools (nmap SYN scan, etc.) work
        # without sudo prompts that would hang the process.
        cmd = ["wsl.exe", "-d", WSL_DISTRO, "-u", "root", "bash", "-c", command]
    else:
        cmd = ["bash", "-c", command]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,   # no input source → interactive prompts fail fast
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills the process before re-raising; collect leftovers.
        raw_out = exc.stdout or b""
        raw_err = exc.stderr or b""
        stdout = raw_out.decode("utf-8", errors="replace") if isinstance(raw_out, bytes) else (raw_out or "")
        stderr = raw_err.decode("utf-8", errors="replace") if isinstance(raw_err, bytes) else (raw_err or "")
        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": -1,
            "timed_out": True,
        }


# ── FastAPI router ─────────────────────────────────────────────────────────────
router = APIRouter(prefix="/ctf", tags=["ctf"])


class ShellRequest(BaseModel):
    command: str
    timeout: int = 60


class ShellResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool


@router.post("/shell", response_model=ShellResponse)
def ctf_shell(body: ShellRequest, _account=Depends(_require_admin)):
    # PERSONAL ACCOUNT ONLY — never expose to multi-user/Railway deployment
    return run_shell_command(body.command, body.timeout)


# ── Direct test (bypasses the API layer entirely) ─────────────────────────────
if __name__ == "__main__":
    import json

    test_commands = [
        "whoami",
        "nmap --version",
        "echo flag{test_123}",
    ]

    for cmd in test_commands:
        print(f"\n{'=' * 60}")
        print(f"Command : {cmd}")
        result = run_shell_command(cmd)
        print(json.dumps(result, indent=2))
