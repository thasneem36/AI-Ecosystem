"""
CTF agent loop — personal use only.

Wraps tools/ctf_shell.run_shell_command in three modes:
  auto   — LLM decides + runs each command automatically.
  manual — LLM decides, user approves each command before it runs.
  learn  — user types commands; LLM teaches on real output.

Knowledge retrieval: before each run, relevant chunks from ChromaDB
(cybersecurity module) are injected as reference context for the LLM.

Stop conditions: flag_found, task_complete, stuck, max_steps, user_quit, error.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Optional

# ── sys.path fix for direct script execution ──────────────────────────────────
# `python agents/ctf_agent.py` puts agents/ on sys.path, not the project root.
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from agents.base_agent import BaseAgent
from config.settings import settings
from tools.ctf_shell import run_shell_command

# Force UTF-8 on Windows cp1252 consoles (same fix as api/server.py)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

# ── Constants ─────────────────────────────────────────────────────────────────
_MODEL_LIMIT   = 3000   # chars of stdout/stderr sent to the LLM per step
_DISPLAY_LIMIT = 4000   # chars shown in the terminal (trace always stores full output)

# ── System prompt — auto / manual (solving) ───────────────────────────────────
_SOLVE_PROMPT = """\
You are an expert CTF (Capture The Flag) and penetration-testing agent running
inside Kali Linux (WSL) as root.  All targets are CTF challenges, HackTheBox
(HTB), TryHackMe (THM), or isolated lab machines the user has explicit
authorisation to attack.  You are cleared to proceed.

MISSION
Solve the given challenge step by step — one command at a time.
Work methodically: enumerate → identify → exploit → post-exploit → capture flag.

HARD RULES
1. Reason ONLY on real output you actually receive.  Never invent or fabricate
   tool output, answers, or flags.
2. flag_found=true ONLY when a CTF flag string (e.g. flag{…}, HTB{…}, THM{…})
   LITERALLY appears in the real command output returned to you this turn.
   Fabricating a flag is an automatic failure.
3. task_complete=true ONLY for non-flag tasks (e.g. "scan and report open ports")
   when real output has already given you the full answer and there is nothing
   more to do.  Set final_answer to the concise answer drawn from real output.
   Never set task_complete=true from assumptions — only from real output.
4. Each response must contain EXACTLY ONE shell command in the "command" field.
5. If you are completely out of ideas, set stuck=true.

OUTPUT FORMAT — respond with ONLY this JSON, no markdown fences, no extra text:
{
  "reasoning":     "<step-by-step reasoning: what last output means, why this next command>",
  "command":       "<exactly one shell command>",
  "flag_found":    <true | false>,
  "flag":          "<flag string if found in real output, else null>",
  "task_complete": <true | false>,
  "final_answer":  "<concise answer if task_complete=true, else null>",
  "stuck":         <true | false>
}
"""

# ── System prompt — learn mode ────────────────────────────────────────────────
_LEARN_PROMPT = """\
You are an expert CTF/pentesting coach.  The student is working on a challenge
in Kali Linux (WSL).  You guide them — you do NOT run commands yourself.

YOUR ROLE
• Explain what real command output means in plain English.
• Help the student reason about what to try next and why.
• When they type "hint", give a more direct pointer (name the tool or technique).
• If a flag appears in the output, confirm it and explain where it came from.
• Only teach from real output that was returned — never fabricate.
• Keep explanations concise; avoid information overload.
"""

# Regex for auto-detecting common CTF flag formats in learn mode.
# Extend this list if your challenges use a different format.
_FLAG_RE = re.compile(
    r'(?:flag|HTB|THM|ctf|picoCTF)\{[^}]+\}',
    re.IGNORECASE,
)


# ── Groq caller — multi-turn, full messages list ──────────────────────────────
def _call_groq(messages: list[dict], json_mode: bool = True) -> str:
    if not settings.GROQ_API_KEY:
        raise RuntimeError("GROQ_API key not set — add  GROQ_API=gsk_...  to .env")
    payload: dict = {
        "model":       settings.GROQ_MODEL,
        "messages":    messages,
        "temperature": 0.2,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    resp = requests.post(
        f"{settings.GROQ_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


# ── Knowledge retrieval — reuses the same ChromaDB collection as /knowledge/search
def _retrieve_kb(query: str, n: int = 3) -> str:
    """Query ChromaDB for relevant knowledge chunks.

    Uses the exact same _collection() and query API as the /knowledge/search
    endpoint.  Returns a formatted string of top results (score >= 0.35), or ""
    if the DB is empty, unreachable, or nothing is relevant enough.
    """
    try:
        from api.knowledge import _collection   # existing collection accessor
        col = _collection()
        total = col.count()
        if total == 0:
            return ""
        res = col.query(
            query_texts=[query],
            n_results=min(n, total),
            include=["documents", "metadatas", "distances"],
        )
        chunks: list[str] = []
        for doc, meta, dist in zip(
            res["documents"][0],
            res["metadatas"][0],
            res["distances"][0],
        ):
            score = max(0.0, 1 - float(dist))
            if score < 0.35:            # skip unrelated content
                continue
            source = meta.get("name") or meta.get("filename") or "kb"
            chunks.append(f"[{source}] {doc.strip()}")
        return "\n\n".join(chunks)
    except Exception:
        return ""                        # retrieval is a helper, never block the agent


# ── JSON parser — handles stray markdown fences and leading text ──────────────
def _parse_reply(raw: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Cannot parse JSON from agent reply:\n{raw[:600]}")


# ── Output helpers ────────────────────────────────────────────────────────────
def _truncate(text: str, limit: int = _DISPLAY_LIMIT) -> str:
    if len(text) <= limit:
        return text
    half    = limit // 2
    omitted = len(text) - limit
    return text[:half] + f"\n...[output truncated — {omitted} chars omitted]...\n" + text[-half:]


def _print_step(step: int, reasoning: str, command: str) -> None:
    print(f"\n{'━' * 66}")
    print(f"  STEP {step}")
    print(f"{'━' * 66}")
    print(f"[Reasoning]\n{reasoning}")
    print(f"\n[Command]  {command}")


def _print_result(res: dict) -> None:
    label = "TIMEOUT" if res["timed_out"] else str(res["exit_code"])
    print(f"\n[Exit code]  {label}")
    if res["stdout"]:
        print(f"[stdout]\n{_truncate(res['stdout'])}")
    if res["stderr"]:
        print(f"[stderr]\n{_truncate(res['stderr'])}")


def _flag_in_output(flag: str, stdout: str, stderr: str) -> bool:
    return flag in stdout or flag in stderr


# ── Learn mode ────────────────────────────────────────────────────────────────
def _run_learn_mode(task: str, max_steps: int) -> dict:
    """User types commands; LLM teaches on real output."""
    print(f"\n{'═' * 66}")
    print(f"  CTF AGENT  |  mode=learn  max_steps={max_steps}")
    print(f"  Task: {task}")
    print(f"  Commands: type a shell command and press Enter.")
    print(f"  'hint' → bigger nudge   'quit' → stop")
    print(f"{'═' * 66}")

    # Retrieve relevant knowledge to inform the teaching orientation.
    kb_ctx = _retrieve_kb(task)
    kb_section = (
        f"\n\nRelevant reference material (use to inform your teaching):\n{kb_ctx}"
        if kb_ctx else ""
    )

    messages: list[dict] = [
        {"role": "system", "content": _LEARN_PROMPT},
        {
            "role": "user",
            "content": (
                f"Task: {task}\n\n"
                "Give the student an orientation: what kind of challenge this looks like, "
                "what general approach and tools are relevant, and a good starting point. "
                "Don't solve it — just orient them so they know where to begin."
                f"{kb_section}"
            ),
        },
    ]

    trace: list[dict] = []
    flag:         Optional[str] = None
    stopped_reason               = "max_steps"
    step                         = 0

    # Initial orientation
    try:
        orientation = _call_groq(messages, json_mode=False)
    except Exception as exc:
        print(f"\n[ERROR] Groq call failed: {exc}")
        return {"flag": None, "final_answer": None, "steps_taken": 0,
                "stopped_reason": "error", "trace": []}

    messages.append({"role": "assistant", "content": orientation})
    print(f"\n{'─' * 66}")
    print(f"[AI Guide]\n{orientation}")
    print(f"{'─' * 66}")

    while step < max_steps:
        # ── User input ────────────────────────────────────────────────────
        try:
            user_input = input("\nYour command (or 'hint' / 'quit'): ").strip()
        except (EOFError, KeyboardInterrupt):
            user_input = "quit"

        if not user_input:
            continue

        low = user_input.lower()

        if low == "quit":
            print("[USER] Quit.")
            stopped_reason = "user_quit"
            break

        if low == "hint":
            messages.append({
                "role": "user",
                "content": (
                    "The student asked for a more direct hint.  "
                    "Name the specific tool or technique they should try next, and why."
                ),
            })
            try:
                hint_text = _call_groq(messages, json_mode=False)
            except Exception as exc:
                print(f"\n[ERROR] Groq call failed: {exc}")
                stopped_reason = "error"
                break
            messages.append({"role": "assistant", "content": hint_text})
            print(f"\n[AI Hint]\n{hint_text}")
            continue

        # ── Run the user's command ────────────────────────────────────────
        step += 1
        command = user_input
        print(f"\n[Running...]  {command}")
        shell_res = run_shell_command(command, timeout=60)
        _print_result(shell_res)

        stdout    = shell_res["stdout"]
        stderr    = shell_res["stderr"]
        exit_code = shell_res["exit_code"]
        timed_out = shell_res["timed_out"]

        # ── Auto-detect flag in real output ───────────────────────────────
        verified_flag: Optional[str] = None
        matches = _FLAG_RE.findall(stdout + stderr)
        if matches:
            verified_flag = matches[0]
            print(f"\n[★ FLAG FOUND IN OUTPUT]  {verified_flag}")

        # ── Trace — full stdout/stderr, no truncation ─────────────────────
        trace_entry: dict = {
            "step":          step,
            "reasoning":     "",        # filled after AI teaches
            "command":       command,
            "stdout":        stdout,
            "stderr":        stderr,
            "exit_code":     exit_code,
            "timed_out":     timed_out,
            "flag_found":    bool(verified_flag),
            "flag":          verified_flag,
            "task_complete": bool(verified_flag),
            "final_answer":  verified_flag,
            "stuck":         False,
        }
        trace.append(trace_entry)

        # ── Ask LLM to teach on the real output ──────────────────────────
        messages.append({
            "role": "user",
            "content": (
                f"The student ran:  {command}\n\n"
                f"Real output:\n"
                f"stdout: {_truncate(stdout, _MODEL_LIMIT)}\n"
                f"stderr: {_truncate(stderr, _MODEL_LIMIT)}\n"
                f"Exit code: {exit_code}.  Timed out: {timed_out}.\n\n"
                "Explain what this output means and help the student reason about "
                "what to try next.  Don't just hand over the next command."
                + ("\n\nNote: a flag was detected in the output!" if verified_flag else "")
            ),
        })
        try:
            teaching = _call_groq(messages, json_mode=False)
        except Exception as exc:
            print(f"\n[ERROR] Groq call failed: {exc}")
            stopped_reason = "error"
            break

        messages.append({"role": "assistant", "content": teaching})
        trace_entry["reasoning"] = teaching     # fill in reasoning retroactively

        print(f"\n{'─' * 66}")
        print(f"[AI Guide]\n{teaching}")
        print(f"{'─' * 66}")

        if verified_flag:
            flag           = verified_flag
            stopped_reason = "flag_found"
            break

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'═' * 66}")
    print(f"  DONE  |  reason={stopped_reason}  steps={step}  flag={flag}")
    print(f"{'═' * 66}\n")

    return {
        "flag":           flag,
        "final_answer":   flag,         # in learn mode final_answer == flag (if any)
        "steps_taken":    step,
        "stopped_reason": stopped_reason,
        "trace":          trace,
    }


# ── Main loop — auto / manual ─────────────────────────────────────────────────
def run_ctf_agent(
    task:      str,
    mode:      str = "manual",
    max_steps: int = 15,
) -> dict:
    """Run the CTF agent loop.

    mode="manual"  LLM picks commands; user approves each before it runs.
                   Prompt: Enter=run, 'skip'=ask for a different command, 'quit'=stop.
    mode="auto"    LLM picks and runs automatically; everything printed live.
    mode="learn"   User types commands; LLM teaches on real output.

    Returns:
        {
            "flag":           str | None,
            "final_answer":   str | None,
            "steps_taken":    int,
            "stopped_reason": "flag_found" | "task_complete" | "stuck" |
                              "max_steps" | "user_quit" | "error",
            "trace":          list[dict]
        }
    """
    if mode not in ("manual", "auto", "learn"):
        raise ValueError(f"mode must be 'manual', 'auto', or 'learn' — got {mode!r}")

    if mode == "learn":
        return _run_learn_mode(task, max_steps)

    print(f"\n{'═' * 66}")
    print(f"  CTF AGENT  |  mode={mode}  max_steps={max_steps}")
    print(f"  Task: {task}")
    print(f"{'═' * 66}")

    # Retrieve relevant knowledge once up-front; inject as context in the first turn.
    kb_ctx = _retrieve_kb(task)
    kb_section = (
        f"\n\n--- Relevant knowledge ---\n{kb_ctx}\n--- End knowledge ---"
        if kb_ctx else ""
    )

    messages: list[dict] = [
        {"role": "system", "content": _SOLVE_PROMPT},
        {
            "role": "user",
            "content": (
                f"Task: {task}\n\nBegin.  Output your first step as JSON."
                f"{kb_section}"
            ),
        },
    ]

    trace:         list[dict]    = []
    flag:          Optional[str] = None
    final_answer:  Optional[str] = None
    stopped_reason               = "max_steps"
    step                         = 0

    while step < max_steps:
        step += 1

        # ── 1. Ask Groq ───────────────────────────────────────────────────
        try:
            raw_reply = _call_groq(messages, json_mode=True)
        except Exception as exc:
            print(f"\n[ERROR] Groq call failed: {exc}")
            stopped_reason = "error"
            break

        # ── 2. Parse structured reply ─────────────────────────────────────
        try:
            parsed = _parse_reply(raw_reply)
        except ValueError as exc:
            print(f"\n[WARN] Bad JSON from agent: {exc}")
            messages.append({"role": "assistant", "content": raw_reply})
            messages.append({
                "role": "user",
                "content": (
                    "Your last response was not valid JSON.  "
                    "Reply with ONLY the JSON object from the system prompt.  "
                    "No markdown fences, no extra text."
                ),
            })
            step -= 1
            continue

        reasoning:      str           = str(parsed.get("reasoning", "")).strip()
        command:        str           = str(parsed.get("command", "")).strip()
        flag_found:     bool          = bool(parsed.get("flag_found", False))
        claimed_flag:   Optional[str] = parsed.get("flag") or None
        task_complete:  bool          = bool(parsed.get("task_complete", False))
        claimed_answer: Optional[str] = parsed.get("final_answer") or None
        stuck:          bool          = bool(parsed.get("stuck", False))

        # ── 3. Show what the agent proposes ──────────────────────────────
        _print_step(step, reasoning, command)

        # ── 4. Stuck ──────────────────────────────────────────────────────
        if stuck:
            print("\n[AGENT] Declared stuck — no further ideas.")
            trace.append({
                "step": step, "reasoning": reasoning, "command": command,
                "stdout": "", "stderr": "", "exit_code": None,
                "timed_out": False, "flag_found": False, "flag": None,
                "task_complete": False, "final_answer": None, "stuck": True,
            })
            stopped_reason = "stuck"
            break

        # ── 5. Require a non-empty command ────────────────────────────────
        if not command:
            print("\n[WARN] Agent returned empty command — asking again.")
            messages.append({"role": "assistant", "content": raw_reply})
            messages.append({
                "role": "user",
                "content": "You did not provide a command.  Give exactly one command in the JSON.",
            })
            step -= 1
            continue

        # ── 6. Manual-mode approval gate ──────────────────────────────────
        if mode == "manual":
            skipped = False
            while True:
                try:
                    choice = input(
                        "\n  [Enter] run it   [skip] different command   [quit] stop: "
                    ).strip().lower()
                except EOFError:
                    choice = ""

                if choice in ("quit", "q"):
                    print("[USER] Quit.")
                    return {
                        "flag": flag, "final_answer": final_answer,
                        "steps_taken": step - 1,
                        "stopped_reason": "user_quit",
                        "trace": trace,
                    }
                if choice in ("skip", "s"):
                    print("[USER] Skip — asking for a different command.")
                    messages.append({"role": "assistant", "content": raw_reply})
                    messages.append({
                        "role": "user",
                        "content": (
                            "I rejected that command.  "
                            "Choose a different approach and give me a new command in JSON."
                        ),
                    })
                    step -= 1
                    skipped = True
                    break
                break   # Enter → approved

            if skipped:
                continue

        # ── 7. Run the command ────────────────────────────────────────────
        print("\n[Running...]")
        shell_res = run_shell_command(command, timeout=60)
        _print_result(shell_res)

        stdout    = shell_res["stdout"]
        stderr    = shell_res["stderr"]
        exit_code = shell_res["exit_code"]
        timed_out = shell_res["timed_out"]

        # ── 8a. Flag verification ─────────────────────────────────────────
        # Save the original claim before any mutation so the rejection note is accurate.
        verified_flag: Optional[str] = None
        rejection_note               = ""
        if flag_found and claimed_flag:
            original_claim = claimed_flag
            if _flag_in_output(claimed_flag, stdout, stderr):
                verified_flag = claimed_flag
                print(f"\n[★ FLAG VERIFIED]  {verified_flag}")
            else:
                print(
                    f"\n[REJECT] Agent claimed flag {original_claim!r} "
                    "but it was NOT present in real output.  Continuing."
                )
                flag_found   = False
                claimed_flag = None
                rejection_note = (
                    f"\n\nWARNING: You set flag_found=true and flag={original_claim!r} "
                    "but that string does NOT appear in the output above.  "
                    "Do NOT invent flags.  Keep working."
                )

        # ── 8b. task_complete check ───────────────────────────────────────
        if task_complete and not verified_flag:
            if claimed_answer:
                print(f"\n[TASK COMPLETE]  {claimed_answer}")
                final_answer = claimed_answer
            else:
                print("\n[WARN] task_complete=true but no final_answer provided — ignoring.")
                task_complete = False

        # ── 9. Trace — FULL stdout/stderr (no truncation) ─────────────────
        trace.append({
            "step":          step,
            "reasoning":     reasoning,
            "command":       command,
            "stdout":        stdout,
            "stderr":        stderr,
            "exit_code":     exit_code,
            "timed_out":     timed_out,
            "flag_found":    bool(verified_flag),
            "flag":          verified_flag,
            "task_complete": bool(task_complete and final_answer),
            "final_answer":  final_answer,
            "stuck":         False,
        })

        # ── 10. Feed real output back to LLM (TRUNCATED per _MODEL_LIMIT) ─
        messages.append({"role": "assistant", "content": raw_reply})

        if verified_flag:
            messages.append({
                "role": "user",
                "content": (
                    f"Command output:\n"
                    f"stdout: {_truncate(stdout, _MODEL_LIMIT)}\n"
                    f"stderr: {_truncate(stderr, _MODEL_LIMIT)}\n\n"
                    f"Exit code: {exit_code}.  Timed out: {timed_out}.\n"
                    f"FLAG CONFIRMED in output: {verified_flag}"
                ),
            })
            flag           = verified_flag
            stopped_reason = "flag_found"
            break

        if task_complete and final_answer:
            messages.append({
                "role": "user",
                "content": (
                    f"Command output:\n"
                    f"stdout: {_truncate(stdout, _MODEL_LIMIT)}\n"
                    f"stderr: {_truncate(stderr, _MODEL_LIMIT)}\n\n"
                    f"Exit code: {exit_code}.  Timed out: {timed_out}.\n"
                    f"TASK COMPLETE.  Final answer accepted: {final_answer}"
                ),
            })
            stopped_reason = "task_complete"
            break

        messages.append({
            "role": "user",
            "content": (
                f"Command output:\n"
                f"stdout: {_truncate(stdout, _MODEL_LIMIT)}\n"
                f"stderr: {_truncate(stderr, _MODEL_LIMIT)}\n\n"
                f"Exit code: {exit_code}.  Timed out: {timed_out}."
                f"{rejection_note}\n\n"
                "Give me the next step as JSON."
            ),
        })

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"\n{'═' * 66}")
    print(f"  DONE  |  reason={stopped_reason}  steps={step}  flag={flag}")
    if final_answer and not flag:
        print(f"  Answer: {final_answer}")
    print(f"{'═' * 66}\n")

    return {
        "flag":           flag,
        "final_answer":   final_answer,
        "steps_taken":    step,
        "stopped_reason": stopped_reason,
        "trace":          trace,
    }


# ── HTTP-facing agent class (registered with master router) ───────────────────
class CTFAgent(BaseAgent):
    """Single-turn HTTP wrapper around the CTF loop.

    Registered in api/server.py under the "ctf" route.
    For the full interactive multi-step loop, use run_ctf_agent() via CLI.
    """

    name  = "CTF"
    color = "red"
    system_prompt = (
        "You are an expert CTF and pentesting analyst. "
        "Given a task, provide: "
        "(1) challenge type (web, crypto, forensics, network, pwn…), "
        "(2) the 2–3 most relevant tools and techniques, "
        "(3) the single best first command to run and what to look for. "
        "Be concise and practical. Never fabricate results."
    )

    def run(self, task: str, context=None, model: str = "groq") -> dict:
        kb = _retrieve_kb(task)
        kb_section = f"\n\nRelevant reference material:\n{kb}" if kb else ""
        reply = self.think(
            f"CTF task: {task}{kb_section}\n\nProvide your analysis.",
            model=model,
        )
        return self._message(
            reply
            + "\n\n*To run the full interactive agent:*\n"
            + "`python agents/ctf_agent.py`"
        )


# ── Interactive test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\nCTF Agent — pick a mode:")
    print("  1  auto    — agent decides + runs all commands")
    print("  2  manual  — agent decides, you approve each command")
    print("  3  learn   — you type commands, agent teaches")
    choice = input("Mode [1/2/3]: ").strip()
    mode = {"1": "auto", "2": "manual", "3": "learn"}.get(choice, "manual")

    result = run_ctf_agent(
        task="Scan target 127.0.0.1 and report open ports",
        mode=mode,
        max_steps=15,
    )

    print("\n── Full result ──")
    print(json.dumps(result, indent=2, default=str))
