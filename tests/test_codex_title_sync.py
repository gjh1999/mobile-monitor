import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from coordination_client import ControlDenied
from task_control import TaskControl


THREAD = '11111111-1111-4111-8111-111111111111'


class CodexConnection:
    client_type = 'codex'
    closed = False

    def __init__(self, name):
        self.name = name
        self.owned_threads = set()
        self.writes = []
        self.before_write = None
        self.after_write = None

    def call(self, method, params, **options):
        if method == 'thread/read':
            return {'thread': {'id': THREAD, 'name': self.name}}
        if method == 'thread/name/set':
            if self.before_write:
                self.before_write()
            if not params['_sendGuard']():
                raise ControlDenied(detail='Write guard rejected')
            self.name = params['name']
            self.writes.append(self.name)
            if self.after_write:
                self.after_write()
            return {}
        raise AssertionError(method)


class CodexTitleSyncTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.open_control()
        self.rpc = CodexConnection('original')

    def open_control(self):
        self.control = TaskControl(self.root / 'control.sqlite3')
        self.addCleanup(self.control.db.close)

    def rename(self, title, base='original'):
        return self.control.submit(THREAD, 'rename_codex', json.dumps({'title': title, 'base': base}))

    def sync(self, *, owned=True):
        self.control.connection = self.rpc
        self.rpc.owned_threads = {THREAD} if owned else set()
        self.control.runtime[THREAD] = {'canControl': owned}
        self.control._sync_codex_title(self.rpc)

    def state(self):
        return self.control.snapshot()['titleSync'].get(THREAD)

    def test_persists_local_title_and_waits_for_write_permission(self):
        revision = self.rename('new')
        self.sync(owned=False)
        self.assertEqual(self.rpc.writes, [])
        self.open_control()
        self.assertEqual(self.state()['revision'], revision)
        self.assertEqual(self.control.snapshot()['names'][THREAD], 'new')
        self.sync()
        self.assertIsNone(self.state())
        self.assertEqual(self.rpc.writes, ['new'])

    def test_external_title_conflict_requires_revision_matched_choice(self):
        self.rename('local')
        self.rpc.name = 'external'
        self.sync()
        conflict = self.state()
        self.assertEqual((conflict['state'], conflict['nativeTitle']), ('conflict', 'external'))
        self.assertEqual(self.rpc.writes, [])
        with self.assertRaises(ValueError):
            self.control.submit(THREAD, 'resolve_codex_title', json.dumps({'revision': 'old', 'useNative': True}))
        self.control.submit(THREAD, 'resolve_codex_title',
                            json.dumps({'revision': conflict['revision'], 'useNative': False}))
        self.sync()
        self.assertEqual(self.rpc.writes, ['local'])
        self.assertIsNone(self.state())

    def test_late_old_confirmation_does_not_clear_new_title(self):
        self.rename('first')
        self.rpc.after_write = lambda: self.rename('latest')
        self.sync()
        self.assertEqual(self.state()['title'], 'latest')
        self.rpc.after_write = None
        self.sync()
        self.assertEqual(self.rpc.writes, ['first', 'latest'])
        self.assertIsNone(self.state())

    def test_new_revision_blocks_old_write_and_timeout_reads_before_retry(self):
        self.rename('old pending')
        self.rpc.before_write = lambda: self.rename('latest')
        self.sync()
        self.assertEqual(self.rpc.writes, [])
        self.rpc.before_write = None
        def lose_response():
            raise TimeoutError('reply lost')
        self.rpc.after_write = lose_response
        self.sync()
        self.assertEqual(self.state()['title'], 'latest')
        self.rpc.after_write = None
        with patch('task_control.time.monotonic', return_value=self.control.title_retry[THREAD] + 1):
            self.sync()
        self.assertIsNone(self.state())
        self.assertEqual(self.rpc.writes, ['latest'])
