import json
import tempfile
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import Approvals
from personal_relay import EMPTY, digest
from rpc_errors import RPCNotSent
from session_retirement import local_version, transfer_and_retire
from task_control import TaskControl


SOURCE = '01a11111-1111-7111-8111-111111111111'
TARGET = '01a22222-2222-7222-8222-222222222222'
OTHER = '01a33333-3333-7333-8333-333333333333'
SUMMARY = '# 摘要\n用户已确认合并上下文。'


class Store:
    def __init__(self, on_publish):
        self.records = {}
        self.on_publish = on_publish
        self.writes = 0

    def retirement_supported(self):
        return True

    def index(self):
        return {key: digest(data) for key, data in self.records.items()}

    def put(self, key, previous, data):
        if self.index().get(key, EMPTY) != previous:
            raise AssertionError('unexpected record revision')
        if key in self.records:
            if self.records[key] != data:
                raise AssertionError('retirement record changed')
            return
        self.on_publish()
        self.records[key] = data
        self.writes += 1

    def read(self, key):
        return self.records.get(key)


class Native:
    def __init__(self, target):
        self.target = target
        self.closed = False
        self.owned_threads = set()
        self.approvals = Approvals()
        self.activity = 'idle'
        self.behavior = 'success'
        self.calls = []
        self.item = None
        self.before_send = lambda: None

    def append_item(self):
        row = {'type': 'response_item', 'payload': self.item}
        with self.target.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')

    def call(self, method, params):
        self.calls.append(method)
        if method in ('thread/read', 'thread/resume'):
            if params.get('_sendGuard'):
                params['_sendGuard']()
            return {'thread': {'id': params['threadId'], 'status': {'type': self.activity}}}
        if method == 'thread/inject_items':
            self.before_send()
            params['_sendGuard']()
            self.item = params['items'][0]
            if self.behavior == 'unsent':
                raise RPCNotSent()
            if self.behavior != 'unconfirmed':
                self.append_item()
            if self.behavior in ('unconfirmed', 'lost_reply'):
                raise TimeoutError()
        return {}


class SessionTransferTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        sandbox = Path(folder.name)
        self.home = sandbox / 'home'
        self.root = sandbox / 'data'
        self.root.mkdir()
        sessions = self.home / 'sessions'
        sessions.mkdir(parents=True)
        self.source_path = sessions / f'rollout-2026-11-20T10-00-00-{SOURCE}.jsonl'
        self.source_path.write_bytes(b'{}\n')
        self.target_path = sessions / f'rollout-2026-11-20T10-00-00-{TARGET}.jsonl'
        self.target_path.write_bytes(b'{}\n')
        self.version = local_version(self.home, SOURCE)
        self.assertIsNotNone(self.version)
        self.client = TaskControl(self.root / 'tasks.sqlite3')
        self.addCleanup(self.client.db.close)
        self.rpc = Native(self.target_path)
        self.client.tick(self.rpc)
        self.server = Store(self.assert_context_before_tombstone)
        self.source_holder = 'here'
        self.locks = SimpleNamespace(client=SimpleNamespace(device='here'),
                                     request=lambda _: {'device': self.source_holder})
        self.flow = SimpleNamespace(home=self.home, root=self.root, wake=threading.Event(),
                                    store=lambda: self.server, task_locks=lambda: self.locks)
        lock = patch('session_retirement.native_publication_lock', return_value=nullcontext())
        lock.start()
        self.addCleanup(lock.stop)

    def assert_context_before_tombstone(self):
        self.assertTrue(self.source_path.is_file())
        self.assertIn(digest(SUMMARY.encode()), self.target_path.read_text(encoding='utf-8'))
        self.assertEqual((self.root / '会话摘要' / f'{SOURCE}.md').read_text(encoding='utf-8'), SUMMARY)

    def transfer(self, controller=None, **changes):
        values = dict(flow=self.flow, controller=self.client if controller is None else controller, source=SOURCE,
                      target=TARGET, markdown=SUMMARY, version=self.version)
        return transfer_and_retire(**(values | changes))

    def test_append_context_and_then_retire_without_turn_or_duplicate(self):
        document = self.transfer()
        self.assertEqual(document.read_text(encoding='utf-8'), SUMMARY)
        self.assertIn('thread/resume', self.rpc.calls)
        self.assertNotIn('turn/start', self.rpc.calls)
        self.assertTrue(self.flow.wake.is_set())
        self.source_path.unlink()
        self.assertEqual(self.transfer(), document)
        self.assertEqual(self.rpc.calls.count('thread/inject_items'), 1)

    def test_lost_confirmation_with_persisted_context_can_continue(self):
        self.rpc.behavior = 'lost_reply'
        self.assertTrue(self.transfer().is_file())
        self.assertEqual(self.rpc.calls.count('thread/inject_items'), 1)

    def test_unknown_write_preserves_original_and_only_checks_again(self):
        self.rpc.behavior = 'unconfirmed'
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, '尚未在目标会话确认'):
                self.transfer()
        self.assertEqual(self.rpc.calls.count('thread/inject_items'), 1)
        self.assertFalse(self.server.records)
        self.assertTrue(self.source_path.is_file())
        with self.assertRaisesRegex(ValueError, '相同的目标和摘要'):
            self.transfer(target=OTHER)
        self.rpc.append_item()
        self.assertTrue(self.transfer().is_file())
        self.assertEqual(self.rpc.calls.count('thread/inject_items'), 1)

    def test_definitively_unsent_can_retry(self):
        self.rpc.behavior = 'unsent'
        with self.assertRaises(RPCNotSent):
            self.transfer()
        self.assertFalse(self.server.records)
        self.rpc.behavior = 'success'
        self.assertTrue(self.transfer().is_file())

    def test_target_busy_remote_source_owner_and_tray_keep_original(self):
        self.source_holder = 'peer'
        with self.assertRaisesRegex(ValueError, '另一台电脑'):
            self.transfer()
        self.source_holder = 'here'
        self.rpc.activity = 'active'
        with self.assertRaisesRegex(ValueError, '仍在执行'):
            self.transfer()
        self.rpc.activity = 'idle'
        self.client.control_allowed = lambda: False
        with self.assertRaisesRegex(ValueError, '控制暂不可用'):
            self.transfer()
        self.assertNotIn('thread/inject_items', self.rpc.calls)
        self.assertTrue(self.source_path.is_file())
        self.assertFalse(self.server.records)

    def test_source_modified_after_context_write_is_not_retired(self):
        self.rpc.before_send = lambda: self.source_path.write_bytes(b'{}\n{}\n')
        with self.assertRaisesRegex(ValueError, '会话在生成摘要后改变了'):
            self.transfer()
        self.assertEqual(self.rpc.calls.count('thread/inject_items'), 1)
        self.assertFalse(self.server.records)
        self.assertTrue(self.source_path.is_file())


if __name__ == '__main__':
    unittest.main()
