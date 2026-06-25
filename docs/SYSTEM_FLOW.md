# SYSTEM_FLOW — Message Lifecycle

**Source of truth · Last updated: 2026-06-07**

> Exactly what happens to one message, end to end. Reflects the real code in
> `api/server.py` (`chat()`), with **PLANNED** items marked.

---

## Shared spine (every message)

```
input ─▶ reset usage log + start timer
      ─▶ ROUTER classifies: chat | task | code | learn   (default: chat)
      ─▶ continuation overrides (see below)
      ─▶ chosen route runs its agent flow
      ─▶ save conversation to memory.json
      ─▶ build metrics (time · calls · tokens · model) + add to session totals
      ─▶ RESPONSE: { route, messages[], model, metrics }
```

**Understanding note:** tone-reading and clarify-confirm are **not** a separate
step before the router today — they happen *inside* the chosen route (Assistant
reads tone on `chat`; Clarifier confirms on `task`; Tutor confirms on `learn`).
*Target (PLANNED): a unified understanding pass before routing.*

**Continuation overrides (before the route runs):**
- If the **last stored turn was the Tutor** → keep `chat`/`task` messages on the
  **learn** route (so "ready", "next" continue the lesson).
- Else if the **last stored turn was the Clarifier** → force `chat`/`task` to the
  **task** route (so the user's reply is treated as an answer).

---

## Route 1 — `chat`  (greeting / small talk / emotional thread)

```
message ─▶ Assistant (understanding layer)
            • loads recent history (last 6 turns)
            • notices mood, follows the thread, no repeated greeting
            • tone flexes; never fakes facts; stays short
        ─▶ one reply ─▶ memory ─▶ metrics
```
No Planner, no tools. This is where **friendly conversation finds the real
problem**; if a real need surfaces, the Assistant offers help and waits for a yes.

---

## Route 2 — `task`  (a real problem to solve)  ← confirm-before-solving lives here

```
message ─▶ CONFIRM STEP (Clarifier)
            effective = (original + reply) if answering a prior question
                        else the message itself
            ┌─ assess(effective) == "vague"  ─▶ Clarifier restates + asks ONE
            │     open question ─▶ STOP (wait for user)        ← no plan yet
            └─ assess(effective) == "clear"  ─▶ run pipeline:
                  (optional) Web Search ─▶ Planner ─▶ Executor
        ─▶ replies ─▶ memory ─▶ metrics
```

- **Confirm-before-solving:** a vague task (e.g. "my business is slow") is
  restated and questioned first; the Planner does **not** run until the combined
  task is judged clear. A repeated/still-vague reply asks again rather than guess.
- **Where web search enters:** inside the pipeline, **before** the Planner, and
  **only if the Search agent is enabled** (it is **off by default**). When on, its
  summary is passed to the Executor as context.
- Planner produces ordered steps → Executor carries them out using those steps
  (+ any search context).

---

## Route 3 — `code`  (explicit "write/run code")

```
message ─▶ Coding agent
            • LLM writes Python (strict: code only)
            • extract runnable code (parser-validated, prose stripped)
            • run_python() in subprocess (30s, output captured)
            • save to output/solution_*.py
        ─▶ reply (code + run result) ─▶ memory ─▶ metrics
```
No confirm step today (runs immediately). No Planner/Executor.

---

## Route 4 — `learn`  (teach me / explain / understand)

```
message ─▶ Tutor
            • FIRST CONTACT: confirm what they want, offer a path,
              invite "ready" ─▶ STOP (no lesson, no plan yet)
            • ALREADY STARTED (history shows they agreed): teach ONE
              small piece + a tiny example, then check in
        ─▶ reply ─▶ memory ─▶ metrics
```
Detected by a deterministic keyword fast-path ("teach me", "help me learn",
"explain X to me", …) or the LLM classifier. Continues via the Tutor
continuation override above. No Planner dump.

---

## Agent toggles (admin)
Planner / Executor / Coding / Search can be enabled/disabled; a disabled agent on
its route returns a "currently stopped" notice instead of running. The **Router
cannot be stopped**. Toggles affect the flows above in real time.

## Metrics (every route)
After the route runs: `total_time_seconds`, `api_calls` (every model call incl.
the router), `input/output/total_tokens` (Groq/Claude usage fields; Ollama
`prompt_eval_count`/`eval_count`), and `model`. Missing token data → `n/a`.
Running session totals accumulate across the process lifetime.
