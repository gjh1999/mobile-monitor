import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_activity import COMMAND_DELTA, MESSAGE_DELTA, OUTPUT_LIMIT, CodexActivity, terminal_text
from codex_history import read_codex_conversation
from conversation import MESSAGE_LIMIT, read_conversation
from reply_log import ReplyLog
from tests.test_codex_history import THREAD, OTHER, line, message


class CodexActivityTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict('os.environ', CODEX_HOME=str(self.home)))
        self.path = self.home / 'replies.sqlite3'
        self.activity = CodexActivity()
        self.log = ReplyLog(self.path, activity=self.activity)
        self.addCleanup(self.log.db.close)

    def record(self, method, *, thread=THREAD, **params):
        return self.log.record({'method': method, 'params': {'threadId': thread, 'turnId': 'turn', **params}}, {THREAD})

    def messages(self):
        return read_codex_conversation(self.path, THREAD, activity=self.activity.snapshot(THREAD))['messages']

    def test_streams_only_owned_items_replaces_output_and_does_not_journal_it(self):
        command = dict(id='cmd', type='commandExecution', command='python check.py', status='inProgress')
        self.record('item/started', item=command, thread=OTHER)
        self.record(COMMAND_DELTA, itemId='cmd', delta='wrong task', thread=OTHER)
        self.assertFalse(self.activity.snapshot(OTHER))
        self.record('item/started', item=command)
        self.assertEqual(self.messages()[0]['status'], 'inProgress')
        revision = self.activity.revision(THREAD)
        self.record(COMMAND_DELTA, itemId='cmd', delta='first\n')
        self.record(COMMAND_DELTA, itemId='cmd', delta='second')
        self.assertGreater(self.activity.revision(THREAD), revision)
        self.assertEqual(self.messages()[0]['text'], 'first\nsecond')
        self.record(MESSAGE_DELTA, itemId='reply', delta='**Progress')
        self.assertEqual(self.messages()[-1]['text'], '**Progress')
        self.record('item/completed', item={**command, 'status': 'completed', 'aggregatedOutput': 'final',
                                            'exitCode': 0, 'durationMs': 1630})
        self.record('item/completed', item=dict(type='agentMessage', id='reply', text='**Progress complete**'))
        self.record(COMMAND_DELTA, itemId='cmd', delta='late')
        self.assertEqual([(m['role'], m['text']) for m in self.messages()],
                         [('process', 'final'), ('assistant', '**Progress complete**')])
        journal = read_conversation(self.path, THREAD)
        self.assertEqual([m['role'] for m in journal['messages']], ['assistant'])
        self.assertEqual(self.messages()[0]['durationMs'], 1630)

    def test_native_command_is_desktop_only_and_survives_restart_until_rollback(self):
        sessions = self.home / 'sessions'
        sessions.mkdir()
        rollout = sessions / f'rollout-2026-11-20T09-00-00-{THREAD}.jsonl'
        payload = dict(type='item_completed', thread_id=THREAD, turn_id='turn',
                       item=dict(type='CommandExecution', id='cmd', command=['python', 'check.py'],
                                 status='failed', aggregated_output='failed\n', exit_code=2,
                                 duration={'secs': 1, 'nanos': 630000000}))
        rollout.write_bytes(line('session_meta', {'id': THREAD}) + message('turn', 'user', 'test it', 'user', 1)
                            + line('event_msg', payload, 2) + message('turn', 'assistant', 'result', 'reply', 3))
        self.record('item/started', item=dict(id='cmd', type='commandExecution', command='python check.py',
                                             status='inProgress'))
        result = self.messages()
        self.assertEqual([m['role'] for m in result], ['user', 'process', 'assistant'])
        self.assertEqual((result[1]['status'], result[1]['exitCode'], result[1]['durationMs']), ('failed', 2, 1630))
        self.assertEqual([m['role'] for m in read_codex_conversation(self.path, THREAD)['messages']], ['user', 'assistant'])
        self.activity = CodexActivity()
        self.assertEqual(self.messages(), result)
        with rollout.open('ab') as stream:
            stream.write(line('event_msg', {'type': 'thread_rolled_back', 'num_turns': 1}, 4))
        self.assertEqual(self.messages(), [])

    def test_large_stream_is_bounded_and_disconnect_closes_pending_display(self):
        self.record(COMMAND_DELTA, itemId='cmd', delta='x' * OUTPUT_LIMIT + ' tail')
        output = self.messages()[0]
        self.assertTrue(output['truncated'])
        self.assertEqual(len(output['text']), OUTPUT_LIMIT)
        self.assertTrue(output['text'].endswith(' tail'))
        self.activity.disconnect()
        self.assertEqual(self.messages()[0]['status'], 'unknown')
        self.assertEqual(terminal_text('\x1b[33mready\x1b[0m\r\n\x1b]8;;https://x\x07log'), 'ready\nlog')

    def test_legacy_reply_deduplicates_stream_and_journal(self):
        sessions = self.home / 'sessions'
        sessions.mkdir()
        rollout = sessions / f'rollout-2026-11-20T09-00-00-{THREAD}.jsonl'
        rollout.write_bytes(line('session_meta', {'id': THREAD})
                            + line('event_msg', {'type': 'agent_message', 'message': 'reply', 'turn_id': 'turn'}))
        self.record(MESSAGE_DELTA, itemId='reply', delta='reply')
        self.record('item/completed', item=dict(type='agentMessage', id='reply', text='reply'))
        self.assertEqual([item['text'] for item in self.messages()], ['reply'])

    def test_commands_leave_an_unflushed_assistant_delta_in_live_cache(self):
        self.record(MESSAGE_DELTA, itemId='reply', delta='still writing')
        for number in range(MESSAGE_LIMIT + 1):
            self.record('item/completed', item=dict(id=f'cmd-{number}', type='commandExecution',
                                                     status='completed', command='fixture'))
        active = self.activity.snapshot(THREAD)
        self.assertEqual(sum(item['role'] == 'process' for item in active), MESSAGE_LIMIT)
        self.assertEqual([item['text'] for item in active if item['role'] == 'assistant'], ['still writing'])
