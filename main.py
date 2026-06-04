"""Entry point for the AI Ecosystem backend.

Run with:  python main.py
"""
import uvicorn
from colorama import Fore, Style, init as colorama_init

from config.settings import settings

colorama_init(autoreset=True)


def banner() -> None:
    print(Fore.GREEN + "=" * 50)
    print(Fore.GREEN + Style.BRIGHT + "   🤖  AI ECOSYSTEM — Backend")
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
