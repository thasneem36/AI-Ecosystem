"""Basic tests for the agent system and API.

These avoid hitting the real Ollama backend by monkeypatching `think`.
Run with:  python -m pytest tests/ -v   (or: python -m unittest)
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.coding_agent import CodingAgent
from agents.executor_agent import ExecutorAgent
from agents.planner_agent import PlannerAgent
from agents.router_agent import RouterAgent
from agents.search_agent import SearchAgent
from memory.memory_manager import MemoryManager
from tools.code_runner import run_python
from tools.file_manager import save_file, list_files, delete_file


class TestPlannerAgent(unittest.TestCase):
    def test_parse_steps(self):
        text = "1. First step\n2. Second step\n3. Third step"
        steps = PlannerAgent._parse_steps(text)
        self.assertEqual(steps, ["First step", "Second step", "Third step"])

    def test_run_returns_message(self):
        agent = PlannerAgent()
        agent.think = lambda *a, **k: "1. Do A\n2. Do B"
        msg = agent.run("solve x")
        self.assertEqual(msg["agent"], "Planner")
        self.assertEqual(msg["color"], "yellow")
        self.assertEqual(msg["steps"], ["Do A", "Do B"])


class TestRouterAgent(unittest.TestCase):
    def test_parse_exact(self):
        self.assertEqual(RouterAgent._parse_route("chat"), "chat")
        self.assertEqual(RouterAgent._parse_route("task"), "task")
        self.assertEqual(RouterAgent._parse_route("code"), "code")

    def test_parse_messy_output(self):
        self.assertEqual(RouterAgent._parse_route("  CODE\n"), "code")
        self.assertEqual(RouterAgent._parse_route("The route is task."), "task")

    def test_parse_unexpected_defaults_to_chat(self):
        self.assertEqual(RouterAgent._parse_route("banana"), "chat")
        self.assertEqual(RouterAgent._parse_route(""), "chat")

    def test_learning_fast_path(self):
        agent = RouterAgent()
        # These must classify as 'learn' WITHOUT calling the model (deterministic).
        agent.think = lambda *a, **k: (_ for _ in ()).throw(AssertionError("model called"))
        for msg in ["help me learn python", "teach me guitar", "explain recursion to me",
                    "I want to understand pointers", "walk me through git"]:
            self.assertEqual(agent.classify(msg), "learn", msg)

    def test_non_learning_uses_model(self):
        agent = RouterAgent()
        agent.think = lambda *a, **k: "task"
        self.assertEqual(agent.classify("fix my résumé wording"), "task")


class TestTeachAgent(unittest.TestCase):
    def test_first_contact_confirms_not_teaches(self):
        from agents.teach_agent import TeachAgent

        agent = TeachAgent()
        captured = {}
        agent.think = lambda prompt, model="ollama": captured.setdefault("p", prompt) or "ok"
        agent.run("help me learn python", context={"history": []})
        # First contact prompt must instruct confirm-first, no plan.
        self.assertIn("Do NOT teach yet", captured["p"])

    def test_uses_history_when_present(self):
        from agents.teach_agent import TeachAgent

        agent = TeachAgent()
        captured = {}
        agent.think = lambda prompt, model="ollama": captured.setdefault("p", prompt) or "ok"
        hist = [{"user_message": "help me learn python",
                 "messages": [{"content": "Want to begin? Say 'ready'."}]}]
        agent.run("ready", context={"history": hist})
        self.assertIn("ready", captured["p"])
        self.assertIn("Want to begin", captured["p"])

    def test_classify_uses_think(self):
        agent = RouterAgent()
        agent.think = lambda *a, **k: "code"
        self.assertEqual(agent.classify("write a python script"), "code")


class TestChatAgent(unittest.TestCase):
    def test_format_history_chronological(self):
        from agents.router_agent import ChatAgent

        # MemoryManager returns newest-first; formatter should reverse to chronological.
        history = [
            {"user_message": "not good", "messages": [{"content": "second reply"}]},
            {"user_message": "hi", "messages": [{"content": "first reply"}]},
        ]
        out = ChatAgent._format_history(history)
        self.assertEqual(
            out, "User: hi\nYou: first reply\nUser: not good\nYou: second reply"
        )

    def test_run_passes_history_into_prompt(self):
        from agents.router_agent import ChatAgent

        agent = ChatAgent()
        captured = {}
        agent.think = lambda prompt, model="ollama": captured.setdefault("p", prompt) or "ok"
        hist = [{"user_message": "hi", "messages": [{"content": "hey there"}]}]
        agent.run("not good", context={"history": hist})
        self.assertIn("User: hi", captured["p"])
        self.assertIn("not good", captured["p"])


class TestSearchAgent(unittest.TestCase):
    def test_disabled_by_default(self):
        self.assertFalse(SearchAgent().enabled)

    def test_run_returns_summary(self):
        agent = SearchAgent()
        agent.gather = lambda *a, **k: "1. Result A\n2. Result B"  # stub, no network
        msg = agent.run("latest python release")
        self.assertEqual(msg["agent"], "Web Search")
        self.assertIn("Result A", msg["summary"])
        self.assertIn("Web search results", msg["content"])


class TestExecutorAgent(unittest.TestCase):
    def test_detects_code_need(self):
        agent = ExecutorAgent()
        agent.think = lambda *a, **k: "Here is code:\n```python\nprint(1)\n```"
        msg = agent.run("task", context={"steps": ["a", "b"]})
        self.assertEqual(msg["agent"], "Executor")
        self.assertTrue(msg["needs_code"])


class TestCodingAgent(unittest.TestCase):
    def test_extract_code(self):
        text = "```python\nprint('hi')\n```"
        self.assertEqual(CodingAgent._extract_code(text), "print('hi')")

    def test_extract_strips_prose_outside_fence(self):
        text = (
            "Sure! Here is the code:\n"
            "```python\nprint(2 + 2)\n```\n"
            "To save this code: 1. Open a text editor and paste it."
        )
        code = CodingAgent._extract_code(text)
        self.assertEqual(code, "print(2 + 2)")
        self.assertTrue(CodingAgent._compiles(code))

    def test_extract_strips_prose_without_fence(self):
        text = (
            "To run this program, follow these steps first.\n"
            "import math\n"
            "print(math.sqrt(16))\n"
            "Now save the file and run it in your terminal."
        )
        code = CodingAgent._extract_code(text)
        self.assertTrue(CodingAgent._compiles(code))
        self.assertIn("math.sqrt(16)", code)
        self.assertNotIn("follow these steps", code)

    def test_extract_pure_prose_returns_empty(self):
        self.assertEqual(CodingAgent._extract_code("Here is how you would do it conceptually."), "")

    def test_run_executes_and_saves(self):
        agent = CodingAgent()
        agent.think = lambda *a, **k: "```python\nprint('hello world')\n```"
        msg = agent.run("print hello")
        self.assertEqual(msg["agent"], "Coding")
        self.assertIn("hello world", msg["execution"]["stdout"])
        self.assertTrue(msg["execution"]["success"])


class TestCodeRunner(unittest.TestCase):
    def test_run_python_success(self):
        result = run_python("print(2 + 2)")
        self.assertTrue(result["success"])
        self.assertIn("4", result["stdout"])

    def test_run_python_error(self):
        result = run_python("raise ValueError('boom')")
        self.assertFalse(result["success"])
        self.assertIn("boom", result["stderr"])


class TestFileManager(unittest.TestCase):
    def test_save_and_list_and_delete(self):
        info = save_file("unittest_sample.txt", "hello")
        self.assertEqual(info["name"], "unittest_sample.txt")
        names = [f["name"] for f in list_files()]
        self.assertIn("unittest_sample.txt", names)
        self.assertTrue(delete_file("unittest_sample.txt"))


class TestMemoryManager(unittest.TestCase):
    def test_save_and_clear(self):
        # Use a throwaway temp file so we never touch real memory or leave artifacts.
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        try:
            mm = MemoryManager(Path(tmp.name))
            mm.clear()
            mm.save_conversation("hi", [{"agent": "Planner", "content": "x"}])
            self.assertEqual(mm.count(), 1)
            mm.clear()
            self.assertEqual(mm.count(), 0)
        finally:
            Path(tmp.name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
