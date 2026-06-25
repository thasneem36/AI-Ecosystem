"""Entry point for the Koottam backend.

Run with:  python main.py
"""
import sys

import uvicorn
from colorama import Fore, Style, init as colorama_init

from config.settings import settings

# Make the console Unicode-safe (Windows cp1252 can't encode emoji otherwise).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

colorama_init(autoreset=True)


def banner() -> None:
    print(Fore.GREEN + "=" * 50)
    print(Fore.GREEN + Style.BRIGHT + "   🤖  KOOTTAM — Backend")
    print(Fore.GREEN + "=" * 50)
    print(f"{Fore.CYAN}Server : {Fore.WHITE}http://{settings.HOST}:{settings.PORT}")
    print(f"{Fore.CYAN}Docs   : {Fore.WHITE}http://{settings.HOST}:{settings.PORT}/docs")
    print(f"{Fore.CYAN}Ollama : {Fore.WHITE}{settings.OLLAMA_BASE_URL} ({settings.OLLAMA_MODEL})")
    print(Fore.GREEN + "=" * 50)


if __name__ == "__main__":
    banner()
    uvicorn.run(
        "api.server:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD,
    )
