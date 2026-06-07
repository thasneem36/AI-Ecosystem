# 🤖 AI Ecosystem

A local-first, multi-agent AI system. A **Router** classifies each message and sends it
down the cheapest path — small talk gets a one-line reply, real problems run a
**Planner → Executor** pipeline, and code requests go straight to a **Coding agent** that
writes and runs Python. Built on a FastAPI backend with a dark, ChatGPT-style
React + TypeScript frontend.

Models are pluggable: **Ollama** (`qwen3.5`, local) by default, plus **Groq** (hosted
Llama) and **Claude** if you add API keys. The local model is set in one place —
`OLLAMA_MODEL` in `config/settings.py`.

## Architecture

```
ai-ecosystem/
├── main.py              # backend entry point (uvicorn)
├── config/settings.py   # env-driven settings (Ollama / Groq / Claude)
├── agents/              # base, router, planner, executor, coding, search
├── tools/               # web search, file manager, code runner
├── memory/              # JSON-backed conversation memory
├── api/server.py        # FastAPI app + all endpoints
├── output/              # files created by the agents
├── tests/               # unit tests
└── frontend/            # React + TS + Tailwind (Vite)
```

### Request flow
```
message → Router (classify: chat | task | code)
            ├─ chat → Assistant  →  one short friendly reply
            ├─ task → [Web Search?] → Planner → Executor
            └─ code → Coding agent → runs code → saves to output/
          ↳ saved to memory/memory.json
```

### Agents
- **Router** — classifies intent (chat/task/code). Always on.
- **Planner** (yellow) — breaks a problem into ordered steps.
- **Executor** (cyan) — works through the plan and produces the answer.
- **Coding** (green) — writes Python, runs it, saves it to `output/`.
- **Web Search** (cyan) — pulls live DuckDuckGo results into task context.
  **Disabled by default** — enable it in Admin → Agent Control.

The terminal prints the routing decision per message, e.g. `🧭 Route: task`.

## Prerequisites
- Python 3.10+ and the bundled `venv`
- Node.js 18+
- [Ollama](https://ollama.com) running locally with the model pulled (for the default backend):
  ```bash
  ollama pull qwen3.5
  ```
  *(Or skip Ollama and use Groq/Claude by setting keys in `.env`.)*

## Setup & Run

**1. Install Python packages**
```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

**2. Install frontend packages**
```powershell
cd frontend; npm install
```

**3. Run the backend** (from the project root)
```powershell
.\venv\Scripts\python.exe main.py
```
API runs at http://127.0.0.1:8000 — interactive docs at http://127.0.0.1:8000/docs

**4. Run the frontend** (in a second terminal)
```powershell
cd frontend; npm run dev
```

**5. Open in browser**
👉 http://localhost:3000

> The Vite dev server proxies `/api/*` to the backend on port 8000, so both must be running.

## Models
Pick the model per-message in the chat input bar, or set the default in
**Admin → System Settings**. Keys are saved to `.env` and applied live (no restart).

| Model | Requires | Notes |
| --- | --- | --- |
| `ollama` | Ollama running locally | default, fully offline |
| `groq` | `GROQ_API` key | fast hosted Llama |
| `claude` | `ANTHROPIC_API_KEY` | Anthropic API |

## API endpoints
| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/chat` | Route + run the needed agents; returns `route` + messages |
| GET | `/history` · `/history/{id}` | Past conversations |
| GET | `/files` · `/files/{name}/download` | List / download agent-created files |
| GET | `/agents/status` | Live agent + system status |
| GET/POST | `/settings` | Get / update model + theme |
| POST | `/memory/clear` | Wipe conversation memory |
| GET/POST | `/admin/keys` | Read (masked) / save API keys to `.env` |
| GET | `/admin/dashboard` | Stats + 7-day activity |
| GET/POST | `/admin/agents` · `/admin/agents/{key}/{action}` | Status / start-stop agents |
| GET/POST | `/admin/users` · `/admin/users/{id}` | Demo users / block-unblock |

## Tests
```powershell
.\venv\Scripts\python.exe -m unittest tests.test_agents -v
```

## Configuration
Copy `.env.example` to `.env` and fill in values:
`OLLAMA_MODEL`, `OLLAMA_BASE_URL`, `GROQ_API`, `GROQ_MODEL`, `ANTHROPIC_API_KEY`,
`PORT`, `CORS_ORIGINS`, etc. The real `.env` is git-ignored.

## ⚠️ Security note — local use only
This is a **local-first prototype with no authentication**. Two things make it unsafe to
expose on a network:
- The **Coding agent runs LLM-generated Python** in a subprocess (timeout only — no sandbox).
- The **admin endpoints are unauthenticated** (anyone who can reach the API can change keys,
  toggle agents, etc.).

Keep the server bound to `127.0.0.1` (the default). Do not deploy as-is to a public host.
```
