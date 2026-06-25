# BLUEPRINT — Target Architecture

**Source of truth · Last updated: 2026-06-07**

> This describes what the system SHOULD be (the target). Each piece is tagged:
> **BUILT** (in code today), **PARTIAL** (exists but incomplete), **PLANNED**
> (designed, not yet built). Where today's code differs from this target, the
> code should move toward the blueprint — see "Known gaps" at the bottom.

---

## Layered architecture (target)

```
        ┌──────────────────────────────────────────────────────────┐
USER ──▶ │ 1. UNDERSTANDING  (tone read + clarify/confirm)          │
        │ 2. ROUTING        (chat | task | code | learn)            │
        │ 3. AGENTS         (Assistant / Clarifier / Tutor /        │
        │                    Planner / Executor / Coding / Search)  │
        │ 4. TOOLS          (web search · code runner · files)      │
        │ 5. MEMORY         (conversation history, JSON)            │
        └──────────────────────────────────────────────────────────┘
                       ▼
        RESPONSE  +  USAGE METRICS (time · calls · tokens · model)
```

### 1. Understanding layer
- **Responsibility:** read the user's tone/mood and decide whether the request is
  clear enough to act on; confirm before solving when it isn't.
- **Today:** **PARTIAL.** There is no single pre-router understanding pass.
  Instead, understanding is split by route: tone/mood + thread-following live in
  the **Assistant** (chat route); clarify-and-confirm lives in the **Clarifier**
  (task route) and the **Tutor** (learn route).
- **Target:** the principles (tone, clarify-confirm) apply consistently across
  all routes, not only chat/task.

### 2. Routing layer — **BUILT**
- **Responsibility:** classify each message into exactly one of `chat | task |
  code | learn` and send it down the cheapest correct path.
- **Today:** `RouterAgent.classify()` — a deterministic keyword fast-path for
  learning requests, otherwise an LLM one-word classification. Defaults to `chat`
  on anything uncertain. Continuation rules keep an active lesson (`learn`) or a
  pending clarification (`task`) on-track.

### 3. Agents layer — **BUILT** (see AGENTS.md for each)
- Router · Assistant · Clarifier · Tutor · Planner · Executor · Coding · Search.
- Each has one job, runs only on its route/condition, and follows the rules in
  `docs/AGENTS.md`.

### 4. Tools layer
- **Code runner** — **BUILT.** Executes generated Python in a subprocess (30s
  timeout, output captured, interrupts handled).
- **File manager** — **BUILT.** Saves/reads/lists agent-created files in `output/`.
- **Web search** — **BUILT (opt-in).** DuckDuckGo via `ddgs`; wrapped by the
  Search agent, **disabled by default**.

### 5. Memory layer — **BUILT**
- **Responsibility:** persist every conversation so agents can follow the thread.
- **Today:** `MemoryManager` → `memory/memory.json` (save / history / count /
  clear). Recent history is fed into Assistant, Tutor, and Clarifier.
- **PLANNED:** a real database + per-user memory (no accounts yet).

### Cross-cutting: usage metrics — **BUILT**
- Every model call records tokens; each response returns `{ total_time_seconds,
  api_calls, input_tokens, output_tokens, total_tokens, model }`, plus running
  session totals. Surfaced in the terminal and (archived) web UI.

---

## Dual-brain model strategy

The system is **model-agnostic** behind one method (`BaseAgent.think`), with three
backends:

| Backend | Model (default) | Use | Status |
|---|---|---|---|
| **Ollama** (local) | `qwen3.5` | offline, private, free | **BUILT** (slow on big models) |
| **Groq** (hosted) | `llama-3.3-70b-versatile` | fast iteration / default | **BUILT** |
| **Claude** (paid) | `claude-*` | hardest problems (later) | **BUILT** (needs key) |

**Single source of truth:** `config/settings.py` →
`OLLAMA_MODEL`, `GROQ_MODEL`, `CLAUDE_MODEL`, and `DEFAULT_BACKEND` (currently
`groq`). Switching the default model/backend is a one-line change there.

**How the system decides which to use:**
- **Today (PARTIAL):** it uses `DEFAULT_BACKEND` unless the caller specifies a
  model per message. There is **no automatic selection**.
- **Target (PLANNED):** route by need — local Ollama for cheap/private/simple
  turns, Groq for speed, Claude for the hardest tasks; fall back automatically if
  a backend is down or a key is missing.

---

## Component responsibilities (one line each)

| Component | Responsibility | Status |
|---|---|---|
| `config/settings.py` | One place for models, backend, keys, paths | BUILT |
| `agents/base_agent.py` | Talk to any backend; track status + usage | BUILT |
| `agents/router_agent.py` | Classify intent; hold Assistant + history helper | BUILT |
| `agents/clarifier_agent.py` | Judge vague/clear; confirm before tasks | BUILT |
| `agents/teach_agent.py` | Conversational tutoring (learn route) | BUILT |
| `agents/planner_agent.py` | Break a task into ordered steps | BUILT |
| `agents/executor_agent.py` | Carry out the plan, use search context | BUILT |
| `agents/coding_agent.py` | Write + run + save Python | BUILT |
| `agents/search_agent.py` | Live web context (opt-in) | BUILT (off by default) |
| `tools/` | search · code_runner · file_manager | BUILT |
| `memory/memory_manager.py` | Persist conversations to JSON | BUILT |
| `api/server.py` | Orchestrate the pipeline; HTTP endpoints | BUILT |
| `run_terminal.py` | Terminal client reusing the same pipeline | BUILT |
| Accounts / auth / DB | Multi-user identity + durable storage | PLANNED |
| Auto model-selection | Choose backend by need/cost/availability | PLANNED |
| Pre-router understanding | Unified tone+confirm before routing | PLANNED |

---

## Known gaps (code vs blueprint)
1. Understanding is per-route, not a unified layer before the router.
2. Tone-flex is implemented only in the Assistant (chat), not all routes.
3. Model selection is a fixed default, not need-based auto-selection.
4. Web search is opt-in/manual, not automatically triggered by task need.
5. No accounts/DB; memory is a single shared JSON file.
