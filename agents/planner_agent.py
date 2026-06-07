"""Planner agent — breaks a problem into clear, ordered steps."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent


class PlannerAgent(BaseAgent):
    name = "Planner"
    color = "yellow"
    system_prompt = (
        "You are the Planner agent in a multi-agent system. "
        "Given a user's problem, break it into a short, ordered list of concrete steps "
        "(3-6 steps max). Be specific and actionable. "
        "Respond ONLY with a numbered list, one step per line. Do not add commentary."
    )

    def run(self, task: str, context: Optional[Dict[str, Any]] = None, model: str = "ollama") -> Dict[str, Any]:
        prompt = f"Problem: {task}\n\nBreak this into clear ordered steps."
        reply = self.think(prompt, model=model)
        steps = self._parse_steps(reply)
        return self._message(reply, steps=steps)

    @staticmethod
    def _parse_steps(text: str) -> List[str]:
        steps: List[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # strip leading "1." / "1)" / "-" / "*"
            cleaned = re.sub(r"^[\s]*(\d+[\.\)]|[-*])\s*", "", line)
            if cleaned:
                steps.append(cleaned)
        return steps or ([text.strip()] if text.strip() else [])
