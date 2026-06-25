# Koottam — Technical Architecture

**Living reference document for the entire build.**
Last updated: 2026-06-08 · Status legend: 🟢 BUILT · 🟡 PARTIAL · 🔴 PLANNED

> Read the code before reading this doc. If anything here disagrees with the code,
> the code wins — then update this doc. Never let them silently drift.

---

## Table of Contents

1. [Project Vision](#1-project-vision)
2. [Tech Stack](#2-tech-stack)
3. [Token Management](#3-token-management)
4. [Context Window Management](#4-context-window-management)
5. [Rate Limiting](#5-rate-limiting)
6. [Load Balancing](#6-load-balancing)
7. [Caching Strategy](#7-caching-strategy)
8. [Error Handling](#8-error-handling)
9. [Smart Routing — Local vs API](#9-smart-routing--local-vs-api)
10. [Build Priority Order](#10-build-priority-order)
11. [Agent Architecture](#11-agent-architecture)
12. [Data Collection Strategy](#12-data-collection-strategy)

---

## 1. Project Vision

### What Koottam is

Most tools stop at *information* or an *answer*. Koottam exists to **solve the user's actual problem** — to move them from "stuck" to "done."

| Tool | What it does | Limitation |
|---|---|---|
| Google | Informs | Leaves thinking to you |
| ChatGPT | Answers | Responds to what you typed, not what you need |
| **Koottam** | **Solves** | Understands the real problem, confirms, then drives to a concrete resolution |

An answer ends the conversation. A solution ends the *problem*.

### Origin and ambition

Built in **Sri Lanka**, designed for a **global** audience. Local-first (runs on Ollama, works offline and privately), with optional hosted models (Groq, Claude) for speed and capability. The same character and principles scale from a single laptop to many users.

**Koottam is a "solving company", not just an AI company.** The agents, tools, and pipeline exist to move people from vague problems to real outcomes — a plan that executes, code that runs, a lesson that lands.

### Non-negotiable principles (binding on every agent and every change)

1. **Lead with conversation to find the REAL problem.**
   The first message is rarely the real need. Talk like a thoughtful person, notice mood, and surface what's actually wrong before doing any work.

2. **Clarify-and-confirm before solving.**
   When a request is vague, restate what was understood in plain words and ask one open question. Do NOT assume the domain. Only act once the goal is clear.

3. **Tone flexes, truth never bends.**
   Adapt delivery freely — be warm with someone upset, direct with someone blunt. Never soften facts, never flatter, never tell people what they want to hear.

4. **Friendliness serves the problem, not engagement.**
   Warmth is a tool for solving, not a hook to keep people talking. If a short answer fully helps, give the short answer.

---

## 2. Tech Stack

### Current stack (what is actually running)

| Layer | Technology | Status |
|---|---|---|
| **Backend** | Python 3.11+ · FastAPI · Uvicorn | 🟢 BUILT |
| **Frontend** | TypeScript · React 18 · Tailwind · Vite | 🟡 PARTIAL (archived, not running) |
| **Local LLM** | Ollama · `qwen3.5` (9.7B default) | 🟢 BUILT (slow on big models) |
| **Hosted fast LLM** | Groq API · `llama-3.3-70b-versatile` | 🟢 BUILT (current default) |
| **Hosted premium LLM** | Anthropic Claude | 🟡 PARTIAL (code wired; model id needs fix) |
| **Memory / DB** | JSON flat file (`memory/memory.json`) | 🟢 BUILT (single-user only) |
| **Persistent DB** | SQLite | 🔴 PLANNED (replaces JSON) |
| **Search** | DuckDuckGo (`ddgs`) | 🟢 BUILT (opt-in, off by default) |
| **System metrics** | `psutil` | 🟢 BUILT |

### Dependencies (`requirements.txt`)

```txt
fastapi          # HTTP framework
uvicorn[standard]# ASGI server
requests         # Sync HTTP client (all model API calls)
ddgs             # DuckDuckGo search
colorama         # Terminal colour output
python-dotenv    # .env loading
pydantic         # Request/response validation
psutil           # Host CPU/memory/disk metrics
```

**Planned additions:**

```txt
tiktoken         # Token counting before sending (§3)
httpx            # Async HTTP client to replace requests (§6)
cachetools       # In-memory LRU caching (§7)
redis            # Persistent cache (§7, later)
```

### Single source of truth for models

All model names and backends live in **one place only**: `config/settings.py`.

```python
# config/settings.py — change model here, nowhere else
OLLAMA_MODEL     = os.getenv("OLLAMA_MODEL", "qwen3.5")
GROQ_MODEL       = os.getenv("GROQ_MODEL",   "llama-3.3-70b-versatile")
CLAUDE_MODEL     = os.getenv("CLAUDE_MODEL",  "claude-sonnet-4-6")  # ← fix pending
DEFAULT_BACKEND  = os.getenv("DEFAULT_BACKEND", "groq")
```

> **Known issue:** `CLAUDE_MODEL` is currently set to `"claude-opus-4-8"` which is not a valid Anthropic model id. Fix to `"claude-sonnet-4-6"` before using the Claude backend.

---

## 3. Token Management

### 3.1 Token recording 🟢 BUILT

Every model call records its token usage via a `ContextVar` so concurrent requests don't mix metrics.

```python
# agents/base_agent.py

_usage_log: contextvars.ContextVar = contextvars.ContextVar("usage_log", default=None)

def record_usage(model, input_tokens, output_tokens, error=False):
    log = _usage_log.get()
    if log is None:
        log = []; _usage_log.set(log)
    log.append({
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "error": error,     # True if the call failed
    })
```

After the pipeline runs, `api/server.py` aggregates into a per-response metrics object:

```python
# api/server.py — _build_metrics()
def _build_metrics(usage_log, elapsed, model):
    api_calls = len(usage_log)
    in_tok  = sum(u.get("input_tokens")  or 0 for u in usage_log)
    out_tok = sum(u.get("output_tokens") or 0 for u in usage_log)
    return {
        "total_time_seconds": round(elapsed, 2),
        "api_calls":          api_calls,
        "input_tokens":       in_tok,
        "output_tokens":      out_tok,
        "total_tokens":       in_tok + out_tok,
        "model":              model,
    }
```

Token totals feed `SESSION_STATS` (lifetime totals shown on the admin dashboard).

### 3.2 Token limits 🟡 PARTIAL

Currently only Claude enforces a limit, hard-coded:

```python
# agents/base_agent.py — _claude_chat()
payload = {
    "model": settings.CLAUDE_MODEL,
    "max_tokens": 1024,   # ← hard-coded; truncates long answers silently
    ...
}
```

Ollama and Groq have **no explicit token limits**. They will use as many tokens as the model allows.

### 3.3 Pre-send counting, budgets, and trimming 🔴 PLANNED

**Target implementation:**

```python
# PLANNED: tools/token_counter.py
import tiktoken

# One encoder per model family (reuse across calls)
_encoders = {}

def count_tokens(text: str, model: str = "gpt-4") -> int:
    enc = _encoders.setdefault(model, tiktoken.encoding_for_model(model))
    return len(enc.encode(text))

def trim_to_budget(messages: list[dict], max_tokens: int) -> list[dict]:
    """Drop oldest turns until the total fits in the budget."""
    while messages and count_tokens(str(messages)) > max_tokens:
        messages.pop(0)
    return messages
```

**Per-route token budgets (target):**

| Route | Recommended max_tokens | Rationale |
|---|---|---|
| `chat` | 512 | Short conversational reply |
| `task` (planner) | 256 | Just a numbered list, 3–6 steps |
| `task` (executor) | 1 024 | Full answer may be long |
| `code` | 2 048 | Code can be large |
| `learn` | 768 | One concept at a time |

**Rule:** Always set `max_tokens`. Never let a call consume unbounded tokens.
Count tokens *before* sending to avoid wasted API spend on inputs that exceed the context window.

---

## 4. Context Window Management

### 4.1 History windowing 🟢 BUILT

Each agent reads only the most recent N turns of conversation to keep prompts short.

```python
# agents/router_agent.py — format_history()
def format_history(history: list, max_turns: int = 6) -> str:
    """Newest-first list from memory → chronological User/You transcript."""
    recent = list(history[:max_turns])
    recent.reverse()          # oldest → newest for the LLM
    lines = []
    for rec in recent:
        user  = (rec.get("user_message") or "")[:300]
        reply = (rec.get("messages") or [{}])[-1].get("content", "")[:300]
        if user:  lines.append(f"User: {user}")
        if reply: lines.append(f"You: {reply}")
    return "\n".join(lines)
```

**Per-agent history limits:**

| Agent | `MAX_HISTORY_TURNS` | Why |
|---|---|---|
| Assistant (chat) | 6 | Enough for conversational thread |
| Clarifier | 4 | Only needs the immediate context |
| Tutor | 10 | Lessons need more continuity |
| Router | — | Doesn't use history |
| Planner / Executor | — | Get the task, not the conversation |

### 4.2 Conversation scoping 🟢 BUILT

The current in-process conversation is tracked in `CURRENT_CONVO` (server-level list, oldest first). Continuation detection reads **only this list**, never the full history file — so a fresh conversation never inherits details from an old, unrelated one.

```python
# api/server.py
CURRENT_CONVO: List[Dict] = []   # reset on process start or /memory/clear

def _current_history() -> List[Dict]:
    """Current conversation only, newest first. Never the whole memory file."""
    return list(reversed(CURRENT_CONVO))
```

### 4.3 Summarisation and SQLite 🔴 PLANNED

When conversations grow long, the oldest turns should be *summarised* before they fall off the window — not silently dropped.

```python
# PLANNED: tools/summariser.py
def summarise_old_turns(turns: list[dict], model: str) -> str:
    """Compress older turns into a one-paragraph summary for context injection."""
    transcript = format_history(turns)
    prompt = f"Summarise this conversation in 2–3 sentences:\n{transcript}"
    return BaseAgent().think(prompt, model=model)
```

**Storage target — SQLite schema:**

```sql
-- PLANNED: memory/schema.sql
CREATE TABLE conversations (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    model       TEXT,
    user_msg    TEXT,
    route       TEXT,
    did_work    INTEGER DEFAULT 0,   -- 1 if pipeline ran
    messages    TEXT,                -- JSON blob
    summary     TEXT                 -- auto-generated when turns > threshold
);

CREATE INDEX idx_session ON conversations(session_id, timestamp DESC);
```

**Inject only what the current task needs.** An executor answering a coding question doesn't need the conversation about mood from five turns ago.

---

## 5. Rate Limiting

### Current state 🔴 PLANNED

There is **zero rate limiting** in the codebase. Every `/chat` POST goes straight to the model APIs with no throttle, no queue, no token budget cap.

This is safe today (single local user, `127.0.0.1` only). It becomes a problem the moment:
- More than one user hits the server
- A loop or bot sends many rapid requests
- Token spend approaches Groq/Anthropic plan limits

### Target implementation

**Layer 1 — FastAPI middleware (per-IP, per-minute):**

```python
# PLANNED: api/middleware.py
from collections import defaultdict
import time

_request_times: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT = 30  # requests per minute per IP

async def rate_limit_middleware(request, call_next):
    ip    = request.client.host
    now   = time.time()
    window = [t for t in _request_times[ip] if now - t < 60]
    if len(window) >= RATE_LIMIT:
        return JSONResponse({"error": "rate limit exceeded"}, status_code=429)
    _request_times[ip] = window + [now]
    return await call_next(request)
```

**Layer 2 — Token budget cap (daily):**

```python
# PLANNED: track in SESSION_STATS
TOKEN_DAILY_BUDGET = 500_000   # adjust to plan limits

def _within_budget() -> bool:
    used = SESSION_STATS["input_tokens"] + SESSION_STATS["output_tokens"]
    return used < TOKEN_DAILY_BUDGET
```

**Layer 3 — Retry with exponential backoff (for 429s from upstream):**

```python
# PLANNED: inside BaseAgent.think()
import time, random

def _call_with_retry(self, fn, *args, max_retries=3):
    for attempt in range(max_retries):
        try:
            return fn(*args)
        except requests.HTTPError as e:
            if e.response.status_code == 429 and attempt < max_retries - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(wait)
            else:
                raise
```

---

## 6. Load Balancing

### Current state 🔴 PLANNED

All agents are **fixed singletons** instantiated once at server startup in `api/server.py`. `chat()` is a synchronous FastAPI handler that runs in a threadpool — requests are handled one at a time per thread, linearly through the pipeline.

```python
# api/server.py — today: fixed singletons
router    = RouterAgent()
assistant = ChatAgent()
clarifier = ClarifierAgent()
planner   = PlannerAgent()
executor  = ExecutorAgent()
coder     = CodingAgent()
searcher  = SearchAgent()
tutor     = TeachAgent()
```

This works for one user. It breaks under concurrent load (shared mutable state on each agent instance — `self.status`, `self.last_activity`, and the Clarifier's system prompt swap).

### Target: async pipeline + priority queue

**Priority levels:**

| Priority | Request type | Example |
|---|---|---|
| HIGH | User-facing `/chat` | "help me fix this" |
| MEDIUM | Background agent subtasks | search, summarise |
| LOW | Admin/analytics | dashboard refresh |

```python
# PLANNED: api/queue.py
import asyncio

HIGH   = 0
MEDIUM = 1
LOW    = 2

_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()

async def enqueue(priority: int, task_fn, *args):
    await _queue.put((priority, task_fn, args))

async def worker():
    while True:
        priority, fn, args = await _queue.get()
        await fn(*args)
        _queue.task_done()
```

**Agents become stateless callables** (no mutable instance state), allowing multiple concurrent calls:

```python
# PLANNED: agents are functions or re-instantiated per call
async def plan(task: str, model: str) -> dict:
    agent = PlannerAgent()   # fresh instance, no shared state
    return agent.run(task, model=model)
```

Migrate `requests` → `httpx` for true async HTTP when this lands.

---

## 7. Caching Strategy

### Current state 🔴 PLANNED

There is **no caching anywhere** in the codebase. Every request hits the model APIs fresh, even for identical or near-identical inputs.

### Layer 1 — Anthropic prompt caching (~90% discount) 🔴 PLANNED

Anthropic's API caches prompt prefixes. System prompts sent with `"cache_control": {"type": "ephemeral"}` are stored for 5 minutes and billed at 10% of normal input token cost on cache hits.

```python
# PLANNED: agents/base_agent.py — _claude_chat() with prompt caching
payload = {
    "model": settings.CLAUDE_MODEL,
    "max_tokens": 1024,
    "system": [
        {
            "type": "text",
            "text": self.system_prompt,
            "cache_control": {"type": "ephemeral"},  # ← cache this prefix
        }
    ],
    "messages": [{"role": "user", "content": prompt}],
}
```

Every agent has a **fixed system prompt** — ideal candidates for caching. The Router, Clarifier, Planner, and Executor system prompts never change between calls.

### Layer 2 — In-memory LRU cache for repeated identical inputs 🔴 PLANNED

```python
# PLANNED: tools/cache.py
from cachetools import LRUCache
import hashlib, json

_cache: LRUCache = LRUCache(maxsize=500)   # holds last 500 unique responses

def _key(system_prompt: str, user_prompt: str) -> str:
    raw = json.dumps({"s": system_prompt, "u": user_prompt}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()

def get_cached(system_prompt: str, user_prompt: str):
    return _cache.get(_key(system_prompt, user_prompt))

def set_cached(system_prompt: str, user_prompt: str, response: str):
    _cache[_key(system_prompt, user_prompt)] = response
```

Use this in `BaseAgent.think()` as a hot-path check before any API call.

### Layer 3 — Persistent cache (Redis) 🔴 PLANNED

For responses that should survive server restarts (e.g. common greetings, reference answers):

```python
# PLANNED: Redis-backed persistent cache
import redis

_redis = redis.Redis(host="localhost", port=6379, db=0)

def get_persistent(key: str):
    val = _redis.get(key)
    return val.decode() if val else None

def set_persistent(key: str, value: str, ttl: int = 3600):
    _redis.setex(key, ttl, value)
```

### What to cache vs what not to cache

| Cache | ✅ Good candidates | ❌ Never cache |
|---|---|---|
| Prompt cache | System prompts (static, repeated) | — |
| LRU in-memory | Router classifications, clarity assessments | Tutor/chat replies (contextual) |
| Persistent (Redis) | Common short answers, static reference | Code execution results, anything with live data |

---

## 8. Error Handling

### 8.1 Pipeline-level error detection 🟢 BUILT

Every failed model call records `error=True` in the usage log. After the pipeline runs, `chat()` checks:

```python
# agents/base_agent.py — inside think()
except requests.exceptions.ConnectionError:
    self.status = "offline"
    record_usage(model_name, None, None, error=True)
    return f"[{self.name}] Could not reach the {model} API. ..."

except Exception as exc:
    self.status = "error"
    record_usage(model_name, None, None, error=True)
    return f"[{self.name}] Error: {exc}"
```

```python
# api/server.py — after the pipeline finishes
failed = had_error()
if failed:
    did_work = False
    messages = [_error_msg()]   # clean user-facing message

if failed:
    return {"conversation_id": None, ...}   # not saved, not counted
```

**On a failed call:**
- ✅ User gets a clean message ("⚠️ I couldn't complete that…")
- ✅ The conversation is NOT saved to memory
- ✅ The "problems solved" counter is NOT incremented
- ✅ Session token/call counts still accumulate (honest accounting)

### 8.2 Mid-pipeline short-circuit 🟡 PARTIAL

Currently only the Planner failure is short-circuited (Executor won't run on a failed plan):

```python
# api/server.py — _run_task_pipeline()
plan_msg = planner.run(message, model=model)
messages.append(plan_msg)
if last_call_failed():
    return   # ← short-circuit: don't run Executor on a failed plan
```

The Router, Clarifier, Executor, and Coding agent failures are caught by `had_error()` at the end, but the pipeline continues running until completion. **Not ideal** — a failed Router should not run Clarifier, etc.

### 8.3 Retry and fallback chain 🔴 PLANNED

**Target fallback chain:** Claude → Groq → Ollama → error message

```python
# PLANNED: agents/base_agent.py — think() with fallback
FALLBACK_CHAIN = ["claude", "groq", "ollama"]

def think_with_fallback(self, prompt: str, preferred_model: str) -> str:
    chain = FALLBACK_CHAIN[FALLBACK_CHAIN.index(preferred_model):]
    for model in chain:
        try:
            result = self._call_backend(prompt, model)
            return result
        except (requests.ConnectionError, requests.HTTPError) as e:
            if model == chain[-1]:
                raise   # exhausted all fallbacks
            # try next backend
```

**Timeout handling:**

| Backend | Current timeout | Target |
|---|---|---|
| Ollama | `OLLAMA_TIMEOUT` (120s) | Separate `OLLAMA_TIMEOUT` |
| Groq | `OLLAMA_TIMEOUT` (bug: wrong var) | Separate `GROQ_TIMEOUT` (30s) |
| Claude | `OLLAMA_TIMEOUT` (bug: wrong var) | Separate `CLAUDE_TIMEOUT` (60s) |

> **Known bug:** `_groq_chat()` and `_claude_chat()` both use `settings.OLLAMA_TIMEOUT`. Fix by introducing `GROQ_TIMEOUT` and `CLAUDE_TIMEOUT` in `config/settings.py`.

**Error escalation rules:**

```
API timeout       → retry once with same backend → try next backend → error message
Rate limit (429)  → exponential backoff (1s, 2s, 4s) → try next backend
Empty response    → retry once → fallback to Ollama
Network failure   → immediate fallback to Ollama
Ollama offline    → error message (no fallback past local)
```

---

## 9. Smart Routing — Local vs API

### 9.1 Intent routing 🟢 BUILT

The `RouterAgent` classifies each message into exactly one path before any pipeline agent runs:

```
chat  → Assistant only            (1 model call total)
task  → Clarifier → Planner → Executor  (3–4 calls)
code  → Coding agent only         (1 call + subprocess)
learn → Tutor only                (1 call)
```

Learning requests hit a **deterministic regex fast-path** (no LLM call):

```python
# agents/router_agent.py
_LEARN_PATTERNS = [
    r"\bteach me\b", r"\bhelp me (?:to )?learn\b",
    r"\bexplain .+ to me\b", r"\bwalk me through\b", ...
]

def classify(self, message: str, model: str) -> str:
    if self._looks_like_learning(message):
        return "learn"   # deterministic, costs nothing
    return self._parse_route(self.think(...))
```

### 9.2 Backend abstraction 🟢 BUILT

All agents call one method — `think(prompt, model)` — and the backend is selected transparently:

```python
# agents/base_agent.py
def think(self, prompt: str, model: str = "ollama") -> str:
    if   model == "claude": reply, usage = self._claude_chat(prompt)
    elif model == "groq":   reply, usage = self._groq_chat(prompt)
    else:                   reply, usage = self._ollama_chat(prompt)
    return reply
```

**To change the default backend for the whole system:** one line in `config/settings.py`:

```python
DEFAULT_BACKEND = "groq"   # change to "ollama" or "claude" to switch globally
```

### 9.3 Need-based auto-selection 🔴 PLANNED

**Target decision table:**

| Task type | Route to | Why |
|---|---|---|
| Simple/conversational | Ollama (local) | Free, private, fast enough |
| Complex planning + execution | Groq | Speed, large context |
| Hardest reasoning / long context | Claude | Best capability |
| Cache hit | Return cached | Free |
| Sensitive / offline required | Ollama (forced) | Privacy |

**Target implementation — complexity scorer in the Router:**

```python
# PLANNED: agents/router_agent.py — select_backend()
def select_backend(self, message: str, route: str) -> str:
    if route == "chat":
        return "ollama"     # always local for chat

    complexity = self._score_complexity(message)
    if complexity == "low":
        return "ollama"
    elif complexity == "high":
        return "claude"
    else:
        return settings.DEFAULT_BACKEND   # medium → configured default

def _score_complexity(self, message: str) -> str:
    """Simple heuristic: length + keyword signals."""
    tokens = len(message.split())
    has_hard_keywords = any(w in message.lower()
        for w in ("analyse", "strategy", "architecture", "compare", "predict"))
    if tokens > 100 or has_hard_keywords:
        return "high"
    if tokens < 20:
        return "low"
    return "medium"
```

---

## 10. Build Priority Order

### Where we are right now

```
✅ DONE — Foundation (solid, working)
  ├── 8 agents (Router, Assistant, Clarifier, Planner, Executor, Coding, Tutor, Search)
  ├── BaseAgent abstraction (3 backends: Ollama, Groq, Claude)
  ├── Intent routing with fast-paths and continuation detection
  ├── Confirm-before-solving (Clarifier, 1-question cap, no bleed across convos)
  ├── Token recording per-call (ContextVar, metrics per response)
  ├── Error pipeline detection (had_error, clean message, no false saves)
  ├── JSON memory + thread-safe writes
  ├── Code runner (subprocess, 30s timeout, interrupt scrubbing)
  └── Real admin dashboard (psutil, live session stats)
```

### Roadmap

```
🟡 WEEK 1-2 — Make what exists correct and cheap
  ├── Fix Claude model id ("claude-sonnet-4-6")
  ├── Fix timeout coupling (separate GROQ_TIMEOUT, CLAUDE_TIMEOUT)
  ├── Add max_tokens per route (stop silent truncation)
  ├── Anthropic prompt caching on Claude calls (90% discount on system prompts)
  └── LRU in-memory cache for Router classifications + Clarifier assessments

🟡 WEEK 3-4 — Smart routing and error resilience
  ├── Need-based backend selector (complexity scorer in Router)
  ├── Per-backend fallback chain (Claude → Groq → Ollama)
  ├── Retry with exponential backoff for 429s
  ├── Separate timeout configs per backend
  └── Pre-send token counting (tiktoken) + per-route budgets

🟡 MONTH 2 — Stability for multiple users
  ├── Replace JSON memory with SQLite
  ├── Add session_id scoping (not just process-level CURRENT_CONVO)
  ├── Make agents stateless per-call (fix concurrency bugs)
  ├── Basic rate limiting middleware (per-IP, per-minute)
  └── Auth on /admin/* endpoints (even simple API key is better than nothing)

🔴 MONTH 3 — Scale and data
  ├── Request priority queue (asyncio.PriorityQueue)
  ├── Async HTTP client (httpx replaces requests)
  ├── Persistent Redis cache
  ├── Conversation summarisation (trim old turns without losing context)
  ├── Structured fine-tune dataset export
  └── Real code sandbox (Docker/subprocess with resource limits + network isolation)

🔴 MONTH 4+ — Advanced intelligence
  ├── Dynamic agent spawning (spawn specialist agents on demand)
  ├── Agent-to-agent messaging protocol
  ├── Load balancer + agent pool
  ├── Auto model-selection (need/cost/availability)
  └── Fine-tuned local model (trained on Koottam's own collected data)
```

---

## 11. Agent Architecture

### 11.1 Base class 🟢 BUILT

Every agent extends `BaseAgent` (`agents/base_agent.py`):

```python
class BaseAgent:
    name:          str = "Base"
    color:         str = "white"    # UI colour key
    system_prompt: str = "You are a helpful AI agent."

    def __init__(self):
        self.status        = "idle"   # idle | thinking | active | offline | error
        self.last_activity = None
        self.enabled       = True     # toggled by admin Agent Control

    def think(self, prompt: str, model: str = "ollama") -> str:
        """Send prompt to the chosen backend; record usage; return text."""

    def run(self, task: str, context=None, model="ollama") -> dict:
        """Override in subclasses. Returns a message dict."""

    def _message(self, content: str, **extra) -> dict:
        """Wrap a response: {agent, color, content, timestamp, ...extra}"""

    def status_dict(self) -> dict:
        """For admin Agent Control panel."""
```

### 11.2 All agents 🟢 BUILT

| Agent | File | Route | Inputs | Outputs | History turns |
|---|---|---|---|---|---|
| **Router** | `router_agent.py` | N/A — runs first | message | `chat\|task\|code\|learn` | 0 |
| **Assistant** | `router_agent.py` | `chat` | message + history | reply | 6 |
| **Clarifier** | `clarifier_agent.py` | `task` (gate) | message | `clear\|vague` + reply | 4 |
| **Planner** | `planner_agent.py` | `task` | clear task | reply + `steps[]` | 0 |
| **Executor** | `executor_agent.py` | `task` | task + steps + search | reply + `needs_code` | 0 |
| **Coding** | `coding_agent.py` | `code` | task | code + execution + file | 0 |
| **Tutor** | `teach_agent.py` | `learn` | topic + history | reply | 10 |
| **Search** | `search_agent.py` | `task` (opt-in) | query | text summary | 0 |

### 11.3 Current orchestration 🟡 PARTIAL

Today, `api/server.py:chat()` is the orchestrator — it calls agents in sequence, checks results, and decides the next step. There is no "Master Agent":

```
chat() in server.py
  ├── router.classify()
  ├── continuation overrides (CURRENT_CONVO)
  └── branch:
      chat  → assistant.run()
      task  → clarifier.assess()
                vague  → clarifier.run() + STOP
                clear  → [searcher.run()] → planner.run() → executor.run()
      code  → coder.run()
      learn → tutor.run()
```

### 11.4 Target: Master Agent + dynamic spawning 🔴 PLANNED

```
MasterAgent (orchestrator)
  ├── reads intent from Router
  ├── decides which specialists to spawn and in what order
  ├── passes results between them (message-passing protocol)
  └── spawns new specialist agents on demand for novel task types

Agent message format (target):
{
  "from":    "Planner",
  "to":      "Executor",
  "type":    "steps",
  "payload": ["Step 1: ...", "Step 2: ..."],
  "task_id": "abc-123"
}
```

**Agent communication protocol (PLANNED):** Each agent publishes its result to a shared task context dict keyed by `task_id`. Downstream agents pull what they need. This decouples the agents from the server orchestration loop.

### 11.5 One system prompt per agent

System prompts are class-level constants. They NEVER change at runtime (the Clarifier's temporary swap is a known anti-pattern to be refactored). The full prompts are documented in `docs/AGENTS.md`.

---

## 12. Data Collection Strategy

### 12.1 Conversations saved automatically 🟢 BUILT

Every successful pipeline run is saved to `memory/memory.json`:

```python
# memory/memory_manager.py — what gets saved per turn
{
    "id":           "<uuid>",
    "timestamp":    "<ISO datetime>",
    "model":        "groq",
    "user_message": "original user text",
    "preview":      "first 80 chars",
    "messages": [
        {"agent": "Planner",  "content": "1. step...", "steps": [...], ...},
        {"agent": "Executor", "content": "final answer...", ...}
    ]
}
```

Saved with `memory_manager.save_conversation()`. Failed calls are **never saved**.

### 12.2 Generated code saved to disk 🟢 BUILT

Every piece of code the Coding agent writes is saved to `output/solution_YYYYMMDD_HHMMSS.py`. This is a growing library of working solutions.

### 12.3 Structured fine-tune dataset 🔴 PLANNED

The goal: turn saved conversations into labeled prompt/completion pairs for local model fine-tuning.

```python
# PLANNED: tools/dataset_builder.py
import json
from pathlib import Path

def export_finetune_dataset(output_path: str = "data/finetune.jsonl"):
    """Convert memory.json into a fine-tune dataset."""
    records = memory_manager.get_history()
    pairs = []
    for rec in records:
        # Only include successful, complete pipeline runs
        msgs = rec.get("messages", [])
        if not msgs or not rec.get("did_work"):
            continue
        final = msgs[-1].get("content", "").strip()
        if not final:
            continue
        pairs.append({
            "prompt":     rec["user_message"],
            "completion": final,
            "route":      rec.get("route", "unknown"),
            "model":      rec.get("model", "unknown"),
            "quality":    "unrated",   # human-rated later
        })
    with open(output_path, "w") as f:
        for p in pairs:
            f.write(json.dumps(p) + "\n")
    return len(pairs)
```

**Collection principles:**
- Save every good API response (currently done via memory.json)
- Log what route was used and which model answered
- Track what worked (user didn't immediately rephrase the same question) vs what failed (had_error=True)
- Rate responses over time — human review > volume
- Build toward a fine-tune dataset that makes the local Ollama model as capable as the hosted models for Koottam's specific tasks

**What to collect per turn (add to memory schema):**

| Field | Purpose |
|---|---|
| `route` | Was it chat/task/code/learn? |
| `did_work` | Did the pipeline actually run? |
| `had_error` | Did any model call fail? |
| `clarified` | Did the Clarifier ask a question first? |
| `model` | Which backend answered? |
| `tokens` | Input + output tokens |
| `duration_s` | How long did it take? |

---

## Appendix: Known Bugs to Fix Before Anything Else

These are confirmed defects in the current code, not design gaps. Fix in order.

| # | Bug | File | Fix |
|---|---|---|---|
| 1 | Claude model id invalid (`claude-opus-4-8`) | `config/settings.py:65` | Set to `claude-sonnet-4-6` |
| 2 | Groq/Claude use `OLLAMA_TIMEOUT` (wrong var) | `base_agent.py:98,125` | Add `GROQ_TIMEOUT`, `CLAUDE_TIMEOUT` |
| 3 | Shared singleton agents are not concurrency-safe | `api/server.py:53-60` | Make agents stateless or per-request |
| 4 | Global `CURRENT_CONVO` collides with two tabs | `api/server.py` | Add `session_id` |
| 5 | `ping_backend()` only works for Ollama | `base_agent.py:203-212` | Add Groq/Claude reachability check |
| 6 | `GET /agents/status` mutates state | `api/server.py:250-252` | Move revive logic out of GET |
| 7 | All `/admin/*` endpoints are unauthenticated | `api/server.py` | Add API key or basic auth |
| 8 | Code runner has no sandbox (subprocess = RCE if exposed) | `tools/code_runner.py` | Docker or restrict to `127.0.0.1` only |
