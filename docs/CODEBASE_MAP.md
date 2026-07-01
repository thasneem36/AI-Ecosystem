# CODEBASE_MAP — Koottam

**Quick-reference map · Last updated: 2026-06-30**

> One-glance guide to the codebase so future work doesn't re-scan from scratch.
> Honest status tags: **WORKING** / **PARTIAL** / **PLANNED**.

---

## 1. PROJECT STRUCTURE

```
ai-ecosystem/
├── api/
│   ├── server.py            FastAPI app — all HTTP endpoints, chat() pipeline, auth deps,
│   │                        rate limit, per-account usage, admin/providers/simulation
│   └── knowledge.py         Knowledge ingestion router: upload→chunk→ChromaDB, tree, search
├── agents/
│   ├── base_agent.py        Shared base: Ollama/Groq/Claude backends, usage log, think()/run()
│   ├── router_agent.py      RouterAgent (classify) + ChatAgent (Assistant) + format_history()
│   ├── clarifier_agent.py   ClarifierAgent — vague/clear gate before task & code routes
│   ├── planner_agent.py     PlannerAgent — breaks a task into 3–6 ordered steps
│   ├── executor_agent.py    ExecutorAgent — carries out the plan, writes final answer
│   ├── coding_agent.py      CodingAgent — writes/runs Python, saves web (HTML/CSS/JS)
│   ├── teach_agent.py       TeachAgent (Tutor) — paced conversational teaching
│   └── search_agent.py      SearchAgent (Web Search) — DuckDuckGo, OFF by default
├── auth/
│   └── auth_manager.py      Accounts, sessions (bearer), per-account usage/limits, providers,
│                            server_stats — all in koottam.db, PBKDF2 hashing, stdlib only
├── tools/
│   ├── code_runner.py       run_python() — subprocess + 30s timeout, captures stdout/stderr
│   ├── ctf_shell.py         /ctf/shell — runs commands in Kali-WSL, admin-only, PERSONAL use
│   ├── file_manager.py      save_file / list_files / _safe_path (output/ dir guard)
│   └── search.py            search_summary() — DuckDuckGo text search helper
├── config/
│   └── settings.py          Single config source; reads .env. Model names, keys, paths, limits
├── memory/
│   ├── memory_manager.py    Conversation store (SQLite), user-scoped history/sessions
│   ├── koottam.db           SQLite: conversations + accounts/sessions/usage/providers/stats
│   ├── knowledge.db         SQLite: knowledge_items metadata (doc name, chunk_count)
│   ├── chroma_db/           ChromaDB persistent vector store ("koottam_knowledge", cosine)
│   └── memory.json          LEGACY — migration read only
├── frontend/
│   ├── index.html           Main chat + admin UI (single file)
│   └── knowledge.html       Knowledge-base admin UI (upload/tree/search)
├── output/                  Saved code artifacts (solution_*.py / *.html) + scratch run files
├── run_terminal.py          Terminal chat client — reuses api.server.chat() (no duplicate logic)
└── docs/                    SYSTEM_FLOW.md, CODEBASE_MAP.md, BLUEPRINT.md, VISION.md, …
```

---

## 2. AGENTS

| Agent | File | Job | When it runs |
|-------|------|-----|--------------|
| **Router** | router_agent.py | Classify message → `chat`/`task`/`code`/`learn` (default `chat`) | Every message, first; cannot be disabled |
| **Assistant** | router_agent.py (`ChatAgent`) | One warm, context-aware reply; surfaces real needs | `chat` route |
| **Clarifier** | clarifier_agent.py | Judge vague/clear; restate + ask ONE question | Gates `task` **and** `code` routes (max 2 Qs) |
| **Planner** | planner_agent.py | Break task into 3–6 ordered steps | `task` route, after clarifier passes |
| **Executor** | executor_agent.py | Execute the plan, produce final answer | `task` route, after Planner |
| **Coding** | coding_agent.py | Write Python (run it) or web (save .html) | `code` route, after clarifier passes |
| **Tutor** | teach_agent.py | Paced teaching; confirms, waits for "ready" | `learn` route |
| **Web Search** | search_agent.py | Fetch live web context for the Executor | `task` route **only if enabled** (off by default) |

Backends per agent come from `BaseAgent`: Ollama / Groq / Claude, chosen per message (default = Groq).

---

## 3. KEY FLOWS

### Message flow (full detail in `docs/SYSTEM_FLOW.md`)
```
request (msg + session_id + bearer token)
  → auth: bootstrap-open OR verify session
  → rate-limit check (sliding window) → token-budget check (per account)
  → restore session context from DB if resuming
  → TOPIC BRIDGE? ("old or new?" on a related new session; skipped on +New chat)
  → ROUTER classifies → continuation overrides (Tutor→learn, Clarifier→task/code)
  → run route agents:
        chat  → Assistant
        learn → Tutor
        task  → Clarifier → [Web Search?] → Planner → Executor
        code  → Clarifier → Coding (Python runs / web saved)
  → on backend failure: clean error, not saved, not counted
  → save turn (SQLite, scoped to user_id + session_id)
  → record usage (atomic SQL) + metrics (time, calls, tokens, route)
  → response { route, messages[], model, metrics, did_work }
```

### Knowledge ingestion flow (`api/knowledge.py`)
```
admin uploads PDF/DOCX/TXT  →  background job
  → extract text (pypdf / python-docx / utf-8)
  → chunk ~500 words
  → embed + store in ChromaDB (batched 500) + metadata row in knowledge.db
  → poll /knowledge/job/{id} for progress
Browse:  /knowledge/tree  (items + first 12 chunk previews)
Test:    /knowledge/search  (semantic query → ranked chunks)   ← retrieval proof only
```
**No RAG answering** — the chat pipeline does NOT query ChromaDB yet.

---

## 4. FEATURES

| Feature | Status | Notes |
|---------|--------|-------|
| Auth (login/session) | **WORKING** | Username/password, PBKDF2-HMAC-SHA256, 30-day bearer tokens. Bootstrap mode: open until first account created |
| Accounts / roles | **WORKING** | Admin CRUD, roles `admin`/`user`, login-as, activate/deactivate |
| Token limits | **WORKING** | Per-account `token_limit` + rolling reset window (`reset_hours`); 429 when exceeded |
| Per-user separation | **WORKING** | `conversations.user_id`; history/sessions/clear scoped per user; admins see all |
| Concurrency safety | **WORKING** | Per-account locks, atomic SQL counters (`SET x=x+N`), WAL mode, ContextVar usage log, double-checked session restore. `/admin/simulate` load-tests it |
| Rate limiting | **WORKING** | Sliding-window deque per account; configurable via .env; admins exempt by default |
| Sandbox (code exec) | **PARTIAL** | Python runs in subprocess + 30s timeout, but NOT isolated (same user/FS). Local-trusted only |
| Knowledge base | **WORKING** | Upload→chunk→ChromaDB, tree view, delete, stats |
| Search test | **WORKING** | `/knowledge/search` semantic query over stored chunks |
| Web search (chat) | **PARTIAL** | SearchAgent works but OFF by default; opt-in via Agent Control |
| API provider registry | **WORKING** | Add/detect/test/switch providers (Groq/Anthropic/OpenAI/Ollama); keys live in .env |
| Master / orchestrator agent | **PLANNED** | Router only classifies; no agent-to-agent handoff — a route commits |
| RAG (answering from KB) | **PLANNED** | Ingestion + retrieval exist; not wired into the chat pipeline |
| MCP | **PLANNED** | Not present in the codebase |

---

## 5. TECH SETUP

**Models** (set in `config/settings.py`, override via `.env`):
- Default backend: **Groq** `llama-3.3-70b-versatile` (`DEFAULT_BACKEND=groq`)
- Local/offline fallback: **Ollama** `qwen3.5` (`OLLAMA_MODEL`)
- Paid alt: **Claude** `claude-sonnet-4-6` (`ANTHROPIC_API_KEY`)
- Selectable per-message in the UI.

**Packages:** fastapi, uvicorn, requests, pydantic, psutil, colorama, python-dotenv,
chromadb, pypdf, python-docx. Stdlib: sqlite3, hashlib, secrets, threading, subprocess.

**Config / keys:** all config in `config/settings.py`; secrets in `.env` at project root
(`GROQ_API`, `ANTHROPIC_API_KEY`, `OLLAMA_MODEL`, rate-limit + CORS vars). Keys are masked
in API responses, never returned in full.

**Databases:**
| File | Engine | Holds |
|------|--------|-------|
| `memory/koottam.db` | SQLite (WAL) | conversations + accounts, sessions, usage, api_providers, server_stats |
| `memory/knowledge.db` | SQLite | knowledge_items (doc metadata) |
| `memory/chroma_db/` | ChromaDB | knowledge chunk vectors (collection `koottam_knowledge`) |
| `memory/memory.json` | JSON | legacy, migration read only |

---

## 6. KNOWN ISSUES / BUGS

- **Some admin endpoints are unauthenticated:** `/settings`, `/admin/keys`, `/admin/dashboard`,
  `/admin/system`, `/admin/agents`, `/admin/agents/{key}/{action}` have NO auth dependency.
  Account/provider/simulate endpoints DO require admin.
- **CTF shell runs arbitrary WSL commands** (`/ctf/shell`) — admin-only, no bootstrap bypass;
  must NEVER be exposed in a multi-user / Railway deployment.
- **Python runner is not truly sandboxed** — subprocess + timeout only; runs as the same user.
- **Tiny token-overage race:** the account lock is released after `check_limit` and before the
  (slow) model call, so two same-account requests can both pass within ms (documented as acceptable).
- **Two managers share `koottam.db`** with separate Python-level locks; rely on WAL + busy_timeout
  for cross-manager coordination.
- **Router can misroute** simple requests to `task` (LLM judgement; learn fast-path is deterministic).
- **No agent-to-agent handoff** — once routed, the route runs to completion (Master agent is PLANNED).
- **Topic-bridge matching is heuristic** (synonym map + ≥2 shared words) — can miss or over-match.
- **`RUNTIME_SETTINGS` (model/theme) is global**, not per-user.
- Minor: duplicate keys in `_TOPIC_MAP` / `_STOP_WORDS` ("brand", "started") — harmless.

---

To refresh: re-run this prompt — it updates this file instead of re-scanning blind.
