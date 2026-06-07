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
    system_prompt = (
        "You judge whether a task request is specific enough to act on. "
        "Reply with EXACTLY ONE word, lowercase: 'clear' or 'vague'.\n"
        "- vague : too broad, missing key details, or could mean many things "
        "(e.g. 'my business is slow', 'help me grow', 'make it better').\n"
        "- clear : specific and actionable (e.g. 'write a SQL query to count "
        "users per month', 'plan a 3-day Tokyo trip for 2 people on a budget').\n"
        "Reply only 'clear' or 'vague'."
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

    def assess(self, message: str, model: str = "ollama") -> str:
        """Return 'vague' or 'clear'. Defaults to 'clear' (proceed) if unsure."""
        raw = self.think(f"Request: {message}\n\nJudge:", model=model).strip().lower()
        if "vague" in raw:
            return "vague"
        return "clear"

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
