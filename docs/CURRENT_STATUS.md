# AI Ecosystem — Current Status Snapshot

**Date:** 2026-06-07
**Purpose:** Factual snapshot of what actually exists in the code, for sharing with another AI.
**Legend:** ✅ WORKING · 🟡 PARTIAL · ⛔ BROKEN/LIKELY-FAILS · 📋 PLANNED

---

## 1. PROJECT STRUCTURE

```
ai-ecosystem/
├── main.py                       # Backend entry; reconfigures UTF-8 stdout, runs uvicorn
├── requirements.txt              # Python deps
├── .env / .env.example           # Config + secrets (real .env git-ignored)
├── .gitignore
├── README.md
├── config/
│   └── settings.py               # ALL config (models, backend, paths, CORS). Single source.
├── agents/
│   ├── base_agent.py             # Base class; _ollama_chat/_groq_chat/_claude_chat + think()
│   ├── router_agent.py           # RouterAgent (intent classifier) + ChatAgent (small-talk reply)
│   ├── planner_agent.py          # Breaks a task into ordered steps
│   ├── executor_agent.py         # Executes plan, optionally uses web-search context
│   ├── coding_agent.py           # Writes Python, runs it, saves to output/
│   └── search_agent.py           # Wraps web search; DISABLED by default
├── tools/
│   ├── code_runner.py            # run_python(): subprocess exec, 15s timeout
│   ├── file_manager.py           # save/read/list/delete files in output/ (path-traversal guard)
│   └── search.py                 # DuckDuckGo search via `ddgs`
├── memory/
│   ├── memory_manager.py         # JSON conversation store (save/get/count/clear)
│   └── memory.json               # Runtime data (git-ignored)
├── api/
│   └── server.py                 # FastAPI app: all endpoints + agent orchestration
├── output/                       # Files the coding agent creates
├── tests/
│   └── test_agents.py            # 15 unit tests (agents, router, tools, memory)
└── frontend/                     # React + TS + Vite + Tailwind
    ├── vite.config.ts            # Dev server :3000, proxies /api → :8000
    ├── tailwind.config.js        # Dark theme tokens (#0f0f0f / #1e1e1e / #00ff88)
    └── src/
        ├── index.tsx             # App entry (BrowserRouter)
        ├── App.tsx               # Routes (no auth; single console)
        ├── api.ts                # Typed API client (all backend calls)
        ├── components/
        │   ├── Sidebar.tsx       # Nav (Workspace + Admin)
        │   ├── ChatBox.tsx       # Message list + typing animation
        │   ├── Message.tsx       # Bubble w/ markdown + code highlighting
        │   ├── InputBar.tsx      # Text input, send, model selector
        │   └── AgentStatus.tsx   # Agent status dots
        └── pages/
            ├── user/
            │   ├── ChatPage.tsx      # Main chat (✅ connected)
            │   ├── HistoryPage.tsx   # Past conversations (✅ connected)
            │   └── FilesPage.tsx     # Files grid + preview/download (✅ connected)
            └── admin/
                ├── DashboardPage.tsx       # 🟡 STATIC demo data (recharts)
                ├── AgentControlPage.tsx    # Start/stop agents (✅ connected)
                ├── UserManagementPage.tsx  # 🟡 demo users (in-memory)
                └── SystemSettingsPage.tsx  # API keys + model (✅ connected)
```
> Note: `frontend/src/pages/user/SettingsPage.tsx` and any auth/login pages do **not** exist (removed).

---

## 2. WHAT WORKS (confirmed in code)

**Agents** (all subclass `BaseAgent`)
- ✅ Router intent classifier → `chat|task|code`, defaults to `chat` — `agents/router_agent.py`
- ✅ Assistant (small-talk one-liner) — `agents/router_agent.py` (`ChatAgent`)
- ✅ Planner (steps) — `agents/planner_agent.py`
- ✅ Executor (runs plan, accepts search context) — `agents/executor_agent.py`
- ✅ Coding (writes + runs + saves code) — `agents/coding_agent.py`
- ✅ Web Search agent (off by default) — `agents/search_agent.py`

**Model backends** — `agents/base_agent.py`
- ✅ Ollama (`_ollama_chat`), Groq (`_groq_chat`), Claude (`_claude_chat`); `think()` picks by `model` arg
- ✅ Graceful errors when a backend is unreachable

**Tools**
- ✅ Code runner (subprocess, 15s timeout) — `tools/code_runner.py`
- ✅ File manager (output/ CRUD, traversal-safe) — `tools/file_manager.py`
- ✅ Web search (ddgs) — `tools/search.py`

**Memory** — `memory/memory_manager.py`
- ✅ Saves every conversation to `memory/memory.json`; history/count/clear

**API endpoints** — `api/server.py`
- ✅ `POST /chat` (router → agents), `GET /history`, `GET /history/{id}`
- ✅ `GET /files`, `GET /files/{name}/download`, `GET /agents/status`
- ✅ `GET/POST /settings`, `POST /memory/clear`
- ✅ `GET/POST /admin/keys` (writes keys to .env, live, masked reads)
- ✅ `GET /admin/dashboard`, `GET /admin/agents`, `POST /admin/agents/{key}/{action}`
- ✅ `GET /admin/users`, `POST /admin/users/{id}`

**Frontend** (React+TS+Vite+Tailwind, connected via Vite proxy `/api`)
- ✅ ChatPage, HistoryPage, FilesPage, AgentControlPage, SystemSettingsPage — call real endpoints
- ✅ Per-message model selector (Ollama/Groq/Claude), seeded from `/settings`
- ✅ Routing decision printed in terminal (`🧭 Route: …`)

---

## 3. WHAT IS PARTIAL OR BROKEN

- ⛔ **Local model too slow** — `OLLAMA_MODEL=qwen3.5` (9.7B) exceeds `OLLAMA_TIMEOUT=120` and times out on this machine (verified live: ReadTimeout). Groq works fine. Cause: large model + low timeout.
- 🟡 **Admin Dashboard is static demo** — `DashboardPage.tsx` uses hardcoded `DEMO_*` constants; it does **not** call `GET /admin/dashboard` (that endpoint exists but is unused by the page).
- 🟡 **Dashboard "Recent Activity" & "System Resources"** — pure demo; no backend endpoints exist for them.
- 🟡 **User Management** — `USERS` is a 2-entry in-memory list in `server.py`; block/unblock resets on restart. No database, no real users.
- 🟡 **History "reopen"** — clicking a conversation opens a read-only modal; it does **not** reload it into the live chat.
- 🟡 **Web Search** — built but `enabled=False` by default; only runs on `task` route after being turned on in Agent Control.
- 🟡 **No authentication** — every `/admin/*` endpoint is open (auth was intentionally removed). Local-only.
- 🟡 **Code runner has no sandbox** — runs LLM-generated Python directly (timeout only). Safe locally, unsafe if exposed.

---

## 4. WHAT IS PLANNED BUT NOT BUILT

- 📋 Real user accounts + authentication + database (currently demo, in-memory)
- 📋 Live data wiring for the Dashboard (activity feed, CPU/memory/storage endpoints)
- 📋 A tone / sentiment / "understanding" layer — **does not exist** (only the intent router)
- 📋 Theme toggle (was in the original spec; not implemented)
- 📋 Reopen-a-past-conversation back into the active chat
- 📋 Sandboxing for the code runner; auth for admin endpoints (needed before any deployment)

---

## 5. TECH SETUP

**Ollama model (where it's set):**
- Default: `config/settings.py` **line 40** → `OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5")`
- Override: `.env` → `OLLAMA_MODEL=qwen3.5` (env wins over the default)
- Consumed by: `agents/base_agent.py` **line 36** (`"model": settings.OLLAMA_MODEL`)
- Default backend selector: `config/settings.py` line 55 → `DEFAULT_BACKEND = "ollama"`

**Python packages** (`requirements.txt`): `fastapi`, `uvicorn[standard]`, `requests`, `ddgs`, `colorama`, `python-dotenv`, `pydantic`. (Claude/Groq are called via plain `requests` — no vendor SDK.)

**Frontend stack:** React 18 + TypeScript + Vite + Tailwind CSS; libs: `react-router-dom`, `lucide-react`, `react-markdown`, `react-syntax-highlighter`, `remark-gfm`, `recharts`.
**Connected to backend?** ✅ Yes — Vite dev server (`:3000`) proxies `/api/*` → `http://127.0.0.1:8000`. All pages except the Dashboard use live data.

---

## 6. THE FLOW RIGHT NOW (real code path)

1. User types in **ChatPage** → `InputBar`; model is seeded from `GET /settings` (`DEFAULT_BACKEND`).
2. Frontend sends `POST /chat { message, model }` (`api.ts`).
3. `server.chat()` calls **`router.classify(message, model)`** → `chat | task | code` (defaults to `chat`); prints `🧭 Route:`.
4. Branch:
   - **chat** → `ChatAgent.run()` → one short reply.
   - **task** → *(if Web Search enabled)* `SearchAgent` → **Planner** → **Executor** (gets steps + search context).
   - **code** → `CodingAgent.run()` → extracts code → `run_python()` → saves to `output/`.
5. Each agent's `think()` → `_ollama_chat` / `_groq_chat` / `_claude_chat` based on `model`.
6. `memory_manager.save_conversation()` stores it; "problems solved" counter bumps only on task/code.
7. Returns `{ conversation_id, route, messages, model }`; frontend renders colored bubbles.

**Router** sits at the very start of `/chat` (step 3). **There is NO tone/understanding/sentiment layer** — intent classification is the only pre-processing.

---

## 7. TOP 5 THINGS TO FIX OR BUILD NEXT (priority order)

1. **Fix the local model timeout** — `qwen3.5` (9.7B) times out at 120s. Either switch `OLLAMA_MODEL` to a small model (e.g. `qwen2.5:3b` / `llama3.2:3b`) or raise `OLLAMA_TIMEOUT`. (Groq is the reliable path today.)
2. **Wire the Dashboard to real data** — make `DashboardPage` call `GET /admin/dashboard`; add endpoints for the activity feed + system resources (or remove those demo panels).
3. **Add persistence + auth** if multi-user is intended — replace the in-memory `USERS` list and protect `/admin/*`.
4. **Harden security before any non-local use** — sandbox `code_runner.py`, lock down admin endpoints (currently open by design).
5. **Finish UX gaps** — "reopen conversation into chat" from History, and the theme toggle from the original spec.
