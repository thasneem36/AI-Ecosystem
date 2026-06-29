"""Central configuration for Koottam.

All values can be overridden via the .env file at the project root.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = parent of the config/ directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env (if present)
load_dotenv(BASE_DIR / ".env")


def _get_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    """Application settings singleton."""

    # ----- Server -----
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))
    RELOAD: bool = _get_bool("RELOAD", True)

    # ===================================================================== #
    #  MODEL SELECTION — THE SINGLE PLACE TO CHANGE THE MODEL               #
    # --------------------------------------------------------------------- #
    #  👉 Change the local model name HERE and nowhere else. Every agent     #
    #     (router, planner, executor, coding, search, assistant) reads this  #
    #     value via settings.OLLAMA_MODEL — no model name is hardcoded       #
    #     anywhere in agents/ or api/.                                       #
    #                                                                        #
    #  Note: an OLLAMA_MODEL entry in .env overrides this default. The .env  #
    #     is kept in sync, so this string is the effective model.            #
    # ===================================================================== #
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen3.5")

    # ----- Ollama connection -----
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "120"))

    # Which backend the agents use by default: "ollama" | "groq" | "claude".
    # --------------------------------------------------------------------- #
    #  ✅ DEFAULT = "groq" — fast hosted model, no local timeouts.           #
    #     The pipeline makes several model calls per message; the big local  #
    #     Ollama model (qwen3.5) times out at OLLAMA_TIMEOUT, so Groq is the  #
    #     default for building/testing.                                      #
    #                                                                        #
    #  🔌 Ollama is the OFFLINE FALLBACK and stays fully available:          #
    #        • selectable per-message in the chat UI and in System Settings  #
    #        • to make it the default again, set DEFAULT_BACKEND = "ollama"  #
    #          right here (one place) — nothing else to change.              #
    #                                                                        #
    #  (Same spot to default to a paid Claude model: set "claude" +          #
    #   ANTHROPIC_API_KEY in .env.)                                          #
    # --------------------------------------------------------------------- #
    DEFAULT_BACKEND: str = os.getenv("DEFAULT_BACKEND", "groq")

    # ----- Claude API (paid alternative) -----
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    # ----- Groq API (fast hosted Llama, OpenAI-compatible) -----
    # Reads GROQ_API (your key name) and falls back to GROQ_API_KEY.
    #   👉 Paste your key in .env as:  GROQ_API=gsk_xxxxxxxx
    GROQ_API_KEY: str = os.getenv("GROQ_API", "") or os.getenv("GROQ_API_KEY", "")
    GROQ_BASE_URL: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    #   👉 Groq model is set HERE (valid + fast as of 2025: "llama-3.3-70b-versatile").
    #      For an even faster/cheaper option use "llama-3.1-8b-instant".
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # ----- Rate limiting (burst cap, separate from the token budget) -----
    # Max requests a single user may make within the window.
    # Set RATE_LIMIT_ADMIN_EXEMPT=false in .env to rate-limit admins too.
    RATE_LIMIT_REQUESTS: int = int(os.getenv("RATE_LIMIT_REQUESTS", "20"))
    RATE_LIMIT_WINDOW: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))   # seconds
    RATE_LIMIT_ADMIN_EXEMPT: bool = _get_bool("RATE_LIMIT_ADMIN_EXEMPT", True)

    # ----- Paths -----
    BASE_DIR: Path = BASE_DIR
    OUTPUT_DIR: Path = BASE_DIR / "output"
    MEMORY_FILE: Path = BASE_DIR / "memory" / "memory.json"  # legacy; kept for migration read
    DB_FILE: Path = BASE_DIR / "memory" / "koottam.db"

    # ----- CORS -----
    CORS_ORIGINS: list = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")

    def __init__(self) -> None:
        # Ensure required directories exist
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
