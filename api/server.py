"""FastAPI server wiring the agents, memory and tools to HTTP endpoints."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

# Ensure the console can print Unicode (emoji etc.) on Windows cp1252 terminals.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

import psutil
import requests
from colorama import Fore, Style, init as colorama_init
from dotenv import set_key
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agents.base_agent import get_usage, reset_usage
from agents.clarifier_agent import ClarifierAgent
from agents.coding_agent import CodingAgent
from agents.executor_agent import ExecutorAgent
from agents.planner_agent import PlannerAgent
from agents.router_agent import ChatAgent, RouterAgent
from agents.search_agent import SearchAgent
from agents.teach_agent import TeachAgent
from config.settings import settings
from memory.memory_manager import memory_manager
from tools.file_manager import list_files, _safe_path

# --------------------------------------------------------------------------- #
# App + agents
# --------------------------------------------------------------------------- #
app = FastAPI(title="AI Ecosystem", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

colorama_init(autoreset=True)

router = RouterAgent()
assistant = ChatAgent()
tutor = TeachAgent()
clarifier = ClarifierAgent()
planner = PlannerAgent()
executor = ExecutorAgent()
coder = CodingAgent()
searcher = SearchAgent()

# Ordered registry shown in status/Agent Control. The Router always runs
# (routing can't be turned off); the rest are gated by their `enabled` flag.
AGENTS = {
    "router": router,
    "planner": planner,
    "executor": executor,
    "coding": coder,
    "search": searcher,
}

# Terminal colour per route, for the "🧭 Route: …" log line.
_ROUTE_COLORS = {"chat": Fore.CYAN, "task": Fore.YELLOW, "code": Fore.GREEN, "learn": Fore.MAGENTA}

# Simple in-memory runtime settings (mirrors what the frontend can change).
# Default backend comes from the single source in config/settings.py.
RUNTIME_SETTINGS: Dict[str, Any] = {
    "model": settings.DEFAULT_BACKEND,
    "theme": "dark",
}

# There is no account system yet — only the single local user running the app.
# (Honest placeholder: NOT a fabricated user list.)
USERS: List[Dict[str, Any]] = [
    {"id": 1, "name": "Local User", "email": "local", "last_active": datetime.now().isoformat(), "blocked": False},
]
PROBLEMS_SOLVED_TODAY = {"count": 0, "date": datetime.now().date().isoformat()}

# Real usage totals since the server started (in-memory; resets on restart).
SESSION_STATS = {"api_calls": 0, "input_tokens": 0, "output_tokens": 0}


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class ChatRequest(BaseModel):
    message: str
    model: str = "ollama"  # "ollama" | "groq" | "claude"


class SettingsRequest(BaseModel):
    model: Optional[str] = None
    theme: Optional[str] = None


class UserActionRequest(BaseModel):
    blocked: Optional[bool] = None


class KeysRequest(BaseModel):
    anthropic_api_key: Optional[str] = None
    groq_api: Optional[str] = None
    ollama_model: Optional[str] = None


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/")
def root() -> Dict[str, str]:
    return {"name": "AI Ecosystem API", "status": "running", "model": RUNTIME_SETTINGS["model"]}


@app.post("/chat")
def chat(req: ChatRequest) -> Dict[str, Any]:
    """Route the message first, then run only the agents that route needs.

    chat → one short friendly reply (no pipeline)
    task → Planner + Executor
    code → Coding agent only
    """
    model = req.model or RUNTIME_SETTINGS["model"]
    messages: List[Dict[str, Any]] = []

    # Metrics: reset the per-request usage log and start the clock.
    reset_usage()
    start = time.perf_counter()

    # 0. ROUTER — decide the path before any pipeline agent runs.
    route = router.classify(req.message, model=model)
    # Continuation: if we're mid-lesson, keep teaching for conversational/task-ish
    # follow-ups ("ready", "next", "I don't get it") instead of dropping out.
    if route in ("chat", "task") and _in_teaching_session():
        route = "learn"
    # Continuation: if we just asked a clarifying question, the user's reply
    # answers it — proceed to the task pipeline.
    elif route in ("chat", "task") and _awaiting_task_confirmation():
        route = "task"
    _log_route(route)

    did_work = False  # whether the heavy pipeline actually ran (for the counter)

    if route == "code":
        # Coding agent only — no planner/executor.
        if coder.enabled:
            messages.append(coder.run(req.message, model=model))
            did_work = True
        else:
            messages.append(_disabled_msg("Coding"))

    elif route == "task":
        # CONFIRM STEP — runs BEFORE Planner.
        # If we just asked a clarifying question, fold the user's reply into the
        # original request; otherwise judge the message on its own.
        awaiting = _awaiting_task_confirmation()
        effective = _clarified_task(req.message) if awaiting else req.message

        # Only run the pipeline once the (combined) task is actually actionable.
        # This also stops a brand-new vague request from being mistaken for a
        # reply to a stale clarifying question.
        if clarifier.assess(effective, model=model) == "vague":
            history = memory_manager.get_history()
            messages.append(clarifier.run(req.message, context={"history": history}, model=model))
            # did_work stays False → STOP and wait for the user's reply.
        else:
            _run_task_pipeline(effective, model, messages)
            did_work = True

    elif route == "learn":
        # Conversational tutor — confirms first, teaches in small pieces.
        # No Planner/Executor; it reads history to know where the lesson is.
        history = memory_manager.get_history()
        messages.append(tutor.run(req.message, context={"history": history}, model=model))

    else:  # "chat" (and the safe default) — understanding layer with context.
        # Pass recent conversation history so it follows the thread and notices mood.
        # (History does NOT yet include this message — it's saved below.)
        history = memory_manager.get_history()
        messages.append(assistant.run(req.message, context={"history": history}, model=model))

    # Build per-response metrics from the calls recorded during the pipeline.
    usage_log = get_usage()
    metrics = _build_metrics(usage_log, time.perf_counter() - start, model)
    # Accumulate real session-wide totals (used by the admin dashboard).
    SESSION_STATS["api_calls"] += len(usage_log)
    SESSION_STATS["input_tokens"] += sum(u.get("input_tokens") or 0 for u in usage_log)
    SESSION_STATS["output_tokens"] += sum(u.get("output_tokens") or 0 for u in usage_log)

    # Persist + counters. Only real pipeline work counts as a "problem solved"
    # (a clarifying question does not).
    record = memory_manager.save_conversation(req.message, messages, model=model)
    if did_work:
        _bump_problem_counter()

    return {
        "conversation_id": record["id"],
        "route": route,
        "messages": messages,
        "model": model,
        "metrics": metrics,
    }


@app.get("/history")
def history() -> Dict[str, Any]:
    convos = memory_manager.get_history()
    return {"count": len(convos), "conversations": convos}


@app.get("/history/{conversation_id}")
def get_conversation(conversation_id: str) -> Dict[str, Any]:
    convo = memory_manager.get_conversation(conversation_id)
    return {"conversation": convo}


@app.get("/files")
def files() -> Dict[str, Any]:
    items = list_files()
    return {"count": len(items), "files": items}


@app.get("/files/{filename}/download")
def download_file(filename: str):
    try:
        path = _safe_path(filename)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid filename")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path, filename=path.name)


@app.get("/agents/status")
def agents_status() -> Dict[str, Any]:
    # Health reflects the active backend (Ollama ping, or key-present for APIs).
    backend_up = _backend_online()
    if backend_up:
        # If the backend is reachable, revive only ENABLED agents.
        # A stopped agent stays offline until it is started again.
        for ag in AGENTS.values():
            if ag.enabled and ag.status in ("offline", "idle"):
                ag.status = "active" if ag.last_activity else "idle"
    statuses = [ag.status_dict() for ag in AGENTS.values()]
    return {
        "backend_online": backend_up,
        "agents": statuses,
        "memory_count": memory_manager.count(),
        "files_count": len(list_files()),
    }


@app.post("/settings")
def update_settings(req: SettingsRequest) -> Dict[str, Any]:
    if req.model is not None:
        RUNTIME_SETTINGS["model"] = req.model
    if req.theme is not None:
        RUNTIME_SETTINGS["theme"] = req.theme
    return {"settings": RUNTIME_SETTINGS}


@app.get("/settings")
def get_settings() -> Dict[str, Any]:
    return {"settings": RUNTIME_SETTINGS}


@app.post("/memory/clear")
def clear_memory() -> Dict[str, Any]:
    memory_manager.clear()
    return {"ok": True, "memory_count": 0}


# ----- API keys (persisted to .env) -----
def _mask(value: str) -> str:
    if not value:
        return ""
    tail = value[-4:] if len(value) > 4 else ""
    return "•" * 8 + tail


@app.get("/admin/keys")
def get_keys() -> Dict[str, Any]:
    """Report which keys are configured (masked — never returns full secrets)."""
    return {
        "anthropic_api_key_set": bool(settings.ANTHROPIC_API_KEY),
        "anthropic_api_key_masked": _mask(settings.ANTHROPIC_API_KEY),
        "groq_api_set": bool(settings.GROQ_API_KEY),
        "groq_api_masked": _mask(settings.GROQ_API_KEY),
        "ollama_model": settings.OLLAMA_MODEL,
    }


@app.post("/admin/keys")
def save_keys(req: KeysRequest) -> Dict[str, Any]:
    """Persist API keys to the backend .env and apply them live (no restart)."""
    env_path = str(settings.BASE_DIR / ".env")
    updated: List[str] = []

    if req.anthropic_api_key:
        set_key(env_path, "ANTHROPIC_API_KEY", req.anthropic_api_key)
        os.environ["ANTHROPIC_API_KEY"] = req.anthropic_api_key
        settings.ANTHROPIC_API_KEY = req.anthropic_api_key
        updated.append("anthropic")

    if req.groq_api:
        set_key(env_path, "GROQ_API", req.groq_api)
        os.environ["GROQ_API"] = req.groq_api
        settings.GROQ_API_KEY = req.groq_api
        updated.append("groq")

    if req.ollama_model:
        set_key(env_path, "OLLAMA_MODEL", req.ollama_model)
        os.environ["OLLAMA_MODEL"] = req.ollama_model
        settings.OLLAMA_MODEL = req.ollama_model
        updated.append("ollama_model")

    return {"ok": True, "updated": updated}


# ----- Admin endpoints -----
@app.get("/admin/dashboard")
def dashboard() -> Dict[str, Any]:
    """Real dashboard data pulled from the live system (no fabricated numbers)."""
    _reset_counter_if_new_day()
    backend_up = _backend_online()
    active_agents = sum(1 for ag in AGENTS.values() if ag.status in ("active", "thinking"))
    return {
        # All of these are real, live values:
        "conversations": memory_manager.count(),
        "problems_solved_today": PROBLEMS_SOLVED_TODAY["count"],
        "active_agents": active_agents,
        "total_agents": len(AGENTS),
        "total_api_calls": SESSION_STATS["api_calls"],
        "total_tokens": SESSION_STATS["input_tokens"] + SESSION_STATS["output_tokens"],
        "system_status": "online" if backend_up else "degraded",
        "activity": _activity_series(),          # last 7 days, from memory timestamps
        "recent_activity": _recent_activity(),   # from memory + output/ files
        # Honest: there is no account system yet.
        "users": {"count": 1, "implemented": False, "label": "1 (local)"},
    }


@app.get("/admin/system")
def system_stats() -> Dict[str, Any]:
    """Real host resource usage via psutil + live Ollama reachability."""
    try:
        disk_root = settings.BASE_DIR.anchor or os.sep  # e.g. "C:\\" on Windows
        return {
            "cpu_percent": round(psutil.cpu_percent(interval=0.3), 1),
            "memory_percent": round(psutil.virtual_memory().percent, 1),
            "disk_percent": round(psutil.disk_usage(disk_root).percent, 1),
            "ollama_online": _ollama_reachable(),
        }
    except Exception as exc:  # pragma: no cover
        return {
            "cpu_percent": None,
            "memory_percent": None,
            "disk_percent": None,
            "ollama_online": _ollama_reachable(),
            "error": str(exc),
        }


@app.get("/admin/agents")
def admin_agents() -> Dict[str, Any]:
    return {"agents": [ag.status_dict() for ag in AGENTS.values()]}


@app.post("/admin/agents/{agent_key}/{action}")
def control_agent(agent_key: str, action: str) -> Dict[str, Any]:
    key = agent_key.lower()
    ag = AGENTS.get(key)
    if not ag:
        return {"ok": False, "error": "unknown agent"}
    if key == "router":
        # The Router always runs — routing can't be turned off.
        return {"ok": False, "error": "router cannot be stopped", "agent": ag.status_dict()}
    if action == "start":
        ag.enabled = True
        ag.status = "active"
    elif action == "stop":
        ag.enabled = False
        ag.status = "offline"
    ag.last_activity = datetime.now().isoformat()
    return {"ok": True, "agent": ag.status_dict()}


@app.get("/admin/users")
def admin_users() -> Dict[str, Any]:
    return {"users": USERS}


@app.post("/admin/users/{user_id}")
def admin_user_action(user_id: int, req: UserActionRequest) -> Dict[str, Any]:
    for u in USERS:
        if u["id"] == user_id:
            if req.blocked is not None:
                u["blocked"] = req.blocked
            return {"ok": True, "user": u}
    return {"ok": False, "error": "user not found"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _build_metrics(usage_log: List[Dict[str, Any]], elapsed: float, model: str) -> Dict[str, Any]:
    """Aggregate per-call usage into the response metrics object.

    Token totals are None when no backend in the pipeline reported usage,
    so the frontend can show 'tokens: n/a' instead of a misleading 0.
    """
    api_calls = len(usage_log)
    has_tokens = any(
        u.get("input_tokens") is not None or u.get("output_tokens") is not None for u in usage_log
    )
    in_tok = sum(u.get("input_tokens") or 0 for u in usage_log) if has_tokens else None
    out_tok = sum(u.get("output_tokens") or 0 for u in usage_log) if has_tokens else None
    total = (in_tok + out_tok) if has_tokens else None
    return {
        "total_time_seconds": round(elapsed, 2),
        "api_calls": api_calls,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": total,
        "model": model,
    }


def _run_task_pipeline(message: str, model: str, messages: List[Dict[str, Any]]) -> None:
    """(optional Web Search) → Planner → Executor. Appends bubbles to `messages`."""
    search_context = ""
    if searcher.enabled:
        search_msg = searcher.run(message, model=model)
        messages.append(search_msg)
        search_context = search_msg.get("summary", "")

    steps: List[str] = []
    if planner.enabled:
        plan_msg = planner.run(message, model=model)
        messages.append(plan_msg)
        steps = plan_msg.get("steps", [])
    else:
        messages.append(_disabled_msg("Planner"))

    if executor.enabled:
        messages.append(
            executor.run(message, context={"steps": steps, "search": search_context}, model=model)
        )
    else:
        messages.append(_disabled_msg("Executor"))


def _awaiting_task_confirmation() -> bool:
    """True if our last turn was a clarifying question (user is now answering it)."""
    hist = memory_manager.get_history()
    if not hist:
        return False
    msgs = hist[0].get("messages") or []
    return bool(msgs) and msgs[-1].get("agent") == "Clarifier"


def _clarified_task(answer: str) -> str:
    """Combine the original vague request with the user's clarifying answer."""
    hist = memory_manager.get_history()
    original = hist[0].get("user_message", "") if hist else ""
    if original:
        return f"Original request: {original}\nUser clarification: {answer}"
    return answer


def _in_teaching_session() -> bool:
    """True if the most recent turn was the Tutor — i.e. a lesson is in progress.

    Lets short follow-ups ('ready', 'next', 'not sure') stay in the teaching
    flow instead of being re-routed to chat/task.
    """
    hist = memory_manager.get_history()
    if not hist:
        return False
    msgs = hist[0].get("messages") or []
    return bool(msgs) and msgs[-1].get("agent") == "Tutor"


def _backend_online() -> bool:
    """Is the *active* backend usable?

    Only local Ollama needs a running server. For the hosted APIs we treat
    "an API key is configured" as online, so the dashboard/status don't read
    as offline just because Ollama isn't running.
    """
    backend = RUNTIME_SETTINGS.get("model", "ollama")
    if backend == "groq":
        return bool(settings.GROQ_API_KEY)
    if backend == "claude":
        return bool(settings.ANTHROPIC_API_KEY)
    return planner.ping_backend()  # ollama (default)


def _log_route(route: str) -> None:
    """Print the routing decision in colour; never let logging break a request."""
    color = _ROUTE_COLORS.get(route, Fore.WHITE)
    try:
        print(color + Style.BRIGHT + f"🧭 Route: {route}" + Style.RESET_ALL)
    except Exception:
        print(f"Route: {route}")  # ASCII fallback


def _disabled_msg(agent_name: str) -> Dict[str, Any]:
    """Message shown when a required agent is stopped in Agent Control."""
    return {
        "agent": "System",
        "color": "white",
        "content": f"⛔ The {agent_name} agent is currently stopped. Enable it in Agent Control to use this route.",
        "timestamp": datetime.now().isoformat(),
    }


def _bump_problem_counter() -> None:
    _reset_counter_if_new_day()
    PROBLEMS_SOLVED_TODAY["count"] += 1


def _reset_counter_if_new_day() -> None:
    today = datetime.now().date().isoformat()
    if PROBLEMS_SOLVED_TODAY["date"] != today:
        PROBLEMS_SOLVED_TODAY["date"] = today
        PROBLEMS_SOLVED_TODAY["count"] = 0


def _ollama_reachable() -> bool:
    """Direct, side-effect-free check that the Ollama server is up."""
    try:
        resp = requests.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def _recent_activity(limit: int = 6) -> List[Dict[str, Any]]:
    """Real recent events: latest conversations (memory) + files (output/)."""
    events: List[Dict[str, Any]] = []
    for rec in memory_manager.get_history()[:limit]:
        preview = (rec.get("preview") or rec.get("user_message") or "").strip()
        events.append(
            {
                "type": "chat",
                "text": f"Conversation: {preview[:60]}" if preview else "Conversation",
                "time": rec.get("timestamp", ""),
            }
        )
    for f in list_files():
        events.append(
            {"type": "file", "text": f"File created: {f['name']}", "time": f.get("modified", "")}
        )
    events.sort(key=lambda e: e["time"], reverse=True)
    return events[:limit]


def _activity_series() -> List[Dict[str, Any]]:
    """Count conversations per day for the last 7 days."""
    from collections import Counter
    from datetime import timedelta

    counts: Counter = Counter()
    for record in memory_manager.get_history():
        ts = record.get("timestamp", "")
        if ts:
            counts[ts[:10]] += 1

    series = []
    today = datetime.now().date()
    for i in range(6, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        series.append({"date": day, "count": counts.get(day, 0)})
    return series
