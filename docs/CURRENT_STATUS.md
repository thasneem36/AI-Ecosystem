# Koottam — Current Status Report

**Date:** 2026-06-25  
**Legend:** ✅ COMPLETE · 🟡 PARTIAL / NEEDS WORK · 🔴 NOT STARTED · 🚧 IN PROGRESS

---

## QUICK SUMMARY

| Area | Status | Notes |
|---|---|---|
| Backend core | ✅ | All agents working, bugs fixed |
| Bug fixes | ✅ | 4 bugs found and fixed this sprint |
| Rename (→ Koottam) | ✅ | Every file updated |
| Architecture docs | ✅ | 12-section reference written |
| New frontend (chat UI) | ✅ | Dark minimal, served from backend |
| Frontend polish | ✅ | Copy button, 80% width, demo messages |
| Rate limiting | 🔴 | Not started |
| Caching | 🔴 | Not started |
| SQLite / real DB | 🔴 | Not started |
| Auth / users | 🔴 | Not started |
| Claude backend | 🟡 | Wired but model ID needs fixing |

---

## 1. COMPLETED ✅

### 1.1 Backend — Multi-Agent Pipeline

| Component | File | Detail |
|---|---|---|
| Router (intent classifier) | `agents/router_agent.py` | Classifies → `chat / task / code / learn` |
| Assistant (small-talk) | `agents/router_agent.py` (ChatAgent) | Quick one-liner replies |
| Clarifier | `agents/clarifier_agent.py` | Asks one targeted question on vague input |
| Planner | `agents/planner_agent.py` | Breaks task into ordered steps |
| Executor | `agents/executor_agent.py` | Runs the plan, uses search context if available |
| Coding | `agents/coding_agent.py` | Writes Python, runs it, saves to `output/` |
| Tutor | `agents/teach_agent.py` | Explains concepts step by step |
| Web Search | `agents/search_agent.py` | DuckDuckGo, opt-in, off by default |
| Base class | `agents/base_agent.py` | `think()` dispatches to ollama/groq/claude |

**Full call path:**
```
POST /chat → Router classifies → branch:
  chat   → Assistant
  task   → Clarifier? → Planner → Executor (+ optional Search)
  code   → CodingAgent → run_python() → save output/
  learn  → Tutor
```

---

### 1.2 Bug Fixes (all verified)

| Bug | Root Cause | Fix Applied |
|---|---|---|
| **Backend default bypassed** | `ChatRequest.model` defaulted to `"ollama"` hardcoded in the request schema, ignoring `settings.DEFAULT_BACKEND` | Changed to `Optional[str] = None`; server resolves None → `settings.DEFAULT_BACKEND` |
| **LLM error strings as valid answers** | `think()` on failure returned an error string like `"Ollama error: …"` which Executor would then read and include in the reply | Added `record_usage(error=True)`, `had_error()`, `last_call_failed()`; post-pipeline check replaces output with clean error message |
| **Clarifier infinite loop** | No cap on clarifying questions; Clarifier kept asking even after user answered | Added `MAX_CLARIFYING_QUESTIONS = 2`, two-stage judgment (`assess()` strict on fresh, `has_enough()` lenient after answer) |
| **Cross-conversation bleed** | Continuation helpers read `memory_manager.get_history()` (global file), so old conversations influenced new ones | Added `CURRENT_CONVO` list + `_current_history()`; all continuation logic now scoped to the in-process conversation only |

---

### 1.3 Memory & Tools

| Item | File | Status |
|---|---|---|
| Conversation memory | `memory/memory_manager.py` | JSON, thread-locked, saves every conversation |
| Code runner | `tools/code_runner.py` | subprocess, 30s timeout, interrupt scrubbing |
| File manager | `tools/file_manager.py` | output/ CRUD, path-traversal guard |
| Web search | `tools/search.py` | DuckDuckGo via `ddgs` |
| Token recording | `agents/base_agent.py` | ContextVar-based, per-request, no cross-request bleed |
| System metrics | `api/server.py` + `psutil` | CPU / memory / disk via `GET /admin/system` |

---

### 1.4 API Endpoints

| Endpoint | Status |
|---|---|
| `POST /chat` | ✅ |
| `GET /ui` | ✅ serves `frontend/index.html` |
| `GET /` | ✅ health check |
| `GET /settings` + `POST /settings` | ✅ |
| `POST /memory/clear` | ✅ |
| `GET /history` + `GET /history/{id}` | ✅ |
| `GET /files` + `GET /files/{name}/download` | ✅ |
| `GET /agents/status` | ✅ |
| `GET /admin/dashboard` | ✅ (endpoint exists, demo data) |
| `GET /admin/system` | ✅ live `psutil` data |
| `GET /admin/keys` + `POST /admin/keys` | ✅ writes to `.env` live |
| `POST /admin/agents/{key}/{action}` | ✅ |

---

### 1.5 New Frontend — `frontend/index.html`

Single-file, no npm, no build step. Served at `http://127.0.0.1:8000/ui`.

| Feature | Detail |
|---|---|
| Dark minimal design | `#0b0b0b` background, indigo accent — Linear/Vercel style |
| Agent color labels | Assistant=slate, Clarifier/Planner=amber, Executor=cyan, Coding=green, Tutor=purple |
| Metrics line | Time · API calls · tokens · route per response |
| Code block rendering | Fenced ` ``` ` blocks with dark background, monospace font |
| **Copy button** | Top-right of every code block, turns green + "Copied!" on click |
| Inline code / bold | `` `x` `` and `**x**` rendered inline |
| **80% width layout** | Content column centered, breathing room on both sides |
| Demo conversations | 3 pre-loaded realistic exchanges (Clarifier → Planner/Executor → Coding), no API hit |
| Model selector | groq / ollama / claude, seeded from `GET /settings`, saves on change |
| Backend status dot | Green = alive, red = unreachable |
| Clear button | Calls `POST /memory/clear`, wipes demo + session messages |
| Typing indicator | Animated 3-dot pulse while waiting |
| Input | Textarea, Enter to send, Shift+Enter for newline, auto-grows |

---

### 1.6 Documentation

| File | Content |
|---|---|
| `TECHNICAL_ARCHITECTURE.md` | 12-section reference: Vision, Stack, Token Mgmt, Context Window, Rate Limiting, Load Balancing, Caching, Error Handling, Smart Routing, Build Priority, Agent Architecture, Data Collection. All sections tagged 🟢/🟡/🔴 |
| `docs/SYSTEM_ANALYSIS.md` | Full engineering audit: what works, what's broken, top 8 fixes |
| `docs/CURRENT_STATUS.md` | This file |
| `docs/BLUEPRINT.md` | Original system design |
| `docs/VISION.md` | Project vision and principles |
| `docs/AGENTS.md` | Per-agent reference |
| `docs/SYSTEM_FLOW.md` | Request flow diagram |

---

### 1.7 Project Rename

Every file updated: `AI Ecosystem` → `Koottam` across `main.py`, `run_terminal.py`, `config/settings.py`, `README.md`, `docs/README.md`, `docs/VISION.md`, `docs/CURRENT_STATUS.md`, `archive/frontend/index.html`, `archive/frontend/src/components/Sidebar.tsx`, `archive/frontend/src/components/ChatBox.tsx`.

---

## 2. PARTIAL / NEEDS WORK 🟡

### 2.1 Claude Backend
- **Code is wired** — `_claude_chat()` in `base_agent.py` works structurally
- **Model ID may be wrong** — verify `CLAUDE_MODEL` in `config/settings.py` matches a live Anthropic model id (`claude-sonnet-4-6` is correct as of June 2026)
- **Untested end-to-end** — needs a real API key + a live test

### 2.2 Ollama / Local Model
- `qwen3.5` (9.7B) exceeds the timeout on most machines
- Fix: switch to a smaller model in `.env`: `OLLAMA_MODEL=llama3.2:3b` or `qwen2.5:3b`
- Groq is the reliable default today

### 2.3 Clarifier Balance
- `MAX_CLARIFYING_QUESTIONS = 2` caps the loop — works
- `has_enough()` leniency after first answer — works
- **Edge case not covered:** if the user's answer is still extremely vague after 2 questions, the system proceeds anyway (correct behavior given the cap, but the plan may be weak)

### 2.4 Token Limits
- Token **recording** works (ContextVar, per-call)
- Token **enforcement** does not exist — no pre-send counting, no per-route budget, no history trimming when context grows
- `max_tokens=1024` is hardcoded for Claude only; Ollama/Groq are uncapped

### 2.5 Context Window Management
- Per-agent history windowing works (Router=6 turns, Tutor=10 turns)
- `CURRENT_CONVO` scopes in-process history correctly
- **Missing:** conversation summarisation when history grows long; no SQLite backing

---

## 3. NOT STARTED 🔴

These are all **PLANNED** in `TECHNICAL_ARCHITECTURE.md` with design notes and pseudocode.

| Feature | Priority | Notes |
|---|---|---|
| **Caching** | HIGH | System prompt caching (Anthropic), response dedup, `functools.lru_cache` for routing |
| **Rate limiting** | HIGH | Per-model counters, request queue, exponential backoff |
| **Smart routing (local vs API)** | HIGH | Simple query → Ollama; complex/long → Groq/Claude; cached → cache hit |
| **Token counting pre-send** | MEDIUM | `tiktoken` library; trim history before it exceeds context window |
| **SQLite database** | MEDIUM | Replace flat `memory.json`; session IDs, user IDs, timestamps |
| **Context summarisation** | MEDIUM | Summarise old turns when conversation exceeds N tokens |
| **Load balancing / queue** | LOW | `asyncio.Queue` or Celery; agent priority; parallel tasks |
| **Fine-tune dataset pipeline** | LOW | Tag saved conversations with route + success; export as JSONL |
| **Dynamic agent spawning** | LOW | Spawn sub-agents per task type instead of fixed singletons |
| **Authentication** | — | Needed before any non-local deployment |
| **Code sandbox** | — | Docker or nsjail; subprocess-only is unsafe for public use |
| **Dashboard live data** | — | Wire `DashboardPage` to real `GET /admin/dashboard` data |

---

## 4. HOW TO RUN

```bash
# 1. Start backend (all endpoints + frontend)
python main.py

# 2. Open chat UI
http://127.0.0.1:8000/ui

# 3. Or use the terminal client
python run_terminal.py

# Default model: groq (fastest, no local GPU needed)
# Requires: GROQ_API=gsk_... in .env
```

---

## 5. WHAT TO BUILD NEXT (priority order)

1. **Fix Claude model ID** — 30 minutes, zero risk, unlocks the third backend
2. **Caching** — biggest bang for cost reduction; start with `lru_cache` on router, then Anthropic prompt caching
3. **Smart routing (auto local vs API)** — complexity scorer in Router; simple → Ollama, complex → Groq
4. **Token counting + pre-send trim** — add `tiktoken`, trim oldest turns before sending if over budget
5. **Rate limiting** — request queue + per-model counters + retry with backoff
6. **SQLite** — replace `memory.json` for proper session management
7. **Frontend: streaming responses** — switch `POST /chat` to SSE/streaming so replies appear word-by-word
8. **Frontend: conversation history panel** — collapsible sidebar listing past sessions from SQLite
