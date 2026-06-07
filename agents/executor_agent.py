"""Executor agent — works through the planner's steps and produces a result."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent


class ExecutorAgent(BaseAgent):
    name = "Executor"
    color = "cyan"
    system_prompt = (
        "You are the Executor agent in a multi-agent system. "
        "You are given a problem and a plan of steps. Carry out the reasoning for each "
        "step and produce a clear, helpful final answer for the user. "
        "If a step requires running code, describe what should be coded — a separate "
        "Coding agent will handle execution. Be concise and well structured."
    )

    def run(self, task: str, context: Optional[Dict[str, Any]] = None, model: str = "ollama") -> Dict[str, Any]:
        ctx = context or {}
        steps: List[str] = ctx.get("steps", [])
        plan_text = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)) if steps else "(no explicit plan)"
        # Optional live web context, provided when the Web Search agent is enabled.
        search_block = ""
        if ctx.get("search"):
            search_block = f"Web search context (use if relevant):\n{ctx['search']}\n\n"
        prompt = (
            f"Problem: {task}\n\n"
            f"Plan:\n{plan_text}\n\n"
            f"{search_block}"
            "Execute the plan and give the user a clear, complete answer."
        )
        reply = self.think(prompt, model=model)
        # Detect whether the executor thinks code is needed.
        needs_code = any(k in reply.lower() for k in ("```", "def ", "import ", "code:")) or (
            context or {}
        ).get("needs_code", False)
        return self._message(reply, needs_code=needs_code)
