import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from task_tree_preferences import TaskTreePreferences


class TaskTreePreferencesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.prefs = TaskTreePreferences(self.root)

    def test_orders_are_scoped_and_append_unknown_ids(self):
        self.prefs.order("codex", "projects:模型", ["p2", "gone", "p1"])
        self.prefs.order("codex", "sessions:p1", ["t2", "t1"])
        self.prefs.order("claude", "projects:模型", ["p3", "p1"])
        reopened = TaskTreePreferences(self.root)
        self.assertEqual(reopened.ordered("codex", "projects:模型", ["p3", "p1", "p2", "p4"]),
                         ["p2", "p1", "p3", "p4"])
        self.assertEqual(reopened.ordered("claude", "projects:模型", ["p1", "p2", "p3"]), ["p3", "p1", "p2"])
        self.assertEqual(reopened.ordered("codex", "sessions:p1", ["t1", "t2"]), ["t2", "t1"])
        self.assertEqual(reopened.ordered("codex", "sessions:p2", ["t1", "t2"]), ["t1", "t2"])

    def test_moves_preserve_order_and_can_restore_default(self):
        self.prefs.order("codex", "sessions:project-a", ["t2", "t1"])
        self.prefs.move("codex", "t1", "project-b")
        reopened = TaskTreePreferences(self.root)
        self.assertEqual(reopened.destination("codex", "t1"), "project-b")
        self.assertIsNone(reopened.destination("claude", "t1"))
        reopened.move("codex", "t1", None)
        self.assertIsNone(TaskTreePreferences(self.root).destination("codex", "t1"))
        self.assertEqual(reopened.ordered("codex", "sessions:project-a", ["t1", "t2"]), ["t2", "t1"])

    def test_empty_uncategorized_group_key_is_a_valid_manual_destination(self):
        self.prefs.order("codex", "projects:all", ["p1", ""])
        self.prefs.move("codex", "t1", "")
        reopened = TaskTreePreferences(self.root)
        self.assertEqual(reopened.ordered("codex", "projects:all", ["", "p1"]), ["p1", ""])
        self.assertEqual(reopened.destination("codex", "t1"), "")

    def test_parallel_edits_merge_and_failed_write_preserves_previous_layout(self):
        other = TaskTreePreferences(self.root)
        with ThreadPoolExecutor(max_workers=2) as workers:
            sorted_projects = workers.submit(self.prefs.order, "codex", "projects:all", ["p2", "p1"])
            moved_session = workers.submit(other.move, "codex", "t1", "p2")
            sorted_projects.result()
            moved_session.result()
        reopened = TaskTreePreferences(self.root)
        self.assertEqual(reopened.ordered("codex", "projects:all", ["p1", "p2"]), ["p2", "p1"])
        self.assertEqual(reopened.destination("codex", "t1"), "p2")
        with patch("task_tree_preferences.atomic_write", side_effect=PermissionError("fixture")):
            with self.assertRaises(PermissionError):
                reopened.move("codex", "t1", "p1")
        self.assertEqual(reopened.destination("codex", "t1"), "p2")
        self.assertEqual(TaskTreePreferences(self.root).destination("codex", "t1"), "p2")
