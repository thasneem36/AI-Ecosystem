"""Web Search agent — wraps the DuckDuckGo search tool.

Disabled by default (opt-in): enable it in Agent Control to have the system
gather live web context before the Executor answers a "task". This keeps it
off the hot path (and off the network) unless an admin turns it on.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from tools.search import search_summary


class SearchAgent(BaseAgent):
    name = "Web Search"
    color = "cyan"
    system_prompt = "You retrieve relevant web results for a query."

    def __init__(self) -> None:
        super().__init__()
        # Off by default — admin enables it in Agent Control.
        self.enabled = False
        self.status = "offline"

    def gather(self, query: str, max_results: int = 5) -> str:
        """Return a plain-text summary of web results (no LLM call)."""
        self.status = "thinking"
        self.last_activity = datetime.now().isoformat()
        summary = search_summary(query, max_results=max_results)
        self.status = "active"
        return summary

    def run(self, task: str, context: Optional[Dict[str, Any]] = None, model: str = "ollama") -> Dict[str, Any]:
        summary = self.gather(task)
        content = f"**Web search results**\n\n{summary}"
        return self._message(content, summary=summary)
