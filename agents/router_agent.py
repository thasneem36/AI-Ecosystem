"""Router — runs BEFORE the agent pipeline.

Classifies the user's message into exactly one of four intents so we only
run the agents that are actually needed:

    chat  → greeting / small talk       → one short friendly reply (no agents)
    task  → real problem to solve       → Planner + Executor
    code  → explicit code request       → Coding agent only
    learn → wants to learn/understand   → conversational Tutor (confirm first,
                                          teach in small pieces — NOT a one-shot plan)

If classification is uncertain or malformed, we default to "chat" (the
safest, smallest response).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from agents.base_agent import BaseAgent

VALID_ROUTES = ("chat", "task", "code", "learn", "ctf")

# Phrases that clearly signal a CTF / pentest task.
# Detected deterministically so they never fall through to chat/task/learn.
_CTF_PATTERNS = [
    r"\bctf\b",
    r"\bhack\s*the\s*box\b",
    r"\bhtb\b",
    r"\btryhackme\b",
    r"\bthm\b",
    r"\bpentest\b",
    r"\bpenetration\s+test",
    r"\bexploit\s+(?:this|the|a\b)",
    r"\bpwn\b",
    r"\bfind\s+(?:the\s+)?flag\b",
    r"\bcapture\s+the\s+flag\b",
    r"\bsql\s*injection\b",
    r"\bsqli\b",
    r"\broot\s+(?:the\s+)?(?:box|machine|server)\b",
    r"\bget\s+(?:root|shell|rce)\b",
    r"\breverse\s+shell\b",
    r"\bprivilege\s+escal",
    r"\bscan\s+(?:target\s+)?(?:\d{1,3}\.){3}\d{1,3}",    # "scan 10.10.x.x"
    r"(?:\d{1,3}\.){3}\d{1,3}.*\b(?:hack|pwn|exploit|flag|vuln)\b",
]

# Phrases that clearly signal an open-ended "teach me / help me learn" request.
# Detected deterministically so these never fall through to the Planner.
_LEARN_PATTERNS = [
    r"\bteach me\b",
    r"\bhelp me (?:to )?learn\b",
    r"\bhelp me (?:to )?understand\b",
    r"\b(?:can|could|will) you teach\b",
    r"\bi(?:'| a)?m trying to learn\b",
    r"\bi want to learn\b",
    r"\bi want to understand\b",
    r"\bi(?:'d| would) like to learn\b",
    r"\blearn how to\b",
    r"\bhow do i learn\b",
    r"\bexplain .+ to me\b",
    r"\bwalk me through\b",
]


def format_history(history: List[Dict[str, Any]], max_turns: int = 6) -> str:
    """Turn recent memory records into a short User/You transcript.

    `history` is the newest-first list from MemoryManager.get_history();
    output is chronological (oldest → newest).
    """
    if not history:
        return ""
    recent = list(history[:max_turns])
    recent.reverse()
    lines: List[str] = []
    for rec in recent:
        user = (rec.get("user_message") or "").strip()
        msgs = rec.get("messages") or []
        reply = (msgs[-1].get("content") or "").strip() if msgs else ""
        if user:
            lines.append(f"User: {user[:300]}")
        if reply:
            lines.append(f"You: {reply[:300]}")
    return "\n".join(lines)


class RouterAgent(BaseAgent):
    name = "Router"
    color = "white"
    system_prompt = (
        "You are a strict intent classifier. Read the user's message and reply with "
        "EXACTLY ONE word, lowercase, no punctuation, no explanation. Choose from:\n\n"
        "- chat  : ANYTHING answerable in one short reply — greetings, small talk, "
        "simple facts, trivial lists (e.g. 'give me numbers 1 to 10', 'list 5 fruits'), "
        "quick definitions, simple calculations, casual questions. "
        "When unsure between chat and task, choose chat.\n\n"
        "- task  : ONLY for genuinely multi-step problems where a plan adds real value "
        "— planning a trip, diagnosing a business problem, designing or building "
        "something with multiple moving parts. A one-sentence answer means it is NOT "
        "a task.\n\n"
        "- code  : an explicit request to write, fix, debug, or run code or a script.\n\n"
        "- learn : the user wants to be taught or understand a topic step-by-step "
        "(e.g. 'teach me X', 'help me learn X', 'explain X to me').\n\n"
        "Examples:\n"
        "  'give me the numbers 1 to 10'        → chat\n"
        "  'what is 7 times 8'                  → chat\n"
        "  'define recursion'                   → chat\n"
        "  'plan a 3-day Tokyo trip'             → task\n"
        "  'help my cafe improve slow sales'     → task\n"
        "  'write a python sort function'        → code\n\n"
        "Reply with only one word: chat, task, code, or learn."
    )

    def classify(self, message: str, model: str = "ollama") -> str:
        """Return one of: 'chat', 'task', 'code', 'learn'. Defaults to 'chat'."""
        # Deterministic fast-path: teaching/learning requests must never fall
        # through to the Planner as a one-shot task.
        if self._looks_like_learning(message):
            return "learn"
        raw = self.think(f"Message: {message}\n\nClassify:", model=model)
        return self._parse_route(raw)

    @staticmethod
    def _looks_like_learning(message: str) -> bool:
        text = (message or "").lower()
        return any(re.search(p, text) for p in _LEARN_PATTERNS)

    @staticmethod
    def _parse_route(raw: str) -> str:
        text = (raw or "").strip().lower()
        # Exact match first.
        if text in VALID_ROUTES:
            return text
        # Otherwise look for the first valid keyword anywhere in the reply.
        for route in VALID_ROUTES:
            if route in text:
                return route
        # Anything unexpected → safest, smallest path.
        return "chat"


class ChatAgent(BaseAgent):
    """The understanding layer — handles conversation with real attention.

    It reads the recent conversation history (passed in via context["history"])
    so it follows the thread, notices mood, and never repeats its opening.
    """

    name = "Assistant"
    color = "white"
    #: how many recent conversations to feed back in as context
    MAX_HISTORY_TURNS = 6

    system_prompt = (
        "You are the warm, perceptive conversational layer of an AI assistant. "
        "You talk like a thoughtful, grounded person — not a chatbot.\n\n"
        "How you behave:\n"
        "• NOTICE MOOD. If the user sounds down, tired, stressed, frustrated or "
        "stuck, acknowledge it gently and ask ONE open follow-up about what's going "
        "on. Never brush feelings aside with a generic line like 'hope it gets "
        "better'.\n"
        "• FOLLOW THE THREAD. Use the conversation so far. Never repeat an earlier "
        "greeting or opening line, and stay consistent with what was just said.\n"
        "• SURFACE THE REAL NEED. Through natural conversation, gently find out what "
        "the user actually needs. If a real problem emerges (e.g. 'python is hard to "
        "learn'), offer to help — then WAIT for them to say yes before doing the "
        "work.\n"
        "• CLARIFY AND CONFIRM. If what they want is unclear, restate what you "
        "understood in your own words and ask 'is that right?' before going further.\n"
        "• TONE FLEXES, TRUTH DOESN'T. Be warm and calm with someone upset, direct "
        "with someone blunt. Never soften or distort facts, and never just tell "
        "people what they want to hear.\n"
        "• STAY NATURAL. Never explain your own mechanics or process (don't say "
        "things like 'I'm trying to be concise' or 'as an AI'). Just talk.\n"
        "• DON'T OVER-ENGAGE. You're friendly to help, not to keep someone talking. "
        "If they just want to chat, keep it short and warm — usually one or two "
        "sentences. Don't interrogate."
    )

    def run(self, task: str, context: Dict[str, Any] | None = None, model: str = "ollama") -> Dict[str, Any]:
        history = (context or {}).get("history", [])
        transcript = self._format_history(history)
        if transcript:
            prompt = (
                "Here is the conversation so far (oldest to newest):\n"
                f"{transcript}\n\n"
                f'The user just said: "{task}"\n\n'
                "Continue naturally as the assistant. Do not repeat earlier greetings."
            )
        else:
            prompt = f'The user said: "{task}"\n\nReply naturally as the assistant.'
        reply = self.think(prompt, model=model)
        return self._message(reply)

    @classmethod
    def _format_history(cls, history: list) -> str:
        """Recent conversation as a chronological transcript (shared helper)."""
        return format_history(history, cls.MAX_HISTORY_TURNS)
