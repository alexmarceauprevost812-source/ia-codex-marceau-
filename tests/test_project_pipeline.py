"""Tests de sécurité du moteur d'écriture TI-LEX."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_pipeline import commit_project_files, fingerprint


class ProjectPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.backups = self.root / ".tilex" / "backups"

    def test_entire_batch_is_written_and_backed_up(self):
        (self.root / "a.py").write_text("before\n", encoding="utf-8")
        changed = commit_project_files(
            self.root,
            [("a.py", "after\n"), ("src/b.py", "print('ok')\n")],
            self.backups,
            expected={
                "a.py": fingerprint(b"before\n"),
                "src/b.py": None,
            },
        )
        self.assertEqual(changed, ["a.py", "src/b.py"])
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "after\n")
        self.assertEqual(
            (self.root / "src/b.py").read_text(encoding="utf-8"),
            "print('ok')\n",
        )
        snapshots = list(self.backups.iterdir())
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(
            (snapshots[0] / "a.py").read_text(encoding="utf-8"),
            "before\n",
        )

    def test_changed_file_blocks_entire_batch(self):
        (self.root / "a.py").write_text("modified by user\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Conflit"):
            commit_project_files(
                self.root,
                [("a.py", "generated\n"), ("new.py", "created\n")],
                self.backups,
                expected={"a.py": fingerprint(b"previous"), "new.py": None},
            )
        self.assertEqual(
            (self.root / "a.py").read_text(encoding="utf-8"),
            "modified by user\n",
        )
        self.assertFalse((self.root / "new.py").exists())

    def test_write_error_rolls_back_previously_written_files(self):
        (self.root / "a.py").write_text("a-original\n", encoding="utf-8")
        (self.root / "b.py").write_text("b-original\n", encoding="utf-8")
        original_replace = os.replace
        counter = {"calls": 0}

        def fail_on_second_replace(source, destination):
            counter["calls"] += 1
            if counter["calls"] == 2:
                raise OSError("simulated disk error")
            return original_replace(source, destination)

        with patch("project_pipeline.os.replace", side_effect=fail_on_second_replace):
            with self.assertRaisesRegex(RuntimeError, "annulée"):
                commit_project_files(
                    self.root,
                    [("a.py", "a-generated\n"), ("b.py", "b-generated\n")],
                    self.backups,
                )
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "a-original\n")
        self.assertEqual((self.root / "b.py").read_text(encoding="utf-8"), "b-original\n")

    def test_escape_outside_project_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "hors projet"):
            commit_project_files(
                self.root,
                [("../outside.py", "invalid")],
                self.backups,
            )

    def test_duplicate_target_is_rejected_without_changes(self):
        with self.assertRaisesRegex(ValueError, "double"):
            commit_project_files(
                self.root,
                [("a.py", "a"), ("A.py", "b")],
                self.backups,
            )
        self.assertFalse((self.root / "a.py").exists())

    def test_new_file_race_is_detected(self):
        (self.root / "new.py").write_text("I created this\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Conflit"):
            commit_project_files(
                self.root,
                [("new.py", "Generated\n")],
                self.backups,
                expected={"new.py": None},
            )


if __name__ == "__main__":
    unittest.main()
