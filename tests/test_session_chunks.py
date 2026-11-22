import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from handoff import session_location
from personal_memory import Mirror, confirm_published_session, sync_memories
from personal_relay import MAX_RECORD, Records, digest
from session_chunks import CHUNKS, manifest_key


def event(kind, payload):
    return json.dumps({"type": kind, "payload": payload}).encode() + b"\n"


def turn(label, size=0):
    return (event("event_msg", {"type": "task_started", "turn_id": label})
            + event("response_item", {"text": label * size})
            + event("event_msg", {"type": "task_complete", "turn_id": label}))


class ChunkSessionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = Records(self.root / "exchange.sqlite", "space")
        self.tid = str(uuid.uuid4())
        self.filename = f"rollout-2026-11-19T09-00-00-{self.tid}.jsonl"
        self.location = session_location(self.filename, self.tid)
        self.project = "owner/project"
        self.key = f"sessions/{self.project}/{self.filename}"
        self.meta = event("session_meta", {"id": self.tid, "cwd": str(self.root / "source/project")})
        self.short = self.meta + turn("a")
        self.long = self.short + turn("b", MAX_RECORD + 27)
        self.cache = {}

    def native(self, who):
        return self.root / who / "codex" / self.location

    def cycle(self, who):
        state = self.root / who
        project = state / "project"
        project.mkdir(parents=True, exist_ok=True)
        home = state / "codex"
        home.mkdir(exist_ok=True)
        errors = []
        mirror = Mirror(state / "state", self.store, self.cache.setdefault(who, {}))
        try:
            sessions = sync_memories(mirror, self.store.index(), home, {self.project: (project, "memory")},
                                     mirror.cache, lambda key, err: errors.append(str(err)), threading.Event())
        finally:
            mirror.db.close()
        return sessions, errors

    def save(self, who, value):
        path = self.native(who)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    def test_legacy_promotion_resumable_verified_install_and_reuse(self):
        self.save("source", self.short)
        self.assertFalse(self.cycle("source")[1])
        self.assertFalse(self.cycle("receiver")[1])
        original = self.store.read(self.key)
        self.save("source", self.long)
        writes = []
        put = self.store.put
        def broken_put(key, previous, data, **kwargs):
            if key.startswith(CHUNKS):
                writes.append(key)
                if len(writes) == 2:
                    raise OSError("interrupted upload")
            return put(key, previous, data, **kwargs)
        with patch.object(self.store, "put", side_effect=broken_put):
            self.assertIn("interrupted upload", self.cycle("source")[1])
        self.assertNotIn(manifest_key(self.key), self.store.index())
        with patch.object(self.store, "put", wraps=put) as retry:
            sessions, errors = self.cycle("source")
            self.assertFalse(errors)
            self.assertNotIn(writes[0], [call.args[0] for call in retry.call_args_list])
        self.assertEqual(self.store.read(self.key), original)
        self.assertEqual(confirm_published_session(self.store, sessions[self.tid], self.tid), digest(self.long))

        read = self.store.read
        reads = []
        def corrupt_second(key):
            if key.startswith(CHUNKS):
                reads.append(key)
                if len(reads) == 2:
                    return b"damaged payload"
            return read(key)
        with patch.object(self.store, "read", side_effect=corrupt_second):
            self.assertTrue(any("校验失败" in err for err in self.cycle("receiver")[1]))
        self.assertEqual(self.native("receiver").read_bytes(), self.short)
        self.cache.pop("receiver")
        with patch.object(self.store, "read", wraps=read) as retry:
            sessions, errors = self.cycle("receiver")
            self.assertFalse(errors)
            self.assertNotIn(reads[0], [call.args[0] for call in retry.call_args_list])
        self.assertEqual(self.native("receiver").read_bytes(), self.long)
        self.assertEqual(sessions[self.tid]["cwd"], str(self.root / "receiver/project"))
        with patch.object(self.store, "read", wraps=read) as retry:
            self.assertFalse(self.cycle("receiver")[1])
            self.assertNotIn(manifest_key(self.key), [c.args[0] for c in retry.call_args_list])

    def test_fork_or_corrupt_manifest_does_not_replace_current_history(self):
        self.save("source", self.short)
        self.assertFalse(self.cycle("source")[1])
        self.assertFalse(self.cycle("other")[1])
        self.save("source", self.long)
        self.assertFalse(self.cycle("source")[1])
        self.save("other", self.short + turn("c", MAX_RECORD + 30))
        prior = self.native("other").read_bytes()
        self.assertTrue(any("分叉" in err for err in self.cycle("other")[1]))
        self.assertEqual(prior, self.native("other").read_bytes())
        mkey = manifest_key(self.key)
        manifest = json.loads(self.store.read(mkey))
        manifest["sha256"] = "0" * 64
        self.store.put(mkey, self.store.index()[mkey], json.dumps(manifest).encode())
        self.assertTrue(any("完整会话校验失败" in err for err in self.cycle("new")[1]))
        self.assertFalse(self.native("new").exists())
