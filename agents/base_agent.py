"""Base class shared by every agent.

Handles talking to the local Ollama server (and optionally the Claude API),
tracks a lightweight status, and exposes a uniform `run()` interface.
"""
from __future__ import annotations

import contextvars
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from config.settings import settings

# ----------------------------------------------------------------------------- #
# Per-request usage accumulator
# ----------------------------------------------------------------------------- #
# Each model call appends a usage record here. It's a ContextVar so concurrent
# requests don't mix metrics. server.chat() resets it, runs the pipeline, then
# reads it back to build the per-response "metrics" object.
_usage_log: contextvars.ContextVar = contextvars.ContextVar("usage_log", default=None)


def reset_usage() -> None:
    """Start a fresh usage log for the current request."""
    _usage_log.set([])


def record_usage(
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    error: bool = False,
) -> None:
    """Record one model/API call (called automatically by think()).

    `error=True` marks a call that failed (backend unreachable, API error, …)
    so the caller can short-circuit the pipeline instead of treating the
    failure text as a real answer.
    """
    log = _usage_log.get()
    if log is None:
        log = []
        _usage_log.set(log)
    log.append(
        {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "error": error,
        }
    )


def get_usage() -> List[Dict[str, Any]]:
    """Return the calls recorded since the last reset_usage()."""
    log = _usage_log.get()
    return list(log) if log else []


def had_error() -> bool:
    """True if ANY model call in the current request failed."""
    log = _usage_log.get()
    return bool(log) and any(c.get("error") for c in log)


def last_call_failed() -> bool:
    """True if the most recent model call failed (for mid-pipeline short-circuit)."""
    log = _usage_log.get()
    return bool(log) and bool(log[-1].get("error"))


class BaseAgent:
    #: Display name, e.g. "Planner"
    name: str = "Base"
    #: UI colour key consumed by the frontend
    color: str = "white"
    #: System prompt describing the agent's role
    system_prompt: str = "You are a helpful AI agent."

    def __init__(self) -> None:
        self.status: str = "idle"  # idle | thinking | active | offline | error
        self.last_activity: Optional[str] = None
        self.enabled: bool = True  # toggled by Agent Control start/stop

    # ------------------------------------------------------------------ #
    # Model backends
    # ------------------------------------------------------------------ #
    def _ollama_chat(self, prompt: str) -> tuple[str, Dict[str, Optional[int]]]:
        url = f"{settings.OLLAMA_BASE_URL}/api/chat"
        payload = {
            "model": settings.OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        resp = requests.post(url, json=payload, timeout=settings.OLLAMA_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        text = data.get("message", {}).get("content", "").strip()
        usage = {
            "input_tokens": data.get("prompt_eval_count"),
            "output_tokens": data.get("eval_count"),
        }
        return text, usage

    def _groq_chat(self, prompt: str) -> tuple[str, Dict[str, Optional[int]]]:
        if not settings.GROQ_API_KEY:
            raise RuntimeError("GROQ_API key is not set in .env")
        url = f"{settings.GROQ_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.GROQ_MODEL,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=settings.OLLAMA_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        # Groq is OpenAI-compatible: usage.prompt_tokens / completion_tokens.
        u = data.get("usage", {}) or {}
        usage = {
            "input_tokens": u.get("prompt_tokens", u.get("input_tokens")),
            "output_tokens": u.get("completion_tokens", u.get("output_tokens")),
        }
        return text, usage

    def _claude_chat(self, prompt: str) -> tuple[str, Dict[str, Optional[int]]]:
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": settings.CLAUDE_MODEL,
            "max_tokens": 1024,
            "system": self.system_prompt,
            "messages": [{"role": "user", "content": prompt}],
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=settings.OLLAMA_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", [])).strip()
        u = data.get("usage", {}) or {}
        usage = {
            "input_tokens": u.get("input_tokens"),
            "output_tokens": u.get("output_tokens"),
        }
        return text, usage

    @staticmethod
    def _model_name(model: str) -> str:
        return {
            "claude": settings.CLAUDE_MODEL,
            "groq": settings.GROQ_MODEL,
        }.get(model, settings.OLLAMA_MODEL)

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #
    def think(self, prompt: str, model: str = "ollama") -> str:
        """Send `prompt` to the chosen backend and return the text reply."""
        self.status = "thinking"
        self.last_activity = datetime.now().isoformat()
        model_name = self._model_name(model)
        try:
            if model == "claude":
                reply, usage = self._claude_chat(prompt)
            elif model == "groq":
                reply, usage = self._groq_chat(prompt)
            else:
                reply, usage = self._ollama_chat(prompt)
            self.status = "active"
            record_usage(model_name, usage.get("input_tokens"), usage.get("output_tokens"))
            return reply
        except requests.exceptions.ConnectionError:
            self.status = "offline"
            record_usage(model_name, None, None, error=True)  # call attempt, failed
            if model == "ollama":
                return (
                    f"[{self.name}] Could not reach Ollama on {settings.OLLAMA_BASE_URL}. "
                    "Is it running?"
                )
            return f"[{self.name}] Could not reach the {model} API. Check your network/API key."
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            record_usage(model_name, None, None, error=True)
            return f"[{self.name}] Error: {exc}"

    def run(self, task: str, context: Optional[Dict[str, Any]] = None, model: str = "ollama") -> Dict[str, Any]:
        """Override in subclasses. Default just relays the task to the model."""
        content = self.think(task, model=model)
        return self._message(content)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _message(self, content: str, **extra: Any) -> Dict[str, Any]:
        msg = {
            "agent": self.name,
            "color": self.color,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        msg.update(extra)
        return msg

    def status_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "color": self.color,
            # A disabled agent always reads as offline, regardless of last run.
            "status": "offline" if not self.enabled else self.status,
            "enabled": self.enabled,
            "last_activity": self.last_activity,
        }

    def ping_backend(self) -> bool:
        """Return True if the Ollama backend is reachable."""
        try:
            resp = requests.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=5)
            ok = resp.status_code == 200
            self.status = "active" if ok else "offline"
            return ok
        except Exception:  # noqa: BLE001
            self.status = "offline"
            return False
