"""Clarifier — gates the task pipeline.

Before a "task" runs Planner → Executor, the Clarifier judges whether the
request is specific enough to act on. If it's vague, it restates what it
understood and asks one question ("…is that right?") instead of dumping a plan.
If it's already clear, it stays out of the way and the pipeline runs normally.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from agents.router_agent import format_history


class ClarifierAgent(BaseAgent):
    name = "Clarifier"
    color = "yellow"

    # Used only for the vague/clear judgement (one word out).
    # Deliberately conservative — only fire when the cost of a wrong guess is HIGH.
    system_prompt = (
        "You decide whether a request needs a clarifying question BEFORE any work starts.\n"
        "Say 'vague' ONLY when ALL of the following are true:\n"
        "  1. Multiple very different interpretations exist.\n"
        "  2. Acting on the wrong one would: waste significant work, touch the wrong file,\n"
        "     run the wrong command, or produce materially incorrect output.\n"
        "  3. The recent conversation does NOT already resolve the ambiguity.\n"
        "Say 'clear' (proceed) when:\n"
        "  - A wrong guess costs only a one-line correction.\n"
        "  - The most reasonable interpretation is obvious, even if not perfectly stated.\n"
        "  - The prior turn(s) established a pattern (game, counting, Q&A loop, step-by-step)\n"
        "    and the current message is a short continuation (e.g. '1', 'next', 'yes', 'go on').\n"
        "  - The message is short but the conversation context makes intent clear.\n"
        "When in doubt, say 'clear' — the user can correct a wrong guess in one line.\n"
        "Reply EXACTLY ONE word, lowercase: 'clear' or 'vague'."
    )

    # Lenient judge used AFTER the user has answered a clarifying question:
    # "do I now have enough to act?" — biased toward proceeding.
    _enough_prompt = (
        "You decide whether there is now ENOUGH to produce a useful first plan. "
        "The user has ALREADY answered a clarifying question, so lean strongly "
        "toward 'yes'. Say 'no' ONLY if the request is still completely unusable — "
        "no identifiable topic AND no goal or direction at all. If there is any "
        "topic and any goal (e.g. 'a cafe' + 'improve slow sales'), say 'yes'.\n"
        "Reply with EXACTLY one word, lowercase: 'yes' or 'no'."
    )

    # Separate persona for writing the clarifying reply.
    _clarify_prompt = (
        "You are clarifying a vague request before any work starts.\n"
        "CRITICAL: Do NOT assume the topic. The request could be about business, "
        "health, study, relationships, money, travel, or anything else. Never assume "
        "it is about programming, software, Python, or code unless the user explicitly "
        "said so. Do NOT mention Python, code, or any technical tools.\n"
        "In 2-3 short sentences:\n"
        "1. Restate ONLY what the user actually said, in your own words — add no topic, "
        "domain, or solution they did not mention.\n"
        "2. Ask ONE open question to understand the real problem (e.g. what they mean, "
        "what kind, or what they want to change).\n"
        "3. End with 'Is that right?'.\n"
        "Do NOT give a plan, steps, or a solution yet. Be warm and concise."
    )

    def assess(
        self,
        message: str,
        model: str = "ollama",
        history: Optional[list] = None,
    ) -> str:
        """Return 'vague' or 'clear'. Defaults to 'clear' (proceed) if unsure.

        history (newest-first) is prepended so the model can detect continuation
        patterns — e.g. a prior game/counting turn makes a bare '1' clearly a reply,
        not an ambiguous new request.
        """
        ctx = ""
        if history:
            last = history[0]  # most recent turn
            prior_msgs = last.get("messages") or []
            prior_content = (prior_msgs[-1].get("content", "") if prior_msgs else "")[:300]
            prior_user = last.get("user_message", "")
            if prior_user or prior_content:
                ctx = (
                    f"Most recent prior turn:\n"
                    f"  User said: {prior_user}\n"
                    f"  Assistant replied: {prior_content}\n\n"
                )
        raw = self.think(f"{ctx}Current request: {message}\n\nJudge:", model=model).strip().lower()
        if "vague" in raw:
            return "vague"
        return "clear"

    def has_enough(self, message: str, model: str = "ollama") -> bool:
        """True if there's now enough to act on (used after the user answered).

        Biased toward proceeding: only returns False on an explicit 'no', so a
        stray reply or backend hiccup proceeds rather than re-asking forever.
        """
        original_prompt = self.system_prompt
        self.system_prompt = self._enough_prompt
        try:
            raw = self.think(
                f"Information so far:\n{message}\n\nIs there enough to act?", model=model
            ).strip().lower()
        finally:
            self.system_prompt = original_prompt
        return "no" not in raw  # default to proceeding unless an explicit 'no'

    def run(self, task: str, context: Optional[Dict[str, Any]] = None, model: str = "ollama") -> Dict[str, Any]:
        """Produce the restate-and-confirm reply for a vague task."""
        history = (context or {}).get("history", [])
        transcript = format_history(history, max_turns=4)
        ctx = f"Conversation so far:\n{transcript}\n\n" if transcript else ""
        # Swap in the clarifying persona just for this generation.
        original_prompt = self.system_prompt
        self.system_prompt = self._clarify_prompt
        try:
            reply = self.think(f'{ctx}The user said: "{task}"\n\nClarify before proceeding.', model=model)
        finally:
            self.system_prompt = original_prompt
        return self._message(reply)
