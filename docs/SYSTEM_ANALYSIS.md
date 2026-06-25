# SYSTEM ANALYSIS — Engineering Audit

**Date: 2026-06-07** · Based on a full read of the real code (`agents/`, `tools/`,
`api/`, `config/`, `memory/`, `run_terminal.py`, `main.py`, `requirements.txt`,
`archive/frontend/`). Every claim below points at actual code, not the design docs.

Status legend: **BUILT** (works in code) · **PARTIAL** (exists, incomplete) ·
**PLANNED** (not built). Verdict at the very bottom.

---

## 1. SYSTEM OVERVIEW

### What it actually is
A single-process FastAPI app that takes one chat message, classifies it, and runs
the minimum agents needed to answer it. Agents are thin wrappers over one LLM call
each (`BaseAgent.think`). Three backends sit behind one method: **Ollama** (local),
**Groq** (hosted, default), **Claude** (paid, untested). Conversations are appended
to one JSON file. A terminal client (`run_terminal.py`) calls the same `chat()`
function in-process. A React frontend exists but is **archived** (not running).

### Real message flow (from `api/server.py:chat`)
```
POST /chat {message, model}
 │
 ├─ reset_usage()  +  start timer                        (base_agent.py)
 ├─ router.classify(message)         → chat | task | code | learn   (1 LLM call)
 ├─ continuation overrides (read memory.json, last turn):
 │     last turn == Tutor      → force "learn"
 │     last turn == Clarifier  → force "task"
 ├─ branch:
 │   chat  → assistant.run(history)                              (1 call)
 │   task  → clarifier.assess(effective)                         (1 call)
 │             vague  → clarifier.run() → STOP (ask, wait)       (+1 call)
 │             clear  → [search?] → planner → executor           (+2–3 calls)
 │   code  → coder.run() → extract → run_python() → save         (1 call + exec)
 │   learn → tutor.run(history)                                  (1 call)
 ├─ build metrics from usage log; add to SESSION_STATS
 ├─ memory_manager.save_conversation(...)   (read whole file, append, rewrite)
 └─ return {route, messages[], model, metrics}
```
So a *clear* task = **4 LLM calls** (router + assess + planner + executor); a vague
one = **3** (router + assess + clarify). `chat`/`learn`/`code` = router + 1.

---

## 2. WHAT WORKS (confirmed in code)

| Feature | File | Status |
|---|---|---|
| Intent routing (LLM + deterministic `learn` fast-path, safe default `chat`) | `agents/router_agent.py` | ✅ BUILT |
| Backend abstraction (ollama/groq/claude behind `think`) | `agents/base_agent.py` | ✅ BUILT |
| Per-request token/latency metrics via ContextVar | `agents/base_agent.py`, `api/server.py:_build_metrics` | ✅ BUILT |
| Confirm-before-solving on the combined (original+reply) task | `api/server.py:161-177`, `clarifier_agent.py` | ✅ BUILT |
| Lesson continuation (`_in_teaching_session`) + clarify continuation (`_awaiting_task_confirmation`) | `api/server.py:462-490` | ✅ BUILT (single-user) |
| Code extraction validated by `compile()`, prose stripped | `agents/coding_agent.py:_extract_code` | ✅ BUILT |
| Code runner: subprocess, 30s timeout, kill+drain, interrupt-traceback scrub | `tools/code_runner.py` | ✅ BUILT (not sandboxed) |
| File save/list/download with traversal guard (`_safe_path`) | `tools/file_manager.py`, `api/server.py:232-240` | ✅ BUILT |
| JSON memory: save/history/get/count/clear, thread-locked writes | `memory/memory_manager.py` | ✅ BUILT |
| Real admin dashboard (memory count, session calls/tokens, 7-day series) | `api/server.py:330-349` | ✅ BUILT |
| Real host metrics via psutil | `api/server.py:352-371` | ✅ BUILT |
| Agent start/stop (router protected) | `api/server.py:378-394` | ✅ BUILT |
| API keys persisted to `.env` + applied live, masked on read | `api/server.py:290-326` | ✅ BUILT (unauthenticated) |
| Honest "1 (local)" user, no fabricated data | `api/server.py:84-86,348` | ✅ BUILT |
| Web search agent, opt-in, fail-soft | `agents/search_agent.py`, `tools/search.py` | ✅ BUILT (OFF default) |
| Terminal client reusing the real pipeline | `run_terminal.py` | ✅ BUILT |
| Unit tests (parsers, extraction, runner, memory, agents) | `tests/test_agents.py` | ✅ BUILT (~24 cases) |
| `.env` git-ignored; `.env.example` template | `.gitignore`, `.env.example` | ✅ BUILT |

---

## 3. WHAT'S WRONG / MISTAKES (blunt)

### 🔴 Bugs / broken logic

1. **`DEFAULT_BACKEND` is bypassed by the HTTP API.**
   `ChatRequest.model` defaults to `"ollama"` (`server.py:98`). Then
   `model = req.model or RUNTIME_SETTINGS["model"]` (`server.py:132`). Because
   `"ollama"` is truthy, the `or` fallback is **dead code** — any `/chat` call that
   omits `model` runs **Ollama**, not the configured `groq` default. Ollama on the
   9.7B model is the exact thing that times out, so a raw API client gets the slow,
   failing path. Only `run_terminal.py` (which reads `settings.DEFAULT_BACKEND`
   directly) and the archived frontend (which seeds from `GET /settings`) dodge this.

2. **Shared agent singletons are not concurrency-safe.** One instance of each agent
   is shared across all requests (`server.py:53-60`). `chat()` is a **sync** FastAPI
   handler → runs in a threadpool → two simultaneous requests mutate the same
   `self.status`/`self.last_activity`. Worst case is the **Clarifier**: `run()`
   swaps `self.system_prompt` and restores it in `finally` (`clarifier_agent.py:60-65`).
   Concurrent calls can restore one request's prompt while another is mid-`think()`,
   sending the *judge* prompt where the *clarify* prompt was meant (or vice-versa).
   The ContextVar fixes usage isolation but **not** this.

3. **Continuation state is global, not per-conversation.** `_awaiting_task_confirmation`,
   `_in_teaching_session`, and `_clarified_task` all key off `get_history()[0]` — the
   single newest record in one shared file (`server.py:462-490`). With two users (or
   two tabs) the "last turn was Clarifier/Tutor" check reads the *other* person's
   turn. Confirm/teach continuation silently misfires the moment usage isn't strictly
   one-at-a-time.

4. **LLM error strings are treated as valid answers.** On any failure `think()`
   returns `"[Name] Error: …"` (`base_agent.py:170-173`). That string then flows on
   as a normal reply: Planner feeds it to `_parse_steps` (becomes a bogus "step"),
   Executor runs on it, it's shown to the user, **saved to memory, and counted as a
   solved problem**. No detection of the error sentinel anywhere.

5. **"Problems solved" counts pipeline runs, not solutions.** `did_work=True` bumps
   the counter for any task/code route even if the code crashed, the executor errored,
   or nothing useful came out (`server.py:153-177, 527-529`). The dashboard label
   says "problems solved today" — it's really "pipelines fired today."

6. **Wrong/placeholder Claude model id.** `CLAUDE_MODEL = "claude-opus-4-8"`
   (`settings.py:65`, `.env.example`) is not a valid Anthropic model name. The
   `claude` backend is wired but would 404 on first call — so it's **PARTIAL**, not
   BUILT, despite being marked BUILT in BLUEPRINT.md.

7. **API timeout is misnamed and shared.** `_groq_chat` and `_claude_chat` both pass
   `timeout=settings.OLLAMA_TIMEOUT` (`base_agent.py:98,125`). Lowering the *Ollama*
   timeout silently throttles the hosted APIs too. Coupled, confusing config.

### 🟠 Bad patterns / fragility

8. **GET with side effects.** `GET /agents/status` *mutates* agent statuses (revives
   them) on read (`server.py:250-252`). A status poll changes state — non-idempotent.

9. **Whole-file memory on the hot path.** Every `chat()` reads `memory.json` in full
   several times (router overrides + route handler + save), and `get_history()` sorts
   the entire list each call (`memory_manager.py:59-62`). Fine at 10 records, painful
   at 10,000.

10. **`_largest_compilable` is O(n²) in lines, each iteration calling `compile()`**
    (`coding_agent.py:101-119`). A long, mostly-prose model reply makes code
    extraction slow.

11. **No validation of `model` field.** `model="anything"` falls through to Ollama via
    `_model_name` (`base_agent.py:136-141`). Silent rather than a 422.

12. **Claude `max_tokens` hard-coded to 1024** (`base_agent.py:121`) — will truncate
    longer answers without warning.

### ⚠️ Where code contradicts the system's own design

13. **"Local-first / private" vs `DEFAULT_BACKEND="groq"`.** VISION.md sells
    local-first privacy as the character of the system, but out of the box every
    message is sent to a hosted third-party API. Defensible for speed, but it is a
    direct contradiction of the stated principle and should be named as a deliberate
    trade-off, not hidden.

14. **"Lead with conversation to find the real problem"** (VISION principle #1) is not
    enforced — only the `chat` route converses. A message the Router calls `task`
    jumps straight to assess→plan→execute. The principle is real only when routing
    happens to pick `chat`.

15. **Already documented gaps** (BLUEPRINT "Known gaps") that the code confirms:
    understanding is per-route not pre-router; tone-flex lives only in the Assistant;
    no auto model-selection; web search is manual; no accounts/DB.

---

## 4. WHAT NEEDS IMPROVING (prioritized)

| Pri | Area | Problem | Fix direction |
|---|---|---|---|
| **High** | Concurrency | Shared singleton agents + mutable `system_prompt` swap + global counters race | Make agents stateless per-call (pass system prompt into `think`), or instantiate per request; guard `SESSION_STATS` with a lock |
| **High** | Session scoping | Continuation/memory are global, single-file | Add a `session_id`/`conversation_id` and scope history + continuation to it |
| **High** | Error handling | Error sentinels treated as answers | Have `think()` raise (or return a typed result); short-circuit the pipeline and don't count/save failures as solutions |
| **High** | Backend default | HTTP default ≠ configured default | Make `ChatRequest.model` default `None`; resolve to `RUNTIME_SETTINGS["model"]` |
| **Med** | Token cost | 4 LLM calls per clear task | Cache/merge router+clarifier; skip `assess` when the message is obviously clear; consider one combined "route+clarity" call |
| **Med** | Memory I/O | Full read+sort+rewrite per turn | Keep an in-memory index, append-only log, or SQLite |
| **Med** | Prompts | Planner/Executor never see search context together; `needs_code` never auto-invokes Coding | Pass search to Planner; chain Executor→Coding when `needs_code` |
| **Med** | Claude path | Invalid model id, untested | Set a real model id; add one live smoke test |
| **Low** | REST hygiene | GET mutates state; no model validation; magic numbers | Move revive logic out of GET; validate `model`; lift constants to settings |
| **Low** | Observability | `print()` logging only | Use `logging` with levels; structured route/latency logs |

---

## 5. WHAT WILL BREAK OR GO WRONG (risks)

### Security
- **RCE by design, unauthenticated.** `route == "code"` runs model-generated Python
  via `subprocess` immediately, no confirm, **no sandbox** (`code_runner.py`,
  `server.py:153-156`). Anyone who can POST `/chat` can run arbitrary code on the
  host. Today it's bound to `127.0.0.1`, so it's local-only — but the moment `HOST`
  becomes `0.0.0.0` or it's put behind a tunnel, it's remote code execution. The
  runner's own docstring says "trusted use only," yet it's wired to an HTTP route.
- **All `/admin/*` endpoints are open.** `POST /admin/keys` writes arbitrary values
  into `.env` on disk and into `os.environ` live (`server.py:302-326`); agent
  start/stop, user-block, and key status have **zero auth**. Local-only saves it for
  now; exposed, it's a takeover.
- **Prompt-injected code execution.** A `code` request can ask the model to write
  Python that exfiltrates files or hits the network; the runner has full FS/network
  access and only a 30s timeout.

### Performance / cost
- **Latency stacks per route.** A clear task is 4 sequential network round-trips.
  On Ollama with the big local model these **time out** (`OLLAMA_TIMEOUT=120` each).
- **Token spend is silent.** Every task pays for router + clarifier even before any
  real work; nothing budgets or caps tokens.
- **Memory file is a future bottleneck** (full read/sort/rewrite per message, several
  times per request).

### Correctness under unexpected input
- **Concurrency corrupts continuation** (global last-turn) and **agent state**
  (shared singletons) — see §3.2/§3.3.
- **`explain … to me` fast-path over-fires.** "explain this error to me" force-routes
  to `learn` even when the user wants a fix (`router_agent.py:37`).
- **A backend hiccup becomes the user's answer** and is persisted/counted (§3.4).

---

## 6. IF IMPROVED, WILL IT WORK? (honest verdict)

| Weakness | If fixed properly… | Approach sound? |
|---|---|---|
| Backend default bug | Trivial, fully fixed | ✅ Patch |
| Error sentinels as answers | Typed results + short-circuit → clean | ✅ Patch |
| Shared-singleton races | Stateless agents / per-request instances → solid | ✅ Patch (small refactor) |
| Global continuation state | Needs real `session_id` threading + scoped memory | ✅ Works, but it's a **structural** change, not a one-liner |
| JSON memory at scale | SQLite/append-log → fine to thousands of convos | ⚠️ **Rebuild the layer** beats patching JSON |
| Unauthed admin + RCE | Auth + real sandbox (container/seccomp/resource limits) or drop auto-exec | ⚠️ Sandbox is a **rebuild**, not a patch; bolt-on guards won't be safe |
| 4-calls-per-task cost | Merge router+clarity, skip-when-clear → cheaper | ✅ Patch |
| Auto model-selection (PLANNED) | A real router-by-need is achievable on this abstraction | ✅ The `think(model=…)` seam already supports it |
| "Local-first" identity | Default to Ollama + opt-in cloud, OR rewrite the VISION claim | ✅ Config + honesty, not code rebuild |

**Bottom line on the approach:** the *architecture is sound for a single local
user* and most issues are patches on a good seam (`think(prompt, model)`). The two
things that need **rebuilds, not patches**, are (a) the **persistence/session layer**
(JSON + global "last turn" cannot survive multi-user) and (b) the **code-execution
sandbox** (subprocess+timeout is not a security boundary). Don't patch those —
replace them when you go past one local user.

---

## 7. TOP PRIORITIES (ordered)

1. **Fix the backend-default bug** (`ChatRequest.model = None` → resolve to runtime
   default). One line; today the HTTP API silently runs the slow/timeout path.
2. **Stop treating LLM errors as answers.** Detect failure in `think()`, short-circuit
   the pipeline, never save/count a failed run. Prevents garbage plans and a lying
   "problems solved" counter.
3. **Make agents concurrency-safe.** Remove the Clarifier `system_prompt` swap (pass
   the prompt per call) and stop sharing mutable state across requests. Lock
   `SESSION_STATS`. This is the difference between "works in a demo" and "works."
4. **Add `session_id` and scope memory + continuation to it.** Required before *any*
   second concurrent user; fixes the global "last turn" misfire.
5. **Lock down execution & admin before any network exposure.** Auth on `/admin/*`
   and `/chat`; real sandbox or an explicit confirm + resource limits on the code
   runner. Cheapest interim: keep it bound to `127.0.0.1` and document that loudly.
6. **Cut task cost from 4 calls.** Skip `assess` when clearly actionable; consider a
   single combined route+clarity call. Directly cuts latency and token spend.
7. **Replace JSON memory with SQLite** (or append-only log + index) once history
   grows or sessions multiply. Removes the per-request full-file read/sort/rewrite.
8. **Decide the local-first story.** Either default to Ollama (honor VISION) or update
   VISION to say "cloud-first by default, local optional." Don't let the doc and the
   default contradict each other.

---

### Honesty notes on prior docs
- BLUEPRINT.md marks **Claude = BUILT**; code shows it's **PARTIAL** (invalid model id,
  unauthenticated, untested live).
- Several layers marked BUILT are BUILT *for one local user only* — none are
  concurrency- or multi-user-safe yet. That caveat belongs on every "BUILT" above.
