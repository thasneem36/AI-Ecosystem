"""Terminal chat client for the AI Ecosystem.

Reuses the EXACT pipeline from api/server.py (router → agents → memory →
metrics). No logic is duplicated here — this file only handles input/output.

Run:  python run_terminal.py
Type 'exit' (or 'quit') to leave. Conversations are saved to memory as you go.
"""
from __future__ import annotations

import sys

# Make the console Unicode-safe (Windows cp1252 can't encode emoji otherwise).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from colorama import Fore, Style, init as colorama_init

from config.settings import settings
# Reuse the real backend pipeline + its session counters — do not re-implement.
from api.server import chat, ChatRequest, SESSION_STATS

colorama_init(autoreset=True)

# Terminal colour per agent (mirrors the web UI's colour scheme).
AGENT_COLORS = {
    "Router": Fore.WHITE,
    "Assistant": Fore.WHITE,
    "Planner": Fore.YELLOW,
    "Executor": Fore.CYAN,
    "Coding": Fore.GREEN,
    "Tutor": Fore.MAGENTA,
    "Clarifier": Fore.YELLOW,
    "Web Search": Fore.CYAN,
    "System": Fore.WHITE,
}


def _n(v: object) -> str:
    return f"{v:,}" if isinstance(v, (int, float)) else "n/a"


def usage_line(m: dict) -> str:
    """Build the per-response usage line with REAL numbers (or n/a)."""
    inp, out, tot = m.get("input_tokens"), m.get("output_tokens"), m.get("total_tokens")
    if inp is None and out is None and tot is None:
        tokens = "🔤 tokens n/a"
    else:
        tokens = f"🔤 {_n(tot or 0)} tokens ({_n(inp or 0)} in / {_n(out or 0)} out)"
    return (
        f"⏱ {m.get('total_time_seconds', 0)}s · "
        f"🔁 {m.get('api_calls', 0)} calls · "
        f"{tokens} · {m.get('model', '?')}"
    )


def session_line() -> str:
    calls = SESSION_STATS["api_calls"]
    tokens = SESSION_STATS["input_tokens"] + SESSION_STATS["output_tokens"]
    tok = f"{tokens:,} tokens" if tokens else "tokens n/a"
    return f"Session: {calls} calls · {tok}"


def banner(model: str) -> None:
    print(Fore.GREEN + Style.BRIGHT + "=" * 56)
    print(Fore.GREEN + Style.BRIGHT + "  🤖  AI ECOSYSTEM — Terminal")
    print(Fore.GREEN + "=" * 56)
    print(f"{Fore.CYAN}Model  : {Fore.WHITE}{model}")
    print(f"{Fore.CYAN}Quit   : {Fore.WHITE}type 'exit' or 'quit'")
    print(Fore.GREEN + "=" * 56 + Style.RESET_ALL)


def main() -> None:
    model = settings.DEFAULT_BACKEND  # single source of truth (config/settings.py)
    banner(model)

    while True:
        try:
            text = input(Fore.GREEN + Style.BRIGHT + "\nyou › " + Style.RESET_ALL).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not text:
            continue
        if text.lower() in ("exit", "quit"):
            break

        try:
            result = chat(ChatRequest(message=text, model=model))
        except Exception as exc:  # noqa: BLE001
            print(Fore.RED + f"error: {exc}" + Style.RESET_ALL)
            continue

        route = result.get("route", "?")
        for msg in result.get("messages", []):
            color = AGENT_COLORS.get(msg.get("agent", ""), Fore.WHITE)
            header = f"{msg.get('agent', 'Agent')}  [{route}]"
            print(f"\n{color}{Style.BRIGHT}{header}{Style.RESET_ALL}")
            print(color + (msg.get("content") or "") + Style.RESET_ALL)

        metrics = result.get("metrics", {})
        print(Style.DIM + "\n" + usage_line(metrics) + Style.RESET_ALL)
        print(Style.DIM + session_line() + Style.RESET_ALL)

    print(Fore.GREEN + "\nMemory saved. Bye! 👋" + Style.RESET_ALL)


if __name__ == "__main__":
    main()
