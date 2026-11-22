import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

import uvicorn

from coordination_client import ControlClient
from coordination_server import create_app
from personal_memory import MEMORY_FIELDS, Mirror, pack, sync_memories
from personal_relay import (
    EMPTY, RETIREMENT_ACK_PREFIX, RETIREMENT_PREFIX, RETIREMENT_SOURCES_PREFIX, Changed, RemoteRecords, digest,
)
from personal_sync import PersonalSync, save_retirement_summary
from project_library import set_project_root


THREAD = "bd0668a8-d3c8-4514-8d98-ae87f46bfb55"
OTHER = "bd0668a8-d3c8-4514-8d98-ae87f46bfb56"
ROLLOUT = f"rollout-2026-11-22T10-00-00-{THREAD}.jsonl"
SUMMARY = "# 任务摘要\n\n待补一项说明。\n"
PART_DIRECTORY = "0123456789abcdef"


class Relay(RemoteRecords):
    def __init__(self, supported=True):
        self.client = SimpleNamespace(device="desktop")
        self.supported = supported
        self.data = {
            RETIREMENT_PREFIX + THREAD: pack({"v": 1, "markdown": SUMMARY, "sha256": digest(SUMMARY.encode())}),
            RETIREMENT_SOURCES_PREFIX + THREAD: pack({"v": 1, "partDirectories": [PART_DIRECTORY]}),
            f"{RETIREMENT_ACK_PREFIX}{THREAD}/laptop": pack({"v": 1}),
        }

    def retirement_supported(self):
        return self.supported

    def index(self):
        return {key: digest(value) for key, value in self.data.items()}

    def read(self, key):
        return self.data[key]

    def put(self, key, previous, data):
        if previous != digest(self.data.get(key)):
            raise Changed("changed")
        self.data[key] = data


class SessionRetirementSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "codex"
        self.home.mkdir()
        self.sync = PersonalSync(self.root, {}, home=self.home)
        self.relay = Relay()
        self.stop = threading.Event()
        self.errors = {}
        self.notes = self.root / "project" / "notes" / ".lanxi"
        self.projects = {"owner/repo": (self.root / "project", "notes")}
        self.summary = self.root / "会话摘要" / f"{THREAD}.md"
        self.ack = f"{RETIREMENT_ACK_PREFIX}{THREAD}/desktop"

    def prepare(self):
        return self.sync._prepare_retirements(self.relay, self.relay.index(), self.errors, self.stop)

    def finish(self, rows, pending):
        self.sync._finish_retirements(self.relay, rows, pending, self.projects, self.errors, self.stop)

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_save_and_retry_precede_delete_and_ack(self):
        def busy(*_):
            self.assertEqual(self.summary.read_text(encoding="utf-8"), SUMMARY)
            raise ValueError("会话正在运行")

        self.sync.retirement_delete = busy
        rows, pending = self.prepare()
        self.assertEqual(rows[THREAD]["status"], "会话正在运行")
        self.assertFalse(pending)
        self.assertNotIn(self.ack, self.relay.data)

        metadata = pack({"type": "session_meta", "payload": {"id": THREAD, "cwd": str(self.root)}}) + b"\n"
        envelope = pack({"name": ROLLOUT, "cwd": "."}) + b"\n" + metadata
        copy = self.write(self.notes / "sessions" / ROLLOUT, envelope)
        row = dict.fromkeys(MEMORY_FIELDS, "")
        row["thread_id"] = THREAD
        memory = self.write(self.notes / "long-term" / f"{THREAD}.json", pack(row))
        backups = self.sync.local_state / "memory-backups"
        old = self.write(backups / digest(envelope), envelope)
        unrelated = self.write(backups / digest(b"keep this note"), b"keep this note")
        handoff = self.write(self.home / "lanxi-handoff-backups" / THREAD / (digest(metadata) + ".jsonl"), metadata)
        part = self.write(self.sync.local_state / "parts" / PART_DIRECTORY / ("1" * 24), b"old chunk")
        with closing(sqlite3.connect(self.home / "memories_1.sqlite")) as db, db:
            db.execute("CREATE TABLE stage1_outputs(thread_id TEXT)")
            db.executemany("INSERT INTO stage1_outputs VALUES (?)", [(THREAD,), (OTHER,)])

        self.sync.retirement_delete = lambda *args: self.assertEqual(args, (self.root, self.home, THREAD))
        rows, pending = self.prepare()
        self.assertNotIn(self.ack, self.relay.data)
        self.finish(rows, pending)
        self.assertEqual(self.relay.data[self.ack], pack({"v": 1}))
        for removed in (copy, memory, old, handoff, part):
            self.assertFalse(removed.exists(), removed)
        self.assertTrue(unrelated.is_file())
        with closing(sqlite3.connect(self.home / "memories_1.sqlite")) as db:
            self.assertEqual(db.execute("SELECT thread_id FROM stage1_outputs").fetchall(), [(OTHER,)])
        self.assertEqual(self.sync.snapshot()["retirements"][THREAD]["acknowledged"], ["desktop", "laptop"])
        self.sync.retirement_delete = lambda *_: self.fail("an acknowledged deletion must not be repeated")
        self.assertFalse(self.prepare()[1])

    def test_unsupported_server_and_conflicting_document_preserve_session(self):
        self.sync.retirement_delete = lambda *_: self.fail("the original conversation must be preserved")
        self.relay.supported = False
        self.assertEqual(self.prepare(), ({}, {}))
        self.assertFalse(self.summary.exists())
        self.assertFalse(self.sync.snapshot()["retirementSupported"])
        self.relay.supported = True
        self.write(self.summary, b"keep my document")
        rows, pending = self.prepare()
        self.assertFalse(pending)
        self.assertNotIn(self.ack, self.relay.data)
        self.assertIn("同名", rows[THREAD]["status"])
        self.assertEqual(self.summary.read_bytes(), b"keep my document")
        with self.assertRaises(ValueError):
            save_retirement_summary(self.root, OTHER, SUMMARY, EMPTY)

    def test_stale_session_files_are_neither_uploaded_nor_installed(self):
        self.write(self.notes / "sessions" / ROLLOUT, b"unreadable stale local replica")
        self.write(self.home / "sessions" / ROLLOUT, b"unreadable stale native replica")
        local = self.sync.local_state / "memory"
        mirror = Mirror(local, self.relay)
        self.addCleanup(mirror.db.close)
        before = dict(self.relay.data)
        index = self.relay.index()
        index.update({f"sessions/owner/repo/{ROLLOUT}": "old", f"long-term/owner/repo/{THREAD}": "old"})
        reports = []
        sessions = sync_memories(mirror, index, self.home, self.projects, {}, lambda *error: reports.append(error),
                                 self.stop, retired={THREAD})
        self.assertEqual(sessions, {})
        self.assertEqual(reports, [])
        self.assertEqual(self.relay.data, before)

    def test_paired_device_rejoins_after_retirement(self):
        devices = {name: {"token": token, "personalSpace": "fixture-owner", "projects": []}
                   for name, token in (("desktop", "a" * 43), ("laptop", "b" * 43))}
        app = create_app(self.root / "relay.sqlite3", devices, personal_only=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off", ws="none"))
            worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            worker.start()
            try:
                deadline = time.monotonic() + 5
                while not server.started and worker.is_alive() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(server.started)
                self._restore_offline_device(url, devices)
            finally:
                server.should_exit = True
                worker.join(8)
                self.assertFalse(worker.is_alive())

    def _restore_offline_device(self, url, devices):
        workers = {}
        for name, spec in devices.items():
            config = {"url": url, "device": name, "projects": {}}
            relay = RemoteRecords(ControlClient(config, spec["token"]))
            root = self.root / name
            home = root / "code-home"
            home.mkdir(parents=True)
            library = root / "projects"
            library.mkdir()
            set_project_root(root, library)
            flow = PersonalSync(root, {}, config, home)
            native = self.write(home / "sessions" / ROLLOUT, b"native conversation test copy")

            def delete(root, home, tid):
                self.assertEqual((root / "会话摘要" / f"{tid}.md").read_bytes(), SUMMARY.encode())
                (home / "sessions" / ROLLOUT).unlink()

            flow.retirement_delete = delete
            workers[name] = flow, relay, native
        sender, relay_a, source = workers["desktop"]
        receiver, relay_b, copy = workers["laptop"]
        retired_key = f"sessions/example/demo/{ROLLOUT}"
        relay_a.put(retired_key, EMPTY, b"server source transcript")
        relay_a.put(RETIREMENT_PREFIX + THREAD, EMPTY, self.relay.data[RETIREMENT_PREFIX + THREAD])
        with self.assertRaises(HTTPError) as missing:
            relay_a.read(retired_key)
        self.assertEqual(missing.exception.code, 404)
        with patch.object(sender, "store", return_value=relay_a), patch.object(sender, "code_backups", return_value=None):
            sender.cycle(self.stop)
        self.assertFalse(source.exists())
        self.assertTrue(copy.is_file())
        self.assertEqual(sender.snapshot()["retirements"][THREAD]["acknowledged"], ["desktop"])
        with self.assertRaises(Changed):
            relay_b.put(retired_key, EMPTY, b"offline rollout")
        with patch.object(receiver, "store", return_value=relay_b), patch.object(receiver, "code_backups", return_value=None):
            receiver.cycle(self.stop)
        self.assertFalse(copy.exists())
        self.assertEqual(receiver.snapshot()["retirements"][THREAD]["acknowledged"], ["desktop", "laptop"])
