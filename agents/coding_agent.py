"""Coding agent — writes code, runs Python, saves web files.

Language routing:
  Python      → run via subprocess, show output, optionally save .py
  HTML/CSS/JS → skip the runner, always save .html, tell user to open in browser
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from tools.code_runner import run_python
from tools.file_manager import save_file


class CodingAgent(BaseAgent):
    name = "Coding"
    color = "green"

    # ------------------------------------------------------------------ #
    # System prompts — swapped per language in run()
    # ------------------------------------------------------------------ #
    _PYTHON_SYSTEM_PROMPT = (
        "You are the Coding agent.\n"
        "FORMAT — two parts, nothing else:\n"
        "  1. One short sentence (under 15 words) describing what the code does. "
        "Example: 'This prints the numbers 1 to 10.'\n"
        "  2. The Python code inside a single ```python fence.\n"
        "RULES:\n"
        "- No other prose before, between, or after those two parts.\n"
        "- No usage notes, no 'How to run', no closing remarks.\n"
        "- Code must run as-is with `python file.py`. No placeholders, no TODOs.\n"
        "- Only short inline '#' comments inside the code are allowed."
    )

    _WEB_SYSTEM_PROMPT = (
        "You are the Coding agent. The user wants a web application (HTML/CSS/JavaScript).\n"
        "FORMAT — two parts, nothing else:\n"
        "  1. One short sentence (under 15 words) describing what the app does. "
        "Example: 'A to-do list where you can add and remove tasks.'\n"
        "  2. A complete, self-contained HTML file inside a single ```html fence.\n"
        "RULES:\n"
        "- Embed ALL CSS in a <style> tag and ALL JS in a <script> tag — no CDN, no external files.\n"
        "- No other prose, no closing remarks.\n"
        "- NEVER wrap HTML in Python print() statements."
    )

    # Required by BaseAgent — default is Python; swapped inside run() for web tasks.
    system_prompt = _PYTHON_SYSTEM_PROMPT

    # Words/phrases signalling the user wants a .py file saved to disk.
    _SAVE_KEYWORDS = (
        "save", "create file", "write file", "make file",
        "create a file", "write a script", "create a script",
        "store", "write to", "save to", "output to file",
    )

    # Words/phrases that indicate a web (HTML/CSS/JS) task.
    _WEB_SIGNALS = (
        "html", "css", "javascript", "webpage", "web page",
        "web app", "web application", "frontend", "front-end",
        "browser-based", "browser based",
    )

    # ------------------------------------------------------------------ #
    # Language detection
    # ------------------------------------------------------------------ #
    @classmethod
    def _detect_language(cls, task: str) -> str:
        """Return 'html' if the task clearly asks for web code, 'python' otherwise."""
        low = task.lower()
        if any(sig in low for sig in cls._WEB_SIGNALS):
            return "html"
        # '\bjs\b' matches "js" as a whole word (not "adjust", "enjoys", etc.)
        if re.search(r"\bjs\b", low):
            return "html"
        return "python"

    # ------------------------------------------------------------------ #
    # Save-intent detection (Python only)
    # ------------------------------------------------------------------ #
    @classmethod
    def _should_save(cls, task: str) -> bool:
        """True only when the user explicitly asked to save/create a Python file."""
        low = task.lower()
        return any(kw in low for kw in cls._SAVE_KEYWORDS)

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    def run(self, task: str, context: Optional[Dict[str, Any]] = None, model: str = "ollama") -> Dict[str, Any]:
        lang = self._detect_language(task)

        if lang == "html":
            return self._run_web(task, model)
        return self._run_python(task, model)

    # ------------------------------------------------------------------ #
    # Web (HTML/CSS/JS) path — save only, never execute
    # ------------------------------------------------------------------ #
    def _run_web(self, task: str, model: str) -> Dict[str, Any]:
        self.system_prompt = self._WEB_SYSTEM_PROMPT
        try:
            prompt = (
                f"Task: {task}\n\n"
                "One sentence describing what the app does, then the complete HTML in a ```html fence."
            )
            reply = self.think(prompt, model=model)
        finally:
            self.system_prompt = self._PYTHON_SYSTEM_PROMPT

        # Extract code only for saving — display uses the full reply so nothing is dropped.
        code = self._extract_web_code(reply)
        result: Dict[str, Any] = {"code": code}

        if code:
            filename = f"solution_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            saved = save_file(filename, code)
            result["file"] = saved
            summary = self._summarize_web(reply, saved["name"])
        else:
            summary = reply or "No web code was produced."

        return self._message(summary, **result)

    # ------------------------------------------------------------------ #
    # Python path — run, optionally save
    # ------------------------------------------------------------------ #
    def _run_python(self, task: str, model: str) -> Dict[str, Any]:
        prompt = (
            f"Task: {task}\n\n"
            "One sentence describing what the code does, then the Python code in a ```python fence."
        )
        reply = self.think(prompt, model=model)

        # Extract code only for running/saving — display uses the full reply so
        # the description is never dropped regardless of where the model put it.
        code = self._extract_code(reply)

        result: Dict[str, Any] = {"code": code}
        if code:
            run_result = run_python(code)
            result["execution"] = run_result

            if self._should_save(task):
                filename = f"solution_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
                saved = save_file(filename, code)
                result["file"] = saved
                summary = self._summarize_python(reply, run_result, saved["name"])
            else:
                summary = self._summarize_python(reply, run_result, None)
        else:
            summary = reply or "No code was produced."

        return self._message(summary, **result)

    # ------------------------------------------------------------------ #
    # Code extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_web_code(text: str) -> str:
        """Extract HTML/CSS/JS from the model reply.

        Prefers a named fence (```html …```). Falls back to any text that
        looks like HTML (contains angle-bracket tags).
        """
        if not text:
            return ""
        blocks = re.findall(
            r"```[ \t]*(?:html|css|javascript|js|web)?[ \t]*\n?(.*?)```",
            text, re.DOTALL | re.IGNORECASE,
        )
        if blocks:
            return "\n".join(b.rstrip() for b in blocks).strip()
        stripped = text.strip()
        # Remove stray backtick lines
        lines = [ln for ln in stripped.splitlines() if not ln.strip().startswith("```")]
        candidate = "\n".join(lines).strip()
        if "<" in candidate and ">" in candidate:
            return candidate
        return stripped

    @staticmethod
    def _extract_code(text: str) -> str:
        """Pull only the runnable Python out of the model's reply.

        Handles prose outside fences, multiple fences, stray backticks, and
        leading/trailing prose by validating with the Python parser itself.
        """
        if not text:
            return ""

        blocks = re.findall(
            r"```[ \t]*(?:python|py)?[ \t]*\n?(.*?)```", text, re.DOTALL | re.IGNORECASE
        )
        candidate = "\n".join(b.rstrip() for b in blocks) if blocks else text

        candidate = "\n".join(
            ln for ln in candidate.splitlines() if not ln.strip().startswith("```")
        ).strip()
        if not candidate:
            return ""

        if CodingAgent._compiles(candidate):
            return candidate

        largest = CodingAgent._largest_compilable(candidate)
        if largest:
            return largest

        looks_like_code = any(
            k in candidate for k in ("def ", "import ", "print(", "=", "for ", "while ", "class ")
        )
        return candidate if looks_like_code else ""

    @staticmethod
    def _compiles(code: str) -> bool:
        if not code.strip():
            return False
        try:
            compile(code, "<generated>", "exec")
            return True
        except SyntaxError:
            return False

    @staticmethod
    def _largest_compilable(code: str) -> str:
        lines = code.splitlines()
        n = len(lines)
        best = ""
        best_len = 0
        for start in range(n):
            for end in range(n, start, -1):
                if end - start <= best_len:
                    break
                chunk = "\n".join(lines[start:end])
                if chunk.strip() and CodingAgent._compiles(chunk):
                    best, best_len = chunk, end - start
                    break
        return best

    # ------------------------------------------------------------------ #
    # Summary builders
    # ------------------------------------------------------------------ #
    @staticmethod
    def _summarize_web(reply: str, filename: str) -> str:
        """Preserve the full LLM reply (description + html fence), add the saved-file note."""
        parts = [reply.strip(), f"Saved to `output/{filename}` — open it in your browser."]
        return "\n\n".join(parts)

    @staticmethod
    def _summarize_python(
        reply: str,
        run_result: Dict[str, Any],
        filename: Optional[str],
    ) -> str:
        """Preserve the full LLM reply (description + code fence), append run results."""
        status = "✅ ran successfully" if run_result.get("success") else "⚠️ exited with errors"
        out = run_result.get("stdout", "").strip()
        err = run_result.get("stderr", "").strip()
        parts = [reply.strip()]
        if filename:
            parts.append(f"Saved to `output/{filename}` — {status}.")
        else:
            parts.append(status)
        if out:
            parts.append(f"**Output:**\n```\n{out}\n```")
        if err and not run_result.get("success"):
            parts.append(f"**Errors:**\n```\n{err}\n```")
        return "\n\n".join(parts)
