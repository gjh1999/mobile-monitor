"""Bounded local view of Codex's public message and command streams."""
import re
import subprocess
import threading
import time
from collections import OrderedDict

from conversation import MESSAGE_LIMIT, TEXT_LIMIT


COMMAND_LIMIT = 1800
OUTPUT_LIMIT = 6000
THREAD_LIMIT = 12
COMMAND_DELTA = 'item/commandExecution/outputDelta'
MESSAGE_DELTA = 'item/agentMessage/delta'
DELTAS = {COMMAND_DELTA: 'process', MESSAGE_DELTA: 'assistant'}
ESCAPES = re.compile(r'\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-_])')
CONTROLS = re.compile(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]')


def terminal_text(text):
    return CONTROLS.sub('', ESCAPES.sub('', text)).replace('\r\n', '\n').replace('\r', '\n')


def command_record(item, turn, stamp, *, native=False):
    key = item.get('id')
    command = item.get('command')
    if native and isinstance(command, list) and all(isinstance(arg, str) for arg in command):
        command = subprocess.list2cmdline(command)
    if not isinstance(key, str) or not key or not isinstance(command, str):
        return None
    output = item.get('aggregated_output' if native else 'aggregatedOutput')
    exit_code = item.get('exit_code' if native else 'exitCode')
    duration = item.get('durationMs') if not native else None
    if native and isinstance(item.get('duration'), dict):
        seconds, nanos = item['duration'].get('secs'), item['duration'].get('nanos')
        if type(seconds) is int and type(nanos) is int:
            duration = seconds * 1000 + nanos // 1000000
    status = item.get('status')
    status = status if status in ('completed', 'failed', 'declined') else 'inProgress'
    output = output if isinstance(output, str) else ''
    return dict(role='process', command=command[:COMMAND_LIMIT], text=output[-OUTPUT_LIMIT:],
                truncated=len(output) > OUTPUT_LIMIT, exitCode=exit_code if type(exit_code) is int else None,
                durationMs=duration if type(duration) is int and duration >= 0 else None,
                status=status, id=key, turnId=turn, time=stamp)


class CodexActivity:
    def __init__(self):
        self.lock = threading.Lock()
        self.threads = OrderedDict()
        self.serial = 0

    def _changed(self, thread, items):
        self.serial += 1
        self.threads[thread] = (self.serial, items)
        self.threads.move_to_end(thread)
        if len(self.threads) > THREAD_LIMIT:
            self.threads.popitem(last=False)

    def revision(self, thread):
        with self.lock:
            return self.threads[thread][0] if thread in self.threads else 0

    def snapshot(self, thread):
        with self.lock:
            return [item.copy() for item in self.threads[thread][1].values()] if thread in self.threads else []

    def record(self, method, params, thread, turn):
        if not isinstance(thread, str) or not isinstance(turn, str) or not turn:
            return
        with self.lock:
            items = self.threads.get(thread, (0, {}))[1]
            if method == 'turn/completed':
                if self._finish(items, turn):
                    self._changed(thread, items)
                return

            stamp = time.time()
            item = params.get('item') or {}
            delta = params.get('delta')
            item_id = params.get('itemId') if method in DELTAS else item.get('id')
            if not isinstance(item_id, str) or not item_id:
                return
            key = turn, item_id
            previous = items.get(key)
            if previous and previous['status'] != 'inProgress' and not (
                    previous['status'] == 'unknown' and method == 'item/completed'):
                return
            if method in DELTAS and isinstance(delta, str) and delta:
                role = DELTAS[method]
                if previous is None:
                    previous = dict(role=role, status='inProgress', text='', truncated=False,
                                    command='', exitCode=None, durationMs=None, id=item_id, turnId=turn, time=stamp)
                limit = OUTPUT_LIMIT if role == 'process' else TEXT_LIMIT
                text = previous['text'] + delta
                entry = {**previous, 'text': text[-limit:] if role == 'process' else text[:limit],
                         'truncated': previous['truncated'] or len(text) > limit}
            elif item.get('type') == 'commandExecution':
                entry = command_record(item, turn, previous['time'] if previous else stamp)
                if entry is None:
                    return
                if previous and (method == 'item/started' or not isinstance(item.get('aggregatedOutput'), str)):
                    entry.update(text=previous['text'], truncated=previous['truncated'])
                if method == 'item/completed' and entry['status'] == 'inProgress':
                    entry['status'] = 'unknown'
            elif method == 'item/completed' and item.get('type') == 'agentMessage' and previous:
                body = item.get('text')
                if not isinstance(body, str):
                    return
                entry = {**previous, 'status': 'completed', 'text': body[:TEXT_LIMIT],
                         'truncated': len(body) > TEXT_LIMIT}
            else:
                return
            items[key] = entry
            if previous is None and len(items) > MESSAGE_LIMIT:
                same_role = [item_key for item_key, value in items.items() if value['role'] == entry['role']]
                if len(same_role) > MESSAGE_LIMIT:
                    del items[same_role[0]]
            self._changed(thread, items)

    @staticmethod
    def _finish(items, turn=None):
        changed = False
        for entry in items.values():
            if entry['status'] == 'inProgress' and (turn is None or entry['turnId'] == turn):
                entry['status'] = 'unknown'
                changed = True
        return changed

    def disconnect(self):
        with self.lock:
            for thread, (_, items) in list(self.threads.items()):
                if self._finish(items):
                    self._changed(thread, items)
