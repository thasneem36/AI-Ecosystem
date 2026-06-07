"""Coding agent — writes Python code, runs it, and saves it to output/."""
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
    system_prompt = (
        "You are the Coding agent. Output ONLY valid, directly-runnable Python code.\n"
        "STRICT RULES — follow exactly:\n"
        "1. Output Python source code ONLY. No prose, no explanations, no preamble, "
        "and no closing remarks before, after, or inside the code.\n"
        "2. NEVER include human instructions such as 'To save this code...', "
        "'Open a text editor', 'How to run', 'Step 1', or usage notes. The system "
        "runs the file automatically — no manual steps exist.\n"
        "3. The ONLY comments allowed are short '#' comments that explain the code "
        "itself. Comments must never be instructions addressed to a person.\n"
        "4. The code must run as-is with `python file.py` and print its result. "
        "No placeholders, no '...', no pseudo-code, no TODOs.\n"
        "5. If you use a code fence, use a single ```python block containing nothing "
        "but code. Preferably output raw code with no fence at all."
    )

    def run(self, task: str, context: Optional[Dict[str, Any]] = None, model: str = "ollama") -> Dict[str, Any]:
        prompt = f"Task: {task}\n\nWrite Python code to accomplish this."
        reply = self.think(prompt, model=model)
        code = self._extract_code(reply)

        result: Dict[str, Any] = {"code": code}
        if code:
            run_result = run_python(code)
            filename = f"solution_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
            saved = save_file(filename, code)
            result["execution"] = run_result
            result["file"] = saved
            summary = self._summarize(code, run_result, saved["name"])
        else:
            summary = reply or "No code was produced."

        return self._message(summary, **result)

    @staticmethod
    def _extract_code(text: str) -> str:
        """Pull only the runnable Python out of the model's reply.

        Handles prose outside fences, multiple fences, stray backticks, and
        leading/trailing prose by validating with the Python parser itself.
        """
        if not text:
            return ""

        # 1) Prefer fenced code blocks; join all of them (drops prose outside).
        blocks = re.findall(
            r"```[ \t]*(?:python|py)?[ \t]*\n?(.*?)```", text, re.DOTALL | re.IGNORECASE
        )
        candidate = "\n".join(b.rstrip() for b in blocks) if blocks else text

        # 2) Remove any stray fence lines that slipped through.
        candidate = "\n".join(
            ln for ln in candidate.splitlines() if not ln.strip().startswith("```")
        ).strip()
        if not candidate:
            return ""

        # 3) If it already parses, use it as-is.
        if CodingAgent._compiles(candidate):
            return candidate

        # 4) Otherwise trim prose from the ends to the largest block that parses.
        largest = CodingAgent._largest_compilable(candidate)
        if largest:
            return largest

        # 5) Nothing parses: only keep it if it at least looks like code (so a real
        #    syntax bug still surfaces); pure prose → treat as "no code".
        looks_like_code = any(
            k in candidate for k in ("def ", "import ", "print(", "=", "for ", "while ", "class ")
        )
        return candidate if looks_like_code else ""

    @staticmethod
    def _compiles(code: str) -> bool:
        """True if `code` is syntactically valid Python (does not execute it)."""
        if not code.strip():
            return False
        try:
            compile(code, "<generated>", "exec")
            return True
        except SyntaxError:
            return False

    @staticmethod
    def _largest_compilable(code: str) -> str:
        """Return the largest contiguous run of lines that parses as Python.

        Strips natural-language lines the model may have placed before or after
        the real code (the usual cause of 'exited with errors').
        """
        lines = code.splitlines()
        n = len(lines)
        best = ""
        best_len = 0
        for start in range(n):
            for end in range(n, start, -1):
                if end - start <= best_len:
                    break  # can't beat the current best from this start
                chunk = "\n".join(lines[start:end])
                if chunk.strip() and CodingAgent._compiles(chunk):
                    best, best_len = chunk, end - start
                    break
        return best

    @staticmethod
    def _summarize(code: str, run_result: Dict[str, Any], filename: str) -> str:
        status = "✅ ran successfully" if run_result.get("success") else "⚠️ exited with errors"
        out = run_result.get("stdout", "").strip()
        err = run_result.get("stderr", "").strip()
        parts = [f"```python\n{code}\n```", f"Saved to `output/{filename}` — {status}."]
        if out:
            parts.append(f"**Output:**\n```\n{out}\n```")
        if err and not run_result.get("success"):
            parts.append(f"**Errors:**\n```\n{err}\n```")
        return "\n\n".join(parts)
