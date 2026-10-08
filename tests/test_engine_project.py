"""Tests sans modèle IA pour le nouveau mode PROJET."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_engine import CodexEngine


class MultiFileEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = CodexEngine(self.root)

    def test_project_plan_can_contain_eight_distinct_files(self):
        self.engine.task_limit = 8
        plan = {
            "tasks": [
                {"id": f"T{i:02}", "goal": "module", "files": [f"src/mod_{i}.py"]}
                for i in range(1, 9)
            ]
        }
        normalized = self.engine._normalize_plan(plan, "construis une distribution Linux")
        self.assertEqual(len(normalized["tasks"]), 8)

    def test_normal_mode_stays_limited_to_two_files(self):
        self.engine.task_limit = 2
        plan = {
            "tasks": [
                {"id": f"T{i:02}", "goal": "module", "files": [f"src/mod_{i}.py"]}
                for i in range(1, 9)
            ]
        }
        normalized = self.engine._normalize_plan(plan, "crée des modules")
        self.assertEqual(len(normalized["tasks"]), 2)

    def test_project_build_commits_all_four_generated_modules(self):
        plan = {
            "tasks": [
                {"id": f"T{i:02}", "goal": "module", "files": [f"src/mod_{i}.py"]}
                for i in range(1, 5)
            ]
        }

        def fake_generate(_request, task):
            rel = task["files"][0]
            return [{"path": rel, "content": "VALUE = 1\n"}]

        with patch.object(self.engine, "make_plan", return_value=plan), patch.object(
            self.engine, "generate_task", side_effect=fake_generate
        ):
            result = self.engine.build("/project crée quatre modules Python")

        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.plan["mode"], "PROJECT")
        self.assertEqual(len(result.changed), 4)
        for i in range(1, 5):
            self.assertEqual(
                (self.root / f"src/mod_{i}.py").read_text(encoding="utf-8"),
                "VALUE = 1\n",
            )

    def test_generation_error_does_not_write_partial_project(self):
        plan = {
            "tasks": [
                {"id": "T01", "goal": "premier", "files": ["a.py"]},
                {"id": "T02", "goal": "second", "files": ["b.py"]},
            ]
        }
        calls = {"count": 0}

        def fake_generate(_request, task):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("modèle indisponible")
            return [{"path": task["files"][0], "content": "VALUE = 1\n"}]

        with patch.object(self.engine, "make_plan", return_value=plan), patch.object(
            self.engine, "generate_task", side_effect=fake_generate
        ):
            result = self.engine.build("/project crée une application complète")

        self.assertFalse(result.ok)
        self.assertFalse((self.root / "a.py").exists())
        self.assertFalse((self.root / "b.py").exists())


if __name__ == "__main__":
    unittest.main()
