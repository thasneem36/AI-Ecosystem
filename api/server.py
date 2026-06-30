"""FastAPI server wiring the agents, memory and tools to HTTP endpoints."""
from __future__ import annotations

import concurrent.futures
import json
import os
import random
import re
import sys
import threading
import time
import uuid
from collections import deque
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
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from agents.base_agent import get_usage, had_error, last_call_failed, reset_usage
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
from auth.auth_manager import auth_manager
from api.knowledge import router as knowledge_router

# --------------------------------------------------------------------------- #
# App + agents
# --------------------------------------------------------------------------- #
app = FastAPI(title="Koottam", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(knowledge_router)

colorama_init(autoreset=True)

security = HTTPBearer(auto_error=False)

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

# Most clarifying questions we'll ask before proceeding to the Planner with
# whatever we have. Keeps the Clarifier from looping forever.
MAX_CLARIFYING_QUESTIONS = 2

# Simple in-memory runtime settings (mirrors what the frontend can change).
# Default backend comes from the single source in config/settings.py.
RUNTIME_SETTINGS: Dict[str, Any] = {
    "model": settings.DEFAULT_BACKEND,
    "theme": "dark",
}

# Mock prompt pool for the simulation tool (dev only).
# task/code prompts lead so count=2 always exercises the full pipeline.
_SIM_PROMPTS = [
    ("write python code to sort a list of numbers", "code"),
    ("create a step-by-step marketing plan for a coffee shop", "task"),
    ("build a simple todo app in html and css", "code"),
    ("plan a 3-day trip to tokyo on a budget", "task"),
    ("teach me what recursion means", "learn"),
    ("my business revenue is dropping, help me fix it", "task"),
    ("hi there", "chat"),
    ("explain async and await in simple terms", "chat"),
]

# Per-session turn history, keyed by session_id.  Each value is the list of
# turns for that session (oldest first).  Continuation helpers read ONLY the
# session_id currently in flight — no cross-session bleed, no cross-request bleed.
# Entries stay in memory for the lifetime of the server process; /memory/clear
# wipes the current user's sessions only.
CURRENT_CONVO: Dict[str, List[Dict[str, Any]]] = {}

# Maps account_id → set of session_ids owned by that account.
# Used so /memory/clear only removes the requesting user's in-memory sessions.
USER_SESSIONS: Dict[int, set] = {}

# Simulation state — keyed by sim_id, capped at the last 20 runs (resets on restart)
_SIMULATIONS: Dict[str, Any] = {}
_SIM_STORE_LOCK = threading.Lock()

# _CONVO_LOCK  — CURRENT_CONVO and USER_SESSIONS dict mutations.
# Counter updates (problems_solved, api_calls, tokens) go through auth_manager's
# atomic SQL SET x = x + N — no in-process lock needed for those.
_CONVO_LOCK = threading.Lock()

# Sliding-window rate limiter — one timestamp deque per account_id.
# _RATE_LOCK serialises the check-and-append so two simultaneous requests
# from the same account can't both slip past a full bucket.
_RATE_BUCKETS: Dict[int, deque] = {}
_RATE_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class ChatRequest(BaseModel):
    message: str
    # None → fall back to the configured default backend (RUNTIME_SETTINGS["model"],
    # seeded from settings.DEFAULT_BACKEND). Do NOT default to a concrete backend
    # here, or the fallback in chat() becomes dead code.
    model: Optional[str] = None  # "ollama" | "groq" | "claude" | None
    # None → generate a new session_id; client should echo it back each turn
    # so all messages in the same conversation share one session_id.
    session_id: Optional[str] = None
    # True when the user explicitly clicked "+ New chat" — skip the topic-bridge
    # check on the first message so we never ask "old or new?" right after they
    # already declared this is new.
    new_chat: bool = False


class SettingsRequest(BaseModel):
    model: Optional[str] = None
    theme: Optional[str] = None


class UserActionRequest(BaseModel):
    blocked: Optional[bool] = None


class KeysRequest(BaseModel):
    anthropic_api_key: Optional[str] = None
    groq_api: Optional[str] = None
    ollama_model: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateAccountRequest(BaseModel):
    username: str
    password: str
    role: str = "user"
    token_limit: int = 5000
    reset_hours: float = 3.0


class UpdateAccountRequest(BaseModel):
    is_active: Optional[int] = None
    token_limit: Optional[int] = None
    reset_hours: Optional[float] = None
    role: Optional[str] = None
    password: Optional[str] = None


class AddApiRequest(BaseModel):
    name: str
    env_var: Optional[str] = None  # auto-derived from key prefix when omitted
    model: str = ""
    key: str = ""


class DetectKeyRequest(BaseModel):
    key: str


class UpdateModelRequest(BaseModel):
    model: str


class SimulateRequest(BaseModel):
    count: int = 5   # number of concurrent requests (1-50)
    mock: bool = True  # True = no real API calls; False = real pipeline


# --------------------------------------------------------------------------- #
# Auth dependencies
# --------------------------------------------------------------------------- #
def get_current_account(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[Dict[str, Any]]:
    """Bootstrap mode: if no accounts exist, allow all requests (returns None).
    Once an admin account is created, every request must carry a valid token.
    """
    if not auth_manager.has_accounts():
        return None
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    account = auth_manager.verify_session(credentials.credentials)
    if not account:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return account


def require_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[Dict[str, Any]]:
    """Like get_current_account, but also enforces the admin role."""
    if not auth_manager.has_accounts():
        return None  # Bootstrap mode — admin panel is open until first account is created
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    account = auth_manager.verify_session(credentials.credentials)
    if not account:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    if account.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return account


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/ui", response_class=FileResponse)
def ui() -> FileResponse:
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html"))


@app.get("/knowledge-ui", response_class=FileResponse)
def knowledge_ui() -> FileResponse:
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "frontend", "knowledge.html"))


@app.get("/")
def root() -> Dict[str, str]:
    return {"name": "Koottam API", "status": "running", "model": RUNTIME_SETTINGS["model"]}


# --------------------------------------------------------------------------- #
# Auth endpoints
# --------------------------------------------------------------------------- #
@app.get("/auth/status")
def auth_status() -> Dict[str, Any]:
    """Unauthenticated. Tells the frontend whether accounts exist yet."""
    return {"needs_setup": not auth_manager.has_accounts()}


@app.post("/auth/login")
def auth_login(req: LoginRequest) -> Dict[str, Any]:
    session = auth_manager.login(req.username, req.password)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return session


@app.post("/auth/logout")
def auth_logout(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    if credentials:
        auth_manager.logout(credentials.credentials)
    return {"ok": True}


@app.get("/auth/me")
def auth_me(account: Optional[Dict[str, Any]] = Depends(get_current_account)) -> Dict[str, Any]:
    if not account:
        return {"authenticated": False, "needs_setup": True}
    stats = auth_manager.get_usage_stats(account["id"])
    return {
        "authenticated": True,
        "username": account["username"],
        "role": account["role"],
        "token_limit": account["token_limit"],
        "reset_hours": account["reset_hours"],
        **stats,
    }


@app.post("/chat")
def chat(
    req: ChatRequest,
    account: Optional[Dict[str, Any]] = Depends(get_current_account),
) -> Dict[str, Any]:
    """Route the message first, then run only the agents that route needs.

    chat → one short friendly reply (no pipeline)
    task → Planner + Executor
    code → Coding agent only
    """
    model = req.model or RUNTIME_SETTINGS["model"]
    session_id: str = req.session_id or str(uuid.uuid4())
    messages: List[Dict[str, Any]] = []

    # Metrics: reset the per-request usage log and start the clock.
    # _usage_log is a ContextVar — already per-request isolated, no lock needed.
    reset_usage()
    start = time.perf_counter()

    # ── Rate limit ──────────────────────────────────────────────────────────
    # Checked BEFORE acquiring _acct_lock so rate-limited requests fail fast
    # without blocking the per-account pipeline slot.
    # This is separate from the token budget: budget = total usage over hours;
    # rate limit = burst speed (requests per window).
    if account:
        _rl_exempt = settings.RATE_LIMIT_ADMIN_EXEMPT and account.get("role") == "admin"
        if not _rl_exempt and not _check_rate_limit(account["id"]):
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Too many requests — please wait a moment. "
                    f"(Limit: {settings.RATE_LIMIT_REQUESTS} requests "
                    f"per {settings.RATE_LIMIT_WINDOW}s)"
                ),
            )
    # ────────────────────────────────────────────────────────────────────────

    # Restore session context from the DB when the user reopens a past
    # conversation from the sidebar.  CURRENT_CONVO only has the turns seen
    # since the server started, so a resumed session would otherwise lose
    # clarifier/tutor continuation — we rebuild it transparently here.
    # Double-check pattern: read cheaply outside the lock; recheck under the
    # lock before writing so two concurrent restores for the same session don't
    # both write (the second would overwrite the first).
    if req.session_id and not req.new_chat and not CURRENT_CONVO.get(session_id):
        past_turns = memory_manager.get_history(session_id=session_id)
        if past_turns:
            with _CONVO_LOCK:
                if not CURRENT_CONVO.get(session_id):  # recheck inside lock
                    CURRENT_CONVO[session_id] = [
                        {"user_message": t.get("user_message", ""), "messages": t.get("messages", [])}
                        for t in reversed(past_turns)
                    ]

    # Per-account usage limit — hold the lock only for the check itself, then
    # release it before any slow Groq call.  Holding it for the entire pipeline
    # (13-120 s) causes same-account concurrent requests to serialize for that
    # full duration, making the second request appear "stuck forever" when Groq
    # is slow.  record_usage() uses atomic SQL (SET x = x + N), so it is safe
    # to call without the lock; the only race is a tiny potential overage when
    # two requests from the same account both pass the check within milliseconds
    # of each other — acceptable for this use-case.
    if account:
        _acct_lock = auth_manager.get_account_lock(account["id"])
        _acct_lock.acquire()
        try:
            limit = auth_manager.check_limit(account["id"])
        finally:
            _acct_lock.release()   # release BEFORE any Groq call
        if not limit["allowed"]:
            secs = int(limit["resets_in_seconds"])
            hrs, mins = secs // 3600, (secs % 3600) // 60
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Usage limit reached ({limit['tokens_used']}/{limit['token_limit']} tokens). "
                    f"Resets in {hrs}h {mins}m."
                ),
            )

    # Everything below runs without any per-account lock; concurrent same-account
    # requests run their pipelines in parallel rather than queueing.
    try:

        # ── TOPIC BRIDGE ────────────────────────────────────────────────────────
        # Phase A: user is replying to our "old topic or new?" question.
        bridge_pending = _awaiting_topic_bridge(session_id)
        if bridge_pending:
            # Pop the bridge record so it doesn't confuse later helpers.
            with _CONVO_LOCK:
                CURRENT_CONVO[session_id] = [
                    r for r in CURRENT_CONVO.get(session_id, [])
                    if r.get("type") != "topic_bridge"
                ]
            choice = _interpret_topic_reply(req.message)
            if choice == "new":
                # User wants a fresh start — confirm and return without touching old context.
                msg: Dict[str, Any] = {
                    "agent": "Assistant",
                    "content": "Got it — starting fresh. What would you like to work on?",
                    "timestamp": datetime.now().isoformat(),
                }
                record = memory_manager.save_conversation(
                    req.message, [msg], model=model, session_id=session_id, route="chat",
                    user_id=account["id"] if account else None,
                )
                with _CONVO_LOCK:
                    CURRENT_CONVO.setdefault(session_id, []).append(
                        {"user_message": req.message, "messages": [msg]}
                    )
                usage_log = get_usage()
                return {
                    "conversation_id": record["id"],
                    "session_id": session_id,
                    "route": "chat",
                    "messages": [msg],
                    "model": model,
                    "metrics": _build_metrics(usage_log, time.perf_counter() - start, model),
                }
            else:
                # User wants to continue the old session — load its context, then
                # re-process the ORIGINAL message (the one that triggered the bridge)
                # through the normal pipeline so they get an actual answer.
                _load_session_context(bridge_pending["matched_session_id"], session_id)
                req = ChatRequest(
                    message=bridge_pending["user_message"],
                    model=req.model,
                    session_id=session_id,
                )
                # Fall through to normal routing below with context now loaded.

        # Phase B: first message of a brand-new session — check for related past sessions.
        # Skip entirely when the user explicitly clicked "+ New chat": they already
        # declared this is new, so asking "old or new?" would be redundant.
        elif not CURRENT_CONVO.get(session_id) and not req.new_chat:
            related = _find_related_session(
                req.message, session_id,
                user_id=account["id"] if account else None,
            )
            if related:
                bridge = _topic_bridge_msg(related)
                with _CONVO_LOCK:
                    CURRENT_CONVO.setdefault(session_id, []).append({
                        "type": "topic_bridge",
                        "user_message": req.message,       # saved so we can re-process it
                        "messages": [bridge],
                        "matched_session_id": related["session_id"],
                    })
                usage_log = get_usage()
                return {
                    "conversation_id": None,
                    "session_id": session_id,
                    "route": "topic_bridge",
                    "messages": [bridge],
                    "model": model,
                    "metrics": _build_metrics(usage_log, time.perf_counter() - start, model),
                }
        # ────────────────────────────────────────────────────────────────────────

        # 0. ROUTER — decide the path before any pipeline agent runs.
        route = router.classify(req.message, model=model)
        # Continuation: if we're mid-lesson, keep teaching for conversational/task-ish
        # follow-ups ("ready", "next", "I don't get it") instead of dropping out.
        if route in ("chat", "task") and _in_teaching_session(session_id):
            route = "learn"
        # Continuation: if we just asked a clarifying question, the user's reply
        # answers it — proceed to the task pipeline.
        elif route in ("chat", "task") and _awaiting_task_confirmation(session_id):
            route = "task"
        _log_route(route)

        did_work = False  # whether the heavy pipeline actually ran (for the counter)

        if route == "code":
            # Coding agent — same clarifier gate as TASK so vague code requests get
            # one question before we write the wrong thing. CHAT route never clarifies.
            if coder.enabled:
                awaiting = _awaiting_task_confirmation(session_id)
                asked = _clarifying_questions_asked(session_id)
                effective = _clarified_task(req.message, session_id) if awaiting else req.message
                history = _current_history(session_id)

                if asked >= MAX_CLARIFYING_QUESTIONS:
                    proceed = True
                elif awaiting:
                    proceed = clarifier.has_enough(effective, model=model)
                else:
                    proceed = clarifier.assess(effective, model=model, history=history) != "vague"

                if proceed:
                    messages.append(coder.run(effective, model=model))
                    did_work = True
                else:
                    messages.append(clarifier.run(req.message, context={"history": history}, model=model))
            else:
                messages.append(_disabled_msg("Coding"))

        elif route == "task":
            # CONFIRM STEP — runs BEFORE Planner, but must NOT loop forever.
            # If we just asked a clarifying question, fold the user's reply into the
            # original request; otherwise judge the message on its own.
            awaiting = _awaiting_task_confirmation(session_id)
            asked = _clarifying_questions_asked(session_id)  # how many we've already asked in a row
            effective = _clarified_task(req.message, session_id) if awaiting else req.message
            history = _current_history(session_id)  # used by both assess() and clarifier.run()

            # Decide: ask one (more) clarifying question, or proceed to the pipeline.
            if asked >= MAX_CLARIFYING_QUESTIONS:
                # Hard cap reached — act on whatever we have, don't keep asking.
                proceed = True
            elif awaiting:
                # The user just answered a question. Lean strongly toward acting:
                # only ask again if the request is STILL genuinely unusable.
                proceed = clarifier.has_enough(effective, model=model)
            else:
                # Brand-new request — only stop to clarify if it's genuinely costly to
                # guess wrong. Pass history so the model recognises continuation patterns.
                proceed = clarifier.assess(effective, model=model, history=history) != "vague"

            if proceed:
                _run_task_pipeline(effective, model, messages)
                did_work = True
            else:
                messages.append(clarifier.run(req.message, context={"history": history}, model=model))
                # did_work stays False → STOP and wait for the user's reply.

        elif route == "learn":
            # Conversational tutor — confirms first, teaches in small pieces.
            # No Planner/Executor; it reads history to know where the lesson is.
            history = _current_history(session_id)
            messages.append(tutor.run(req.message, context={"history": history}, model=model))

        else:  # "chat" (and the safe default) — understanding layer with context.
            # Pass the CURRENT conversation's history so it follows the thread and
            # notices mood — never the whole memory file (no cross-conversation bleed).
            history = _current_history(session_id)
            messages.append(assistant.run(req.message, context={"history": history}, model=model))

        # A model call failed somewhere in this request (backend unreachable, API
        # error, …). Don't let the failure text flow through as a real answer:
        # show a clean error, don't persist it as a solution, don't count it.
        failed = had_error()
        if failed:
            did_work = False
            messages = [_error_msg()]

        # Build per-response metrics from the calls recorded during the pipeline.
        usage_log = get_usage()
        metrics = _build_metrics(usage_log, time.perf_counter() - start, model)
        in_tok = sum(u.get("input_tokens") or 0 for u in usage_log)
        out_tok = sum(u.get("output_tokens") or 0 for u in usage_log)

        # Atomic DB increments — SET x = x + N, no in-memory read-modify-write.
        auth_manager.record_session_stats(
            api_calls=len(usage_log),
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

        # Per-account usage tracking — recorded while still holding _acct_lock so
        # the check_limit → record_usage window is atomic per account.
        if account and not failed:
            auth_manager.record_usage(
                account["id"],
                tokens=in_tok + out_tok,
                api_calls=len(usage_log),
                context_bytes=len(req.message.encode("utf-8")),
            )

        # Persist + counters. A failed call is NOT saved as a conversation and never
        # counts as a "problem solved" (neither does a clarifying question).
        if failed:
            return {
                "conversation_id": None,
                "session_id": session_id,
                "route": route,
                "messages": messages,
                "model": model,
                "metrics": metrics,
                "did_work": False,
            }

        record = memory_manager.save_conversation(
            req.message, messages, model=model, session_id=session_id, route=route,
            user_id=account["id"] if account else None,
        )
        # Track this turn in the in-process session so continuation helpers use
        # only this session's turns — never another session's history.
        with _CONVO_LOCK:
            CURRENT_CONVO.setdefault(session_id, []).append(
                {"user_message": req.message, "messages": messages}
            )
            # Record the session→account mapping for scoped /memory/clear.
            if account:
                USER_SESSIONS.setdefault(account["id"], set()).add(session_id)
        if did_work:
            # Atomic DB SET x = x + 1 — safe even when 20 s requests overlap.
            auth_manager.bump_problems_solved()

        return {
            "conversation_id": record["id"],
            "session_id": session_id,
            "route": route,
            "messages": messages,
            "model": model,
            "metrics": metrics,
            "did_work": did_work,
        }
    finally:
        pass  # no per-account lock held here; the lock was released right after check_limit()


@app.get("/conversations")
def list_conversations(
    account: Optional[Dict[str, Any]] = Depends(get_current_account),
) -> Dict[str, Any]:
    """Sidebar history: one entry per session, scoped to the current user."""
    user_id = None if (not account or account.get("role") == "admin") else account["id"]
    sessions = memory_manager.get_sessions(user_id=user_id)
    return {"sessions": sessions}


@app.get("/history")
def history(
    session_id: Optional[str] = None,
    account: Optional[Dict[str, Any]] = Depends(get_current_account),
) -> Dict[str, Any]:
    # Admins see all conversations; regular users see only their own.
    user_id = None if (not account or account.get("role") == "admin") else account["id"]
    convos = memory_manager.get_history(session_id=session_id, user_id=user_id)
    return {"count": len(convos), "conversations": convos}


@app.get("/history/{conversation_id}")
def get_conversation(
    conversation_id: str,
    account: Optional[Dict[str, Any]] = Depends(get_current_account),
) -> Dict[str, Any]:
    convo = memory_manager.get_conversation(conversation_id)
    # Non-admins may only read their own turns.
    if convo and account and account.get("role") != "admin":
        owner = convo.get("user_id")
        if owner is not None and owner != account["id"]:
            raise HTTPException(status_code=403, detail="Access denied")
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
def clear_memory(
    account: Optional[Dict[str, Any]] = Depends(get_current_account),
) -> Dict[str, Any]:
    if account:
        uid = account["id"]
        memory_manager.clear(user_id=uid)
        with _CONVO_LOCK:
            for sid in USER_SESSIONS.pop(uid, set()):
                CURRENT_CONVO.pop(sid, None)
    else:
        memory_manager.clear()
        with _CONVO_LOCK:
            CURRENT_CONVO.clear()
            USER_SESSIONS.clear()
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
    backend_up = _backend_online()
    active_agents = sum(1 for ag in AGENTS.values() if ag.status in ("active", "thinking"))
    stats = auth_manager.get_server_stats()
    return {
        # All of these are real, live values:
        "conversations": memory_manager.count(),
        "problems_solved_today": stats["problems_count"],
        "active_agents": active_agents,
        "total_agents": len(AGENTS),
        "total_api_calls": stats["api_calls"],
        "total_tokens": stats["input_tokens"] + stats["output_tokens"],
        "system_status": "online" if backend_up else "degraded",
        "activity": _activity_series(),          # last 7 days, from memory timestamps
        "recent_activity": _recent_activity(),   # from memory + output/ files
        "users": {"count": len(auth_manager.get_accounts()), "implemented": True},
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
def admin_users(_account: Optional[Dict[str, Any]] = Depends(require_admin)) -> Dict[str, Any]:
    return {"users": auth_manager.get_accounts()}


@app.get("/admin/accounts")
def admin_accounts(_account: Optional[Dict[str, Any]] = Depends(require_admin)) -> Dict[str, Any]:
    return {"accounts": auth_manager.get_accounts()}


@app.post("/admin/accounts")
def admin_create_account(
    req: CreateAccountRequest,
    _account: Optional[Dict[str, Any]] = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        acc = auth_manager.create_account(
            req.username, req.password, req.role, req.token_limit, req.reset_hours
        )
        return {"ok": True, "account": acc}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.put("/admin/accounts/{account_id}")
def admin_update_account(
    account_id: int,
    req: UpdateAccountRequest,
    _account: Optional[Dict[str, Any]] = Depends(require_admin),
) -> Dict[str, Any]:
    ok = auth_manager.update_account(
        account_id,
        is_active=req.is_active,
        token_limit=req.token_limit,
        reset_hours=req.reset_hours,
        role=req.role,
        password=req.password,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True}


@app.post("/admin/accounts/{account_id}/reset-usage")
def admin_reset_usage(
    account_id: int,
    _account: Optional[Dict[str, Any]] = Depends(require_admin),
) -> Dict[str, Any]:
    auth_manager.reset_usage(account_id)
    return {"ok": True}


@app.post("/admin/accounts/{account_id}/login-as")
def admin_login_as(
    account_id: int,
    _account: Optional[Dict[str, Any]] = Depends(require_admin),
) -> Dict[str, Any]:
    result = auth_manager.login_as(account_id)
    if not result:
        raise HTTPException(status_code=404, detail="Account not found or inactive")
    return result


@app.delete("/admin/accounts/{account_id}")
def admin_delete_account(
    account_id: int,
    _account: Optional[Dict[str, Any]] = Depends(require_admin),
) -> Dict[str, Any]:
    if account_id == _account["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    if not auth_manager.delete_account(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True}


# --------------------------------------------------------------------------- #
# API provider registry endpoints
# --------------------------------------------------------------------------- #

def _key_to_env_var(key: str, name: str) -> str:
    """Derive the env-var name from the key prefix or provider name."""
    k = key.strip()
    if k.startswith("gsk_"):
        return "GROQ_API"
    if k.startswith("sk-ant"):
        return "ANTHROPIC_API_KEY"
    if k.startswith("sk-"):
        return "OPENAI_API_KEY"
    return f"{name.upper().replace(' ', '_')}_API_KEY"


def _sync_env_providers() -> None:
    """Lazily import API keys already in .env into the provider registry."""
    checks = [
        ("groq",      "GROQ_API",         settings.GROQ_API_KEY,      settings.GROQ_MODEL),
        ("anthropic", "ANTHROPIC_API_KEY", settings.ANTHROPIC_API_KEY, settings.CLAUDE_MODEL),
    ]
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        checks.append(("openai", "OPENAI_API_KEY", openai_key, "gpt-4o-mini"))
    for name, env_var, key, model in checks:
        if key:
            auth_manager.insert_provider_if_absent(name, env_var, model or "", key)


@app.get("/admin/apis")
def admin_list_apis(
    _account: Optional[Dict[str, Any]] = Depends(require_admin),
) -> List[Dict[str, Any]]:
    _sync_env_providers()
    return auth_manager.get_providers()


@app.post("/admin/apis")
def admin_add_api(
    req: AddApiRequest,
    _account: Optional[Dict[str, Any]] = Depends(require_admin),
) -> Dict[str, Any]:
    env_var = req.env_var or _key_to_env_var(req.key, req.name)
    if req.key:
        env_path = settings.BASE_DIR / ".env"
        set_key(str(env_path), env_var, req.key)
        os.environ[env_var] = req.key
    provider = auth_manager.upsert_provider(req.name, env_var, req.model, req.key)
    return provider


@app.post("/admin/apis/detect-key")
def admin_detect_key(
    req: DetectKeyRequest,
    _account: Optional[Dict[str, Any]] = Depends(require_admin),
) -> Dict[str, Any]:
    """Detect provider from key prefix and fetch its available models."""
    key = req.key.strip()
    if key.startswith("gsk_"):
        provider, env_var = "groq", "GROQ_API"
    elif key.startswith("sk-ant"):
        provider, env_var = "anthropic", "ANTHROPIC_API_KEY"
    elif key.startswith("sk-"):
        provider, env_var = "openai", "OPENAI_API_KEY"
    else:
        return {"provider": None, "env_var": None, "models": [], "detected": False,
                "error": "Unknown key format — please choose provider manually."}
    try:
        if provider == "groq":
            r = requests.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"}, timeout=8,
            )
            if r.status_code == 200:
                models = sorted(
                    m["id"] for m in r.json().get("data", [])
                    if m.get("id") and "whisper" not in m["id"]
                )
                return {"provider": provider, "env_var": env_var, "models": models, "detected": True}
        elif provider == "anthropic":
            r = requests.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=8,
            )
            if r.status_code == 200:
                models = sorted(
                    (m["id"] for m in r.json().get("data", []) if m.get("id")),
                    reverse=True,
                )
                return {"provider": provider, "env_var": env_var, "models": models, "detected": True}
        elif provider == "openai":
            r = requests.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"}, timeout=8,
            )
            if r.status_code == 200:
                models = sorted(
                    (m["id"] for m in r.json().get("data", []) if "gpt" in m.get("id", "")),
                    reverse=True,
                )
                return {"provider": provider, "env_var": env_var, "models": models, "detected": True}
        return {"provider": provider, "env_var": env_var, "models": [], "detected": True,
                "error": f"Key rejected by {provider} (HTTP {r.status_code}) — check it's valid."}
    except Exception as exc:
        return {"provider": provider, "env_var": env_var, "models": [], "detected": True,
                "error": f"Could not reach {provider}: {exc}"}


@app.patch("/admin/apis/{provider_id}/model")
def admin_update_api_model(
    provider_id: int,
    req: UpdateModelRequest,
    _account: Optional[Dict[str, Any]] = Depends(require_admin),
) -> Dict[str, Any]:
    auth_manager.update_provider_model(provider_id, req.model)
    return {"ok": True}


@app.delete("/admin/apis/{provider_id}")
def admin_delete_api(
    provider_id: int,
    _account: Optional[Dict[str, Any]] = Depends(require_admin),
) -> Dict[str, Any]:
    if not auth_manager.delete_provider(provider_id):
        raise HTTPException(status_code=404, detail="Provider not found")
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Simulation endpoints (dev/admin only)
# --------------------------------------------------------------------------- #

@app.post("/admin/simulate")
def start_simulation(
    req: SimulateRequest,
    _account: Optional[Dict[str, Any]] = Depends(require_admin),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """Start a concurrent load simulation. Returns a sim_id; poll /status for progress."""
    count = max(1, min(req.count, 50))
    model = RUNTIME_SETTINGS["model"]
    base_url = f"http://127.0.0.1:{settings.PORT}"
    token = credentials.credentials if credentials else None

    # Pre-snapshot from DB — single source of truth, no in-memory race risk.
    pre_stats = auth_manager.get_server_stats()
    pre = {
        "problems": pre_stats["problems_count"],
        "api_calls": pre_stats["api_calls"],
        "tokens": pre_stats["input_tokens"] + pre_stats["output_tokens"],
    }

    sim_id = str(uuid.uuid4())

    with _SIM_STORE_LOCK:
        # Keep only the last 20 simulation records
        if len(_SIMULATIONS) >= 20:
            oldest = next(iter(_SIMULATIONS))
            del _SIMULATIONS[oldest]
        _SIMULATIONS[sim_id] = {
            "status": "running",
            "count": count,
            "mock": req.mock,
            "events": [],
            "pre": pre,
            "start_time": time.time(),
        }

    def _run():
        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
            futs = {
                pool.submit(
                    _run_one_simulation_task, i, req.mock, model, _account, base_url, token
                ): i
                for i in range(count)
            }
            for f in concurrent.futures.as_completed(futs):
                result = f.result()
                with _SIM_STORE_LOCK:
                    _SIMULATIONS[sim_id]["events"].append({"type": "task_done", "result": result})

        # Post-snapshot — DB reflects all atomic increments from completed tasks.
        post_stats = auth_manager.get_server_stats()
        post_problems = post_stats["problems_count"]
        post_api_calls = post_stats["api_calls"]
        post_tokens = post_stats["input_tokens"] + post_stats["output_tokens"]

        with _SIM_STORE_LOCK:
            sim = _SIMULATIONS[sim_id]
            tasks = [e["result"] for e in sim["events"] if e.get("type") == "task_done"]
            done_t = [r for r in tasks if r["status"] == "done"]
            failed_t = [r for r in tasks if r["status"] != "done"]
            times = [r["elapsed_s"] for r in done_t]
            tok_vals = [r["tokens"] for r in done_t if r.get("tokens") is not None]
            problems_delta = post_problems - sim["pre"]["problems"]
            # expected_delta: only tasks that actually ran the full pipeline
            # (did_work=True) bump the counter. Chat-route tasks and failed
            # pipelines set did_work=False and are excluded here.
            expected_delta = sum(1 for r in done_t if r.get("did_work"))
            sim["events"].append({
                "type": "done",
                "summary": {
                    "mode": "MOCK" if req.mock else "REAL",
                    "total": count,
                    "succeeded": len(done_t),
                    "failed": len(failed_t),
                    "avg_time_s": round(sum(times) / len(times), 3) if times else 0,
                    "slowest_s": round(max(times), 3) if times else 0,
                    "total_elapsed_s": round(time.time() - sim["start_time"], 3),
                    "total_tokens": sum(tok_vals),
                    "total_api_calls": len(done_t),
                },
                "concurrency_check": {
                    "ok": problems_delta == expected_delta,
                    "problems_before": sim["pre"]["problems"],
                    "problems_after": post_problems,
                    "expected_delta": expected_delta,
                    "actual_delta": problems_delta,
                    "api_calls_delta": post_api_calls - sim["pre"]["api_calls"],
                    "tokens_delta": post_tokens - sim["pre"]["tokens"],
                },
                "errors": [{"task_id": r["task_id"], "error": r["error"]} for r in failed_t],
            })
            sim["status"] = "done"

    threading.Thread(target=_run, daemon=True).start()
    return {"sim_id": sim_id, "count": count, "mock": req.mock}


@app.get("/admin/simulate/{sim_id}/status")
def simulation_status(
    sim_id: str,
    _account: Optional[Dict[str, Any]] = Depends(require_admin),
) -> Dict[str, Any]:
    """Poll this endpoint to track concurrent simulation progress."""
    with _SIM_STORE_LOCK:
        sim = _SIMULATIONS.get(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    events = sim.get("events", [])
    task_results = [e["result"] for e in events if e.get("type") == "task_done"]
    done_event = next((e for e in events if e.get("type") == "done"), None)
    return {
        "status": sim["status"],
        "count": sim["count"],
        "mock": sim["mock"],
        "completed": len(task_results),
        "tasks": task_results,
        "done_event": done_event,
    }


def _classify_api_error(resp: "requests.Response") -> str:
    code = resp.status_code
    if code in (401, 403):
        return "bad key"
    if code == 429:
        return "rate limit"
    if code == 404:
        return "model not found"
    try:
        body = resp.json()
        msg = (body.get("error") or {}).get("message") or str(body)
    except Exception:
        msg = resp.text[:200]
    return f"HTTP {code}: {msg}"


@app.post("/admin/apis/{provider_id}/test")
def admin_test_api(
    provider_id: int,
    _account: Optional[Dict[str, Any]] = Depends(require_admin),
) -> Dict[str, Any]:
    provider = auth_manager.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    name = provider["name"].lower()
    env_var = provider["env_var"]
    model = provider["model"]
    api_key = os.environ.get(env_var, "")

    try:
        if "ollama" in name:
            base = settings.OLLAMA_BASE_URL.rstrip("/")
            resp = requests.get(f"{base}/api/tags", timeout=5)
            if resp.status_code == 200:
                auth_manager.update_provider_status(provider_id, "working")
                return {"ok": True, "message": "Working"}
            err = _classify_api_error(resp)
            auth_manager.update_provider_status(provider_id, "failed", err)
            return {"ok": False, "message": f"Failed: {err}"}

        elif "anthropic" in name or env_var in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"):
            if not api_key:
                auth_manager.update_provider_status(provider_id, "failed", "no key set")
                return {"ok": False, "message": "Failed: no key set"}
            used_model = model or "claude-haiku-4-5-20251001"
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={"model": used_model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                timeout=10,
            )
            if resp.status_code == 200:
                auth_manager.update_provider_status(provider_id, "working")
                return {"ok": True, "message": "Working"}
            err = _classify_api_error(resp)
            auth_manager.update_provider_status(provider_id, "failed", err)
            return {"ok": False, "message": f"Failed: {err}"}

        else:
            # Default: OpenAI-compatible (Groq and others)
            if not api_key:
                auth_manager.update_provider_status(provider_id, "failed", "no key set")
                return {"ok": False, "message": "Failed: no key set"}
            base_url = os.environ.get(f"{env_var.rstrip('_KEY').rstrip('_API')}_BASE_URL", "https://api.groq.com/openai/v1")
            used_model = model or "llama-3.1-8b-instant"
            resp = requests.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": used_model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                timeout=10,
            )
            if resp.status_code == 200:
                auth_manager.update_provider_status(provider_id, "working")
                return {"ok": True, "message": "Working"}
            err = _classify_api_error(resp)
            auth_manager.update_provider_status(provider_id, "failed", err)
            return {"ok": False, "message": f"Failed: {err}"}

    except requests.Timeout:
        auth_manager.update_provider_status(provider_id, "failed", "unreachable")
        return {"ok": False, "message": "Failed: unreachable (timeout)"}
    except requests.ConnectionError:
        auth_manager.update_provider_status(provider_id, "failed", "unreachable")
        return {"ok": False, "message": "Failed: unreachable (connection error)"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _check_rate_limit(account_id: int) -> bool:
    """Sliding-window rate check. Returns True if the request is allowed.

    Reads the window size and cap from settings so they can be tuned via .env
    without a code change. The deque holds one monotonic timestamp per admitted
    request; entries older than the window are pruned on each call.
    """
    max_req = settings.RATE_LIMIT_REQUESTS
    window = settings.RATE_LIMIT_WINDOW
    now = time.monotonic()
    cutoff = now - window
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS.setdefault(account_id, deque())
        # Evict timestamps outside the current window (oldest are at the left).
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= max_req:
            return False
        bucket.append(now)
        return True


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
        # If the planner's model call failed, don't run the executor on the
        # error text — chat() will replace everything with a clean error.
        if last_call_failed():
            return
        steps = plan_msg.get("steps", [])
    else:
        messages.append(_disabled_msg("Planner"))

    if executor.enabled:
        messages.append(
            executor.run(message, context={"steps": steps, "search": search_context}, model=model)
        )
    else:
        messages.append(_disabled_msg("Executor"))


def _current_history(session_id: str) -> List[Dict[str, Any]]:
    """Turns for *this session*, NEWEST first.

    Scoped strictly to session_id so no two sessions can see each other's
    history — even within the same server process.
    """
    return list(reversed(CURRENT_CONVO.get(session_id, [])))


def _awaiting_task_confirmation(session_id: str) -> bool:
    """True if the last turn in this session was a clarifying question."""
    hist = _current_history(session_id)
    if not hist:
        return False
    msgs = hist[0].get("messages") or []
    return bool(msgs) and msgs[-1].get("agent") == "Clarifier"


def _clarifying_streak(session_id: str) -> List[Dict[str, Any]]:
    """The run of most-recent turns in this session that ended in a clarifying question.

    Returned newest-first; used to count back-to-back questions and reconstruct
    the full request thread (original + each answer) for the Planner.
    """
    streak: List[Dict[str, Any]] = []
    for rec in _current_history(session_id):
        msgs = rec.get("messages") or []
        if msgs and msgs[-1].get("agent") == "Clarifier":
            streak.append(rec)
        else:
            break
    return streak


def _clarifying_questions_asked(session_id: str) -> int:
    """How many clarifying questions we've asked back-to-back in this session."""
    return len(_clarifying_streak(session_id))


def _clarified_task(answer: str, session_id: str) -> str:
    """Combine the original vague request + intermediate answers + this answer.

    Walks the clarifying streak so every detail the user gave across several
    turns is available to the Planner in one coherent string.
    """
    streak = _clarifying_streak(session_id)  # newest-first
    if not streak:
        return answer
    original = streak[-1].get("user_message", "")
    prior_answers = [r.get("user_message", "") for r in streak[:-1]][::-1]
    parts = [f"Original request: {original}"] if original else []
    parts += [f"User added: {a}" for a in prior_answers if a]
    parts.append(f"User clarification: {answer}")
    return "\n".join(parts)


def _in_teaching_session(session_id: str) -> bool:
    """True if the most recent turn in this session was handled by the Tutor."""
    hist = _current_history(session_id)
    if not hist:
        return False
    msgs = hist[0].get("messages") or []
    return bool(msgs) and msgs[-1].get("agent") == "Tutor"


# --------------------------------------------------------------------------- #
# Topic-bridge helpers — "old topic or new?"
# --------------------------------------------------------------------------- #

# Common words that carry no topic signal.
_STOP_WORDS: frozenset = frozenset({
    "about", "again", "also", "although", "always", "another", "anything",
    "anyway", "back", "been", "before", "began", "begin", "being", "best",
    "better", "between", "both", "came", "come", "comes", "could", "done",
    "down", "each", "else", "ended", "even", "ever", "every", "find", "from",
    "give", "goes", "going", "good", "have", "hello", "here", "high", "just",
    "keep", "know", "left", "lets", "like", "look", "looked", "make", "makes",
    "might", "more", "most", "move", "much", "need", "never", "next", "okay",
    "once", "only", "open", "other", "over", "part", "past", "pick", "plan",
    "please", "provide", "real", "really", "right", "said", "same", "saying",
    "sees", "should", "show", "since", "some", "soon", "start", "started",
    "started", "still", "such", "sure", "take", "takes", "tell", "that",
    "their", "them", "then", "there", "these", "they", "think", "this",
    "those", "through", "time", "today", "together", "took", "trying",
    "under", "until", "used", "very", "want", "wants", "well", "were",
    "what", "when", "where", "which", "while", "will", "with", "work",
    "would", "your", "yours", "yourself",
    # single-char and very short
    "a", "i", "in", "is", "it", "of", "on", "or", "to", "be", "an",
    "at", "by", "do", "if", "me", "my", "no", "ok", "so", "up", "us",
    "we", "am", "as", "go",
})


# Maps surface words to a shared cluster ID so synonyms match each other.
# e.g. "business" and "cafe" both map to "biz" → one shared topic.
_TOPIC_MAP: Dict[str, str] = {
    # business / commerce
    "business": "biz", "cafe":       "biz", "shop":    "biz",
    "store":    "biz", "restaurant": "biz", "company": "biz",
    "startup":  "biz", "revenue":    "biz", "sales":   "biz",
    "client":   "biz", "customers":  "biz", "income":  "biz",
    "profit":   "biz", "market":     "biz", "price":   "biz",
    "product":  "biz", "brand":      "biz", "brand":   "biz",
    # performance problems
    "slow":      "struggle", "struggling": "struggle",
    "declining": "struggle", "dropping":   "struggle",
    "falling":   "struggle", "failing":    "struggle",
    # code / tech
    "python":   "code", "script":   "code", "function": "code",
    "program":  "code", "coding":   "code", "error":    "code",
    "debug":    "code", "database": "code", "endpoint": "code",
    # travel
    "trip":     "travel", "travel":  "travel", "flight": "travel",
    "hotel":    "travel", "vacation":"travel", "budget": "travel",
    # learning / teaching
    "learn":    "learn", "teach":   "learn", "explain": "learn",
    "study":    "learn", "lesson":  "learn", "course":  "learn",
}


def _topics(text: str) -> frozenset:
    """Extract topic tokens from text.

    Each meaningful word (>= 4 chars, not a stop word) is mapped to its
    cluster ID if one exists, otherwise kept as-is.  This lets synonyms
    ('cafe' / 'business', 'slow' / 'struggling') count as the same topic.
    """
    result = set()
    for w in re.findall(r"[a-z]+", text.lower()):
        if len(w) >= 4 and w not in _STOP_WORDS:
            result.add(_TOPIC_MAP.get(w, w))
    return frozenset(result)


def _find_related_session(
    message: str,
    current_session_id: str,
    user_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Return the most topically-similar past session, or None.

    Scoped to user_id so the bridge only surfaces the same user's own history.
    Requires at least 2 shared content words to trigger.
    """
    msg_topics = _topics(message)
    if len(msg_topics) < 2:
        return None

    candidates = memory_manager.get_recent_sessions(
        limit=8, exclude_session=current_session_id, user_id=user_id,
    )
    best: Optional[Dict[str, Any]] = None
    best_score = 1  # must beat 1 → needs score >= 2

    for sess in candidates:
        sess_text = " ".join(sess.get("user_messages", []))
        sess_topics = _topics(sess_text)
        score = len(msg_topics & sess_topics)
        if score > best_score:
            best_score = score
            best = sess

    return best


def _awaiting_topic_bridge(session_id: str) -> Optional[Dict[str, Any]]:
    """Return the pending bridge record if we're waiting for the user's old/new answer."""
    convo = CURRENT_CONVO.get(session_id, [])
    if convo and convo[-1].get("type") == "topic_bridge":
        return convo[-1]
    return None


def _interpret_topic_reply(reply: str) -> str:
    """Return 'old' or 'new' based on the user's answer to the bridge question.

    Defaults to 'new' — we never silently load old context; the user must
    clearly indicate they want it.
    """
    low = reply.lower().strip()
    old_signals = (
        "yes", "yeah", "yep", "yup", "that", "same", "old", "continue",
        "previous", "before", "pick up", "that one", "it is", "correct",
    )
    new_signals = (
        "no", "nope", "new", "fresh", "different", "something else",
        "not that", "another", "start over",
    )
    for sig in new_signals:
        if sig in low:
            return "new"
    for sig in old_signals:
        if sig in low:
            return "old"
    return "new"  # safe default: don't assume


def _load_session_context(old_session_id: str, current_session_id: str) -> None:
    """Prepend turns from an old session into the current session's in-memory history.

    After this call, _current_history(current_session_id) includes the old
    session's turns (oldest first) so the agents have full context.
    """
    old_turns = list(reversed(memory_manager.get_history(session_id=old_session_id)))
    CURRENT_CONVO.setdefault(current_session_id, []).extend([
        {"user_message": r.get("user_message", ""), "messages": r.get("messages", [])}
        for r in old_turns
    ])


def _topic_bridge_msg(session: Dict[str, Any]) -> Dict[str, Any]:
    """Build the agent message that asks 'old topic or new?'"""
    topic = (session.get("first_message") or "").strip()
    short = topic[:70] + ("…" if len(topic) > 70 else "")
    content = (
        f"Before I start — it looks like you might be continuing from an earlier "
        f'conversation about **"{short}"**.\n\n'
        f"Are we picking up where we left off, or is this something new?"
    )
    return {"agent": "TopicBridge", "content": content, "timestamp": datetime.now().isoformat()}


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


def _error_msg() -> Dict[str, Any]:
    """Clean, user-facing message shown when a model/API call failed.

    Replaces the raw '[Agent] Error: …' / 'Could not reach …' text so backend
    failures never leak through as if they were a real answer.
    """
    return {
        "agent": "System",
        "color": "white",
        "content": (
            "⚠️ I couldn't complete that — the AI backend didn't respond. "
            "Check that the model/API is reachable (and the API key is set), then try again."
        ),
        "timestamp": datetime.now().isoformat(),
    }


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


def _run_one_simulation_task(
    task_id: int,
    mock: bool,
    model: str,
    account: Optional[Dict[str, Any]],
    base_url: str,
    token: Optional[str],
) -> Dict[str, Any]:
    """Run one simulated chat request.

    Mock ON  → exercises all internal locks/counters without calling the AI API.
    Mock OFF → POSTs a real /chat request (uses Groq quota).
    """
    prompt, route_hint = _SIM_PROMPTS[task_id % len(_SIM_PROMPTS)]
    start = time.perf_counter()
    try:
        if mock:
            fake_in = random.randint(45, 180)
            fake_out = random.randint(20, 90)
            fake_total = fake_in + fake_out
            # Acquire same per-account lock path as real /chat
            _acct_lock = auth_manager.get_account_lock(account["id"]) if account else None
            if _acct_lock:
                _acct_lock.acquire()
            try:
                # Atomic DB increments — same as the real /chat path.
                auth_manager.record_session_stats(
                    api_calls=1, input_tokens=fake_in, output_tokens=fake_out,
                )
                if account:
                    auth_manager.record_usage(
                        account["id"], tokens=fake_total, api_calls=1,
                        context_bytes=len(prompt.encode()),
                    )
                sim_sid = f"sim-{uuid.uuid4()}"
                with _CONVO_LOCK:
                    CURRENT_CONVO[sim_sid] = [{"user_message": prompt, "messages": []}]
                    if account:
                        USER_SESSIONS.setdefault(account["id"], set()).add(sim_sid)
                auth_manager.bump_problems_solved()
            finally:
                if _acct_lock:
                    _acct_lock.release()
            elapsed = time.perf_counter() - start
            return {
                "task_id": task_id + 1, "prompt": prompt, "status": "done",
                "route": route_hint, "elapsed_s": round(elapsed, 4),
                "tokens": fake_total, "input_tokens": fake_in, "output_tokens": fake_out,
                "did_work": True, "error": None,
            }
        else:
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            resp = requests.post(
                f"{base_url}/chat",
                json={"message": prompt, "model": model,
                      "session_id": str(uuid.uuid4()), "new_chat": True},
                headers=headers, timeout=120,
            )
            elapsed = time.perf_counter() - start
            if resp.status_code == 200:
                data = resp.json()
                m = data.get("metrics", {})
                return {
                    "task_id": task_id + 1, "prompt": prompt, "status": "done",
                    "route": data.get("route", "?"), "elapsed_s": round(elapsed, 4),
                    "tokens": m.get("total_tokens"), "input_tokens": m.get("input_tokens"),
                    "output_tokens": m.get("output_tokens"),
                    "did_work": data.get("did_work", False), "error": None,
                }
            return {
                "task_id": task_id + 1, "prompt": prompt, "status": "failed",
                "route": "?", "elapsed_s": round(elapsed, 4), "tokens": 0,
                "input_tokens": None, "output_tokens": None,
                "did_work": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return {
            "task_id": task_id + 1, "prompt": prompt, "status": "failed",
            "route": "?", "elapsed_s": round(elapsed, 4), "tokens": 0,
            "input_tokens": None, "output_tokens": None, "error": str(exc)[:300],
        }


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
