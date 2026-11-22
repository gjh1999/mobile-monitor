import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from fastapi.testclient import TestClient

from coordination_server import create_app
from personal_relay import (
    EMPTY, RETIREMENT_ACK_PREFIX, RETIREMENT_PREFIX, RETIREMENT_SOURCES_PREFIX,
    Changed, Records, RemoteRecords, digest, retirement_markdown,
)
from session_chunks import CHUNKS, manifest_key


THREAD = '00000000-0000-0000-0000-000000000001'
OTHER = '00000000-0000-0000-0000-000000000002'
MARKER = RETIREMENT_PREFIX + THREAD
ACK = RETIREMENT_ACK_PREFIX + THREAD + '/B'
METADATA = RETIREMENT_SOURCES_PREFIX + THREAD


def session_key(thread, project='owner/project'):
    return f'sessions/{project}/rollout-2026-11-22T09-10-11-{thread}.jsonl'


def marker(markdown='# 会话摘要\n已完成检查。\n'):
    return json.dumps({'v': 1, 'markdown': markdown, 'sha256': digest(markdown.encode())}, ensure_ascii=False).encode()


class RetirementServiceTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.database = root / 'records.sqlite3'
        self.store = Records(self.database, 'private-space', 'A')

    def upload_part(self, data, thread=None):
        sha = digest(data)
        self.store.put(CHUNKS + sha, EMPTY, data, session_id=thread)
        return sha

    def write_manifest(self, thread, parts):
        name = manifest_key(session_key(thread))
        self.store.put(name, self.store.index().get(name, EMPTY), json.dumps({'v': 1, 'parts': parts}).encode())

    def test_atomic_purge_rejects_stale_uploads_and_preserves_shared_parts(self):
        original = session_key(THREAD)
        alternate = session_key(THREAD, '.folder/local')
        long_term = f'long-term/owner/project/{THREAD}'
        note = f'notes/owner/project/{THREAD}.md'
        for key in (original, alternate, long_term, note, session_key(OTHER)):
            self.store.put(key, EMPTY, b'payload')
        another_space = Records(self.database, 'different-space', 'A')
        another_space.put(original, EMPTY, b'other workspace')
        shared = self.upload_part(b'shared')
        superseded = self.upload_part(b'previous version')
        current = self.upload_part(b'current version')
        partial = self.upload_part(b'unfinished upload', THREAD)
        self.write_manifest(OTHER, [shared])
        self.write_manifest(THREAD, [shared, superseded])
        self.write_manifest(THREAD, [shared, current])

        self.store.put(MARKER, EMPTY, marker())
        self.store.put(MARKER, EMPTY, marker())

        records = self.store.index()
        removed = (original, alternate, long_term, manifest_key(original),
                   *(CHUNKS + sha for sha in (superseded, current, partial)))
        self.assertTrue(all(key not in records and self.store.read(key) is None for key in removed))
        self.assertEqual(self.store.read(CHUNKS + shared), b'shared')
        self.assertEqual(self.store.read(note), b'payload')
        self.assertEqual(self.store.read(session_key(OTHER)), b'payload')
        self.assertEqual(another_space.read(original), b'other workspace')
        self.assertEqual(retirement_markdown(self.store.read(MARKER)), '# 会话摘要\n已完成检查。\n')
        self.assertEqual(json.loads(self.store.read(METADATA)), {
            'v': 1, 'partDirectories': sorted(digest(manifest_key(key).encode())[:16] for key in (original, alternate)),
        })
        for key in (original, alternate, long_term, manifest_key(original)):
            with self.subTest(key=key), self.assertRaises(Changed):
                self.store.put(key, EMPTY, b'old device payload')
        with self.assertRaises(Changed):
            self.store.put(CHUNKS + current, EMPTY, b'current version')
        with self.assertRaises(Changed):
            self.store.put(CHUNKS + digest(b'new part'), EMPTY, b'new part', session_id=THREAD)
        with self.assertRaises(Changed):
            self.write_manifest(OTHER, [shared, current])
        self.assertEqual(self.upload_part(b'current version', OTHER), current)
        self.write_manifest(OTHER, [shared, current])

    def test_write_racing_retirement_never_reinstates_session(self):
        name = session_key(THREAD)
        barrier = threading.Barrier(2)

        def retire():
            barrier.wait(timeout=2)
            self.store.put(MARKER, EMPTY, marker())

        def upload():
            barrier.wait(timeout=2)
            try:
                self.store.put(name, EMPTY, b'concurrent original')
            except Changed:
                pass

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(action) for action in (retire, upload)]
            for future in futures:
                future.result(timeout=10)
        self.assertIsNone(self.store.read(name))
        self.assertEqual(self.store.read(MARKER), marker())

    def test_route_protects_marker_ownership_receipts_and_task_claims(self):
        identities = {device: {'token': device.lower() * 43, 'projects': [],
                               'personalSpace': 'private-space' if device in ('A', 'B') else 'other-space'}
                      for device in ('A', 'B', 'C')}

        def auth(device):
            return {'Authorization': 'Bearer ' + device.lower() * 43, 'X-Lanxi-Device': device}

        base = '/api/v1/personal/'
        self.store.put(session_key(THREAD), EMPTY, b'original')
        with TestClient(create_app(self.database, identities, personal_only=True)) as api:
            def put(key, data, device='A', previous=EMPTY):
                return api.put(base + 'record', params={'key': key, 'previous': previous},
                               content=data, headers=auth(device))

            capability = base + 'retirement-supported'
            self.assertEqual(api.get(capability).status_code, 403)
            self.assertEqual(api.get(capability, headers=auth('A')).json(), {'v': 1})
            self.assertEqual(put(ACK, b'{"v":1}', 'B').status_code, 409)
            self.assertEqual(put(MARKER, b'invalid').status_code, 422)
            self.assertEqual(self.store.read(session_key(THREAD)), b'original')
            claim = {'project': 'owner/project'}
            task = base + 'tasks/' + THREAD
            self.assertEqual(api.post(task + '/claim', json=claim, headers=auth('B')).status_code, 200)
            self.assertEqual(put(MARKER, marker()).status_code, 409)
            self.assertEqual(self.store.read(session_key(THREAD)), b'original')
            self.assertEqual(put(MARKER, marker(), 'B').status_code, 204)
            self.assertEqual(put(MARKER, marker()).status_code, 204)
            self.assertEqual(put(MARKER, marker('different'), previous=digest(marker())).status_code, 409)
            self.assertEqual(api.delete(base + 'record', params={'key': MARKER, 'previous': digest(marker())},
                                        headers=auth('B')).status_code, 409)
            self.assertEqual(put(ACK, b'{"v":1}').status_code, 422)
            self.assertEqual(put(ACK, b'{"v":1}', 'B').status_code, 204)
            self.assertEqual(put(ACK, b'{"v":1}', 'B').status_code, 204)
            self.assertEqual(put(METADATA, b'modified').status_code, 422)
            self.assertIn(ACK, api.get(base + 'index', headers=auth('A')).json())
            self.assertNotIn(MARKER, api.get(base + 'index', headers=auth('C')).json())
            self.assertIsNone(api.get(task, headers=auth('A')).json()['device'])
            self.assertEqual(api.post(task + '/claim', json=claim, headers=auth('A')).status_code, 409)
            self.assertEqual(api.post(base + 'tasks/' + OTHER + '/claim', json=claim, headers=auth('A')).status_code, 200)

    def test_old_server_capability_probe_returns_false_without_trying_to_delete(self):
        records = RemoteRecords(None)
        error = HTTPError('http://test', 404, 'Not found', None, None)
        with patch.object(records, 'request', side_effect=error) as request:
            self.assertFalse(records.retirement_supported())
            request.assert_called_once_with('GET', 'retirement-supported')
