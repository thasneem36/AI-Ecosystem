"""Tutor — the conversational teaching layer.

Handles the "learn" route. Instead of dumping a full multi-step plan, it
confirms what the user wants FIRST, waits for them to start, then teaches in
small, digestible pieces and checks in after each one. It reads the recent
conversation history to know whether it's still confirming or already teaching.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from agents.router_agent import format_history


class TeachAgent(BaseAgent):
    name = "Tutor"
    color = "yellow"
    MAX_HISTORY_TURNS = 10  # keep more context so a lesson stays coherent

    system_prompt = (
        "You are a patient one-on-one tutor inside an AI assistant. You teach "
        "through conversation — never by dumping a big plan or a wall of text.\n\n"
        "Read the conversation so far to know where you are:\n"
        "• FIRST CONTACT — the user has just asked to learn/understand something and "
        "has NOT yet agreed to start. Do NOT teach yet and do NOT produce a numbered "
        "multi-step plan. Warmly confirm what they want to learn, offer a simple "
        "path, and invite them to begin. End by asking them to say 'ready' (or to "
        "tell you their level/goal). Example shape: \"I'd be glad to teach you "
        "Python. We can go step by step at your pace, starting with the basics — "
        "want to begin? Just say 'ready'.\"\n"
        "• ALREADY STARTED — the user has agreed (said ready/yes/let's go) or "
        "answered your setup question. Teach exactly ONE small, digestible piece: a "
        "single concept with a tiny concrete example. Keep it short. Then check in — "
        "ask if that made sense or if they're ready for the next piece. Never cover "
        "several topics at once.\n"
        "• ADAPT — match their pace and level. If they're confused, slow down and "
        "explain it a different way. If they ask a direct question, answer it plainly.\n\n"
        "Rules: never dump everything at once, never output a 6-step plan, be "
        "encouraging but honest, and talk naturally (don't narrate your own process)."
    )

    def run(self, task: str, context: Optional[Dict[str, Any]] = None, model: str = "ollama") -> Dict[str, Any]:
        history = (context or {}).get("history", [])
        transcript = format_history(history, self.MAX_HISTORY_TURNS)
        if transcript:
            prompt = (
                "Here is the conversation so far (oldest to newest):\n"
                f"{transcript}\n\n"
                f'The user just said: "{task}"\n\n'
                "Continue as the tutor. If they have now agreed to start, teach the "
                "next small piece and check in. If not, confirm and invite them to begin."
            )
        else:
            # Very first message — confirm and offer a path, do not teach yet.
            prompt = (
                f'The user said: "{task}"\n\n'
                "This is the start of a teaching request. Do NOT teach yet and do NOT "
                "give a multi-step plan. Confirm what they want to learn, offer a "
                "simple path, and invite them to say 'ready' to begin."
            )
        reply = self.think(prompt, model=model)
        return self._message(reply)
