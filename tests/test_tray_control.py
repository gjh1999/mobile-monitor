import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from automation import Approvals
from claude_rpc import ClaudeCodeRPC
from task_control import TaskControl
from tray_release import TrayRelease


class TrayControlTests(unittest.TestCase):
    def test_disables_existing_and_queued_control_until_explicit_new_attachment(self):
        gate = TrayRelease()
        control = TaskControl(":memory:")
        self.addCleanup(control.db.close)
        control.control_allowed = gate.control_allowed
        thread, configured = str(uuid4()), str(uuid4())
        control.seed([thread])
        operation = control.submit(thread, "message", "fixture")
        gate.set(True)
        generation = gate.begin("codex")
        control.disable_for_tray()
        self.assertFalse(control.snapshot()["tasks"])
        queued, = [row for row in control.snapshot()["operations"] if row["id"] == operation]
        self.assertEqual(queued["status"], "cancelled")
        with self.assertRaisesRegex(ValueError, "正在交还"):
            control.submit(thread, "focus")
        gate.set(False)
        self.assertFalse(gate.control_allowed())
        gate.finish("codex", generation, ("waiting", 1, "fixture"))
        self.assertTrue(gate.control_allowed())
        control.seed([thread])
        self.assertFalse(control.snapshot()["tasks"])
        scheduled = [{"threadId": thread}, {"threadId": configured}]
        self.assertEqual(control.automatic_tasks(Mock(), scheduled, explicit_only=True), [])
        control.submit(thread, "focus")
        self.assertEqual(control.snapshot()["operations"][0]["status"], "queued")

    def test_claude_subprocess_drains_only_after_round_and_approval_end(self):
        import threading

        rpc = ClaudeCodeRPC.__new__(ClaudeCodeRPC)
        rpc.approvals = Approvals()
        rpc.closed = False
        rpc.lock = threading.RLock()
        thread = str(uuid4())
        session = SimpleNamespace(lock=threading.RLock(), active_turn="running", inputs={},
                                  pending_permissions=set(), interrupting=False, process=Mock())
        session.process.poll.side_effect = lambda: 0 if closed.called else None
        closed = Mock(side_effect=lambda: setattr(rpc, "closed", True))
        rpc.close = closed
        rpc.running = {thread: session}
        token = rpc.approvals.add(rpc, {
            "id": "fixture", "method": "item/commandExecution/requestApproval",
            "params": {"threadId": thread, "command": "fixture"},
        })
        self.assertFalse(rpc.drain_for_tray())
        session.active_turn = None
        self.assertFalse(rpc.drain_for_tray())
        rpc.approvals.items[token]["decision"] = "resolved"
        self.assertTrue(rpc.drain_for_tray())
        closed.assert_called_once()
