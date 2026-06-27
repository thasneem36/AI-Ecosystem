# SYSTEM_FLOW — How a Message Flows Through Koottam

**Source of truth · Last updated: 2026-06-26**

> Exactly what happens to one message, end to end. Matches the real code in
> `api/server.py` (`chat()`), the agents in `agents/`, and `memory/memory_manager.py`.
> Entry points: the FastAPI `/chat` endpoint and `run_terminal.py` (which calls the
> *same* `chat()` — no duplicated logic). Status tags: **BUILT** / **PARTIAL** / **PLANNED**.

---

## 1. OVERVIEW

A message comes in with a `session_id` (the client echoes one back each turn).
First the **topic bridge** may ask "old topic or new?" if this looks like a
continuation of an earlier session. Then the **Router** classifies the message
into one of four routes — `chat`, `task`, `code`, `learn` — and only the agents
that route needs are run: `chat` → Assistant, `learn` → Tutor, `task` →
Clarifier → Planner → Executor, `code` → Coding agent. The turn is **saved to
memory** (SQLite, scoped to that `session_id`), and the **reply** is returned
with usage **metrics** (time, model calls, tokens, route). All of this is
**BUILT** unless noted.

---

## 2. THE FLOW, STEP BY STEP

### Shared spine (every message)

```
input ─▶ reset per-request usage log + start timer
      ─▶ TOPIC BRIDGE check (may short-circuit and ask "old or new?")
      ─▶ ROUTER classifies: chat | task | code | learn   (default: chat)
      ─▶ continuation overrides (Tutor → learn, Clarifier → task)
      ─▶ chosen route runs its agent(s)
      ─▶ on backend failure: replace output with a clean error, don't save
      ─▶ save turn to memory (SQLite, per session_id)
      ─▶ build metrics + add to running session totals
      ─▶ RESPONSE: { route, session_id, messages[], model, metrics }
```

### Topic bridge — "old topic or new?" — **BUILT**

Runs before the Router. Two phases:

- **Phase A — answering the bridge.** If the previous turn in this session was a
  bridge question, the user's reply is read as *old* vs *new*
  (`_interpret_topic_reply`, default **new** — old context is never loaded
  silently). "New" → fresh start. "Old" → load the matched session's turns into
  this session's history, then re-process the *original* message that triggered
  the bridge.
- **Phase B — first message of a new session.** Looks for a topically-similar
  past session (`_find_related_session`: keyword/synonym overlap, needs **≥ 2**
  shared content words). If found, it asks "picking up where we left off, or
  something new?" and **stops** for the reply.

**When it's skipped:**
- The session already has turns in memory (only fires on a brand-new session's
  first message).
- The user clicked **+ New chat** (`new_chat=True`) — they already declared this
  is new, so we never ask. *(New Chat never triggers the bridge on its first
  message.)*
- Fewer than 2 meaningful shared topic words → no related session → no bridge.

### Router — **BUILT**

`RouterAgent.classify()`:
1. **Deterministic learn fast-path** — phrases like "teach me", "help me learn",
   "explain X to me", "walk me through" return `learn` without an LLM call, so a
   teaching request never falls through to the task pipeline.
2. Otherwise the LLM classifies into exactly one word: `chat`, `task`, `code`,
   `learn`.
3. **Default when unsure** — anything unexpected or malformed parses to **`chat`**
   (the smallest, safest path).

**Continuation overrides** (applied right after classify):
- Last turn handled by the **Tutor** → force `chat`/`task` back to **`learn`**
  (so "ready" / "next" continue the lesson).
- Else last turn was a **Clarifier** question → force `chat`/`task` to **`task`**
  (the user's reply is the answer to that question).

### Route: `chat` → Assistant — **BUILT**

```
message ─▶ Assistant
            • loads THIS session's recent history (last 6 turns)
            • notices mood, follows the thread, never repeats a greeting
            • one short, warm reply; if a real need surfaces, offers help
              and WAITS for a yes
        ─▶ reply ─▶ memory ─▶ metrics
```
No Planner, no tools.

### Route: `learn` → Tutor — **BUILT**

```
message ─▶ Tutor (reads last 10 turns to know where the lesson is)
            • FIRST CONTACT: confirm what they want, offer a path,
              invite "ready" ─▶ STOP (no lesson, no multi-step plan)
            • ALREADY STARTED: teach ONE small piece + tiny example,
              then check in
        ─▶ reply ─▶ memory ─▶ metrics
```
Paced and conversational — waits for the user to say "ready" before teaching.

### Route: `task` → Clarifier → Planner → Executor — **BUILT**

```
message ─▶ CONFIRM STEP (Clarifier gate)
            effective = (original + prior answers + reply) if answering
                        a clarifying question, else the message itself
            ┌─ vague  ─▶ Clarifier restates + asks ONE open question
            │             ─▶ STOP and wait              (no plan yet)
            └─ clear  ─▶ run pipeline:
                  (optional) Web Search ─▶ Planner ─▶ Executor
        ─▶ replies ─▶ memory ─▶ metrics
```

- **Confirm before solving.** A brand-new request proceeds only if
  `Clarifier.assess()` ≠ "vague". After the user answers a question, the gate
  switches to the lenient `Clarifier.has_enough()` (biased toward proceeding).
- **No infinite loops.** At most **2** clarifying questions in a row
  (`MAX_CLARIFYING_QUESTIONS`); past that, the pipeline runs on whatever we have.
- **Web Search** (`agents/search_agent.py`) is **off by default** (PARTIAL —
  opt-in via Agent Control). When enabled it runs *before* the Planner and its
  summary is passed to the Executor as context.
- **Planner** emits 3–6 ordered steps; **Executor** carries them out (using the
  steps + any search context) and writes the final answer. If the Planner's
  backend call fails, the Executor is skipped and a clean error is returned.

### Route: `code` → Coding agent — **BUILT**

```
message ─▶ Coding agent ─▶ detect language
   ├─ Python  : LLM writes code ─▶ extract runnable code (parser-validated)
   │            ─▶ run_python() in a 30s subprocess, capture output
   │            ─▶ save output/solution_*.py ONLY if user asked to save
   │            ─▶ reply = description + code + run result
   └─ Web (HTML/CSS/JS): LLM writes a self-contained .html
                ─▶ ALWAYS save output/solution_*.html (never executed)
                ─▶ reply = description + "open it in your browser"
        ─▶ reply ─▶ memory ─▶ metrics
```
Python runs immediately; non-Python (web) is saved with instructions instead of
run. No Clarifier, no Planner/Executor on this route.

### Memory save — **BUILT**

`memory_manager.save_conversation()` writes one row per turn to **SQLite**
(`memory/koottam.db`), keyed by `session_id`. History reads are scoped strictly
to one `session_id`, so sessions never see each other's turns. Failed-backend
turns and clarifying questions are **not** counted as "problems solved"; failed
turns are **not** saved as conversations.

### Response — **BUILT**

Returns `{ conversation_id, session_id, route, messages[], model, metrics }`.
`metrics` = `total_time_seconds`, `api_calls` (every model call incl. the
router), `input/output/total_tokens`, and `model`. Tokens come from Ollama
(`prompt_eval_count`/`eval_count`) or Groq/Claude `usage` fields; when no backend
reports usage the totals are `n/a` (not a misleading 0). Running session-wide
totals accumulate for the lifetime of the process.

---

## 3. TEXT FLOW DIAGRAM

```
                         ┌────────────────────────────┐
   user message  ─────▶  │  reset usage log + timer   │
   (+ session_id)        └─────────────┬──────────────┘
                                       ▼
                         ┌────────────────────────────┐
                         │       TOPIC BRIDGE?         │
                         │  new session, 1st message,  │
                         │  not "+ New chat",          │
                         │  ≥2 shared topic words      │
                         └──────┬──────────────┬───────┘
                          yes   │              │  no / skipped
                                ▼              ▼
                   ask "old or new?"     ┌───────────┐
                   STOP, wait reply      │  ROUTER   │  default → chat
                                         └─────┬─────┘
                  continuation overrides:      │
                  Tutor→learn, Clarifier→task  │
            ┌────────────┬────────────────┬────┴──────────┐
            ▼            ▼                ▼                ▼
          chat         learn            task             code
            │            │                │                │
        Assistant      Tutor        Clarifier gate     Coding agent
       (1 reply,    (confirm,      ┌───┴────┐         ┌──┴─────────┐
        reads        wait        vague     clear      Python      Web
        history)     "ready")      │         │          │          │
            │            │        STOP   (Search?)→   run +      save
            │            │       ask Q    Planner→    save?      .html
            │            │               Executor      │          │
            └────────────┴────────────────┴────────────┴──────────┘
                                       ▼
                         ┌────────────────────────────┐
                         │  save turn → SQLite (sid)   │
                         │  build metrics + totals     │
                         └─────────────┬──────────────┘
                                       ▼
                    RESPONSE { route, messages[], model, metrics }
```

---

## 4. KEY RULES BAKED INTO THE FLOW

- **Tone flexes, truth doesn't.** Agents adapt warmth to the user's mood but
  never soften or distort facts (`ChatAgent` / `TeachAgent` system prompts).
- **Confirm before solving vague tasks.** The `task` route gates on the Clarifier
  — a vague request is restated and questioned before any Planner runs.
- **Simple requests stay in chat.** Greetings/small talk and the safe default
  resolve in `chat` with one reply — they don't enter the task pipeline.
- **New Chat never triggers the topic bridge on the first message.**
  `new_chat=True` skips the "old or new?" check entirely.

---

## 5. KNOWN GAPS (honest)

- **Router sometimes misroutes simple requests to `task`.** The LLM classifier
  isn't perfect; a casual line can occasionally land on `task` and hit the
  Clarifier instead of staying in `chat`. The learn fast-path is deterministic,
  but chat/task/code is model-judged. **PARTIAL.**
- **No agent-to-agent handoff yet.** Once the Router commits to a route, that
  route runs to completion — an agent can't pass work mid-stream to another route
  (e.g. a `task` Executor can't hand a code step to the Coding agent; it only
  *describes* what to code). Cross-route handoff is **PLANNED for Phase 3**.
- **A unified "understanding" pass before routing is PLANNED.** Today tone-reading
  and clarify/confirm live *inside* each route (Assistant on `chat`, Clarifier on
  `task`, Tutor on `learn`), not as one step before the Router.
- **Web Search is opt-in (off by default).** **PARTIAL** — enable it in Agent
  Control to add live context to the `task` pipeline.
```
