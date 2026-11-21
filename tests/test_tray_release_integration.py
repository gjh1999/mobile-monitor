"""Exercise tray handoff with two devices on an isolated personal coordinator."""

import socket
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from urllib.error import URLError
from unittest.mock import Mock, patch
from uuid import uuid4

import uvicorn

from automation import Approvals
from coordination_client import ControlClient, ControlDenied, PersonalTaskLocks
from coordination_server import create_app
from personal_memory import pack
from personal_relay import EMPTY, RemoteRecords, digest
from personal_sync import PersonalSync
from universal_rpc import CodexRPC


class TrayReleaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.endpoint = self.enterContext(socket.socket())
        self.endpoint.bind(("127.0.0.1", 0))
        self.url = "http://127.0.0.1:" + str(self.endpoint.getsockname()[1])
        devices = {name: {"token": name * 43, "projects": [], "personalSpace": "fixture"}
                   for name in ("a", "b")}
        self.server = uvicorn.Server(uvicorn.Config(
            create_app(self.root / "coordinator.sqlite3", devices, personal_only=True), log_level="error"
        ))
        worker = threading.Thread(target=self.server.run, kwargs={"sockets": [self.endpoint]}, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(setattr, self.server, "should_exit", True)
        self.wait_until(lambda: self.server.started)

        source, receiver = (ControlClient({"url": self.url, "device": name, "projects": {}}, name * 43)
                            for name in ("a", "b"))
        self.source, self.receiver = PersonalTaskLocks(source), PersonalTaskLocks(receiver)
        self.records = RemoteRecords(source)
        profile = self.root / "profile"
        self.flow = PersonalSync(profile, {}, home=profile / "codex")
        self.enterContext(patch.object(self.flow, "store", return_value=self.records))
        self.enterContext(patch.object(self.flow, "task_locks", return_value=self.source))

    def wait_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            self.assertLess(time.monotonic(), deadline, "等待隔离协调服务启动超时")
            time.sleep(0.02)

    def session(self, project):
        tid = str(uuid4())
        home = self.flow.home / "sessions/2026/11/21"
        home.mkdir(parents=True, exist_ok=True)
        path = home / f"rollout-2026-11-21T12-00-00-{tid}.jsonl"
        cwd = self.root / project
        cwd.mkdir(parents=True)
        turn = str(uuid4())
        events = (
            ("session_meta", {"id": tid, "cwd": str(cwd)}),
            ("event_msg", {"type": "task_started", "turn_id": turn}),
            ("event_msg", {"type": "task_complete", "turn_id": turn}),
        )
        content = b"".join(pack({"type": kind, "payload": fields}) + b"\n" for kind, fields in events)
        path.write_bytes(content)
        self.flow.threads[tid] = {"project": project, "cwd": str(cwd), "relative": ".", "path": str(path)}
        key = f"sessions/{project}/{path.name}"
        envelope = pack({"name": path.name, "cwd": "."}) + b"\n" + content
        return tid, key, envelope, digest(content)

    def claim(self, locks, tid, project, checkpoint=None):
        return locks.claim(tid, project, lambda _: checkpoint)

    def app_server(self, states):
        rpc = CodexRPC.__new__(CodexRPC)
        rpc.approvals = Approvals()
        rpc.write_lock = threading.Lock()
        rpc.loaded_threads = set(states)
        rpc.owned_threads = set(states)
        rpc.closed = False
        rpc.draining = False
        rpc.project_writes = None
        rpc.p = Mock()
        rpc.p.poll.side_effect = lambda: 0 if rpc.p.stdin.close.called else None
        rpc.p.wait.return_value = 0
        rpc.send = Mock()
        rpc.personal_flow = lambda: self.flow
        rpc.personal_session = self.flow.threads.get

        def call(method, params, timeout=20):
            self.assertEqual(method, "thread/read")
            self.assertFalse(rpc.closed)
            return {"thread": {"status": {"type": states[params["threadId"]]}}}

        rpc.call = call
        return rpc

    def test_waits_for_rpc_and_sync_then_releases_eligible_tasks_across_projects(self):
        from tray_release import CodexTrayRelease, release_step

        one, two = "owner/one", "owner/two"
        first, first_key, first_envelope, first_checksum = self.session(one)
        second, second_key, second_envelope, second_checksum = self.session(two)
        missing, other, editor = (str(uuid4()) for _ in range(3))
        self.claim(self.source, first, one)
        self.claim(self.source, second, two)
        self.claim(self.source, missing, "owner/missing-local")
        self.claim(self.receiver, other, "owner/remote")
        self.source.event(two, editor, "vscode", 1, "open")
        expected = {first: "a", second: "a", missing: "a", other: "b"}
        self.assertEqual(self.receiver.request()["tasks"], expected)

        states = {first: "active", second: "idle"}
        rpc = self.app_server(states)
        token = rpc.approvals.add(rpc, {
            "id": 1, "method": "item/commandExecution/requestApproval",
            "params": {"threadId": second, "command": "fixture"},
        })
        synchronized = Future()
        with patch.object(self.flow, "request_manual_sync", return_value=synchronized) as request_sync:
            release = CodexTrayRelease()
            step = lambda connection: release_step(lambda: release.step(connection, self.flow, []))
            with patch.object(self.source.client.opener, "open", side_effect=URLError("offline")):
                self.assertNotEqual(step(rpc)[0], "released")
            self.assertEqual(self.receiver.request()["tasks"], expected)

            step(rpc)
            states[first] = "idle"
            step(rpc)
            rpc.p.stdin.close.assert_not_called()
            request_sync.assert_not_called()
            self.assertEqual(self.receiver.request()["tasks"], expected)

            self.assertEqual(rpc.approvals.decide(token, False), "denied")
            step(rpc)
            rpc.p.stdin.close.assert_called_once()
            request_sync.assert_called_once_with(fresh=True)
            self.assertEqual(self.receiver.request()["tasks"], expected)

            self.records.put(first_key, EMPTY, first_envelope)
            self.records.put(second_key, EMPTY, second_envelope)
            self.flow.status.update({one: "已同步", two: "已同步"})
            synchronized.set_result({"status": self.flow.status.copy(), "memoryChecked": True,
                                     "connection": "已连接协调服务"})
            with patch.object(self.source.client.opener, "open", side_effect=URLError("offline again")):
                self.assertNotEqual(step(None)[0], "released")
            self.assertEqual(self.receiver.request()["tasks"], expected)

            state, pending, detail = step(None)
            self.assertEqual((state, pending), ("waiting", 1), detail)
            self.assertEqual(self.receiver.request()["tasks"], {missing: "a", other: "b"})
            self.assertEqual(self.receiver.request("/" + first),
                             {"device": None, "checkpoint": first_checksum})
            self.assertEqual(self.receiver.request("/" + second),
                             {"device": None, "checkpoint": second_checksum})
            self.claim(self.receiver, first, one, first_checksum)
            with self.assertRaises(ControlDenied):
                self.claim(self.receiver, second, two, second_checksum)
            self.source.event(two, editor, "vscode", 2, "close")
            self.claim(self.receiver, second, two, second_checksum)

            step(None)
            self.assertEqual(self.receiver.request()["tasks"], {first: "b", second: "b", missing: "a", other: "b"})
