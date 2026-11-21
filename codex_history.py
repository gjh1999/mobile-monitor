"""Read the visible parts of a local Codex rollout without touching its writer."""
import json
import os
import sqlite3
import stat
import threading
import time
import uuid
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from conversation import MESSAGE_LIMIT, TEXT_LIMIT, read_conversation
from codex_activity import command_record


SCAN_INTERVAL = 3
TAIL_LIMIT = 16 * 1024**2
LINE_LIMIT = 1024**2
CACHE_LIMIT = 5
ITEM_ROLES = {'UserMessage': 'user', 'AgentMessage': 'assistant'}
LEGACY_ROLES = {'user_message': 'user', 'agent_message': 'assistant'}


def valid_thread_id(value):
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def decode(line):
    try:
        data = json.loads(line)
        return data if isinstance(data, dict) else {}
    except (ValueError, RecursionError):
        return {}


def event_time(event, fallback):
    try:
        return datetime.fromisoformat(event['timestamp']).timestamp()
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        return fallback


@dataclass
class Rollout:
    path: Path
    stamp: tuple
    offset: int = 0
    discard_line: bool = False
    turn: str | None = None
    tail: bytes = b''
    completed: deque = field(default_factory=lambda: deque(maxlen=MESSAGE_LIMIT * 2))
    commands: deque = field(default_factory=lambda: deque(maxlen=MESSAGE_LIMIT))
    legacy: deque = field(default_factory=lambda: deque(maxlen=MESSAGE_LIMIT * 2))
    removed: deque = field(default_factory=lambda: deque(maxlen=MESSAGE_LIMIT * 2))

    def apply(self, event, tid, fallback):
        payload = event.get('payload')
        if not isinstance(payload, dict) or payload.get('thread_id', tid) != tid:
            return
        kind = payload.get('type')
        category = event.get('type')
        turn = payload.get('turn_id')
        if category == 'turn_context' or (category == 'event_msg' and kind == 'task_started'):
            if isinstance(turn, str) and turn:
                self.turn = turn
            return
        if category != 'event_msg' or not isinstance(kind, str):
            return
        if kind == 'thread_rolled_back':
            self.rollback(payload.get('num_turns'))
            return
        turn = turn if isinstance(turn, str) and turn else self.turn
        if kind in LEGACY_ROLES:
            self.add(self.legacy, LEGACY_ROLES[kind], payload.get('message'), event_time(event, fallback), turn)
        elif kind == 'item_completed':
            item = payload.get('item')
            if not isinstance(item, dict) or not isinstance(item.get('type'), str):
                return
            if item['type'] == 'CommandExecution':
                command = command_record(item, turn, event_time(event, fallback), native=True)
                if command is not None:
                    self.commands.append({**command, 'position': self.offset})
                return
            if item['type'] not in ITEM_ROLES:
                return
            pieces = item.get('content')
            if isinstance(pieces, list) and isinstance(item.get('id'), str):
                text = '\n'.join(piece['text'] for piece in pieces if isinstance(piece, dict)
                                 and piece.get('type') in ('text', 'Text') and isinstance(piece.get('text'), str))
                self.add(self.completed, ITEM_ROLES[item['type']], text, event_time(event, fallback), turn, item['id'])

    def add(self, target, role, text, stamp, turn, item=None):
        if isinstance(text, str) and text.strip():
            target.append(dict(role=role, text=text[:TEXT_LIMIT], time=stamp, truncated=len(text) > TEXT_LIMIT,
                               turnId=turn, id=item, position=self.offset))

    def rollback(self, count):
        if type(count) is not int or count <= 0:
            return
        turns = dict.fromkeys(message['turnId'] for message in sorted((*self.completed, *self.legacy),
                                                                    key=lambda message: message['position'])
                              if message['turnId'] and message['role'] == 'user')
        removed = set(list(turns)[-count:])
        self.removed.extend(removed)
        for messages in (self.completed, self.legacy, self.commands):
            kept = [message for message in messages if message['turnId'] not in removed]
            messages.clear()
            messages.extend(kept)
        self.turn = None

    def matches_tail(self, stream):
        stream.seek(self.offset - len(self.tail))
        return stream.read(len(self.tail)) == self.tail

    def read(self, stream, size, tid, fallback):
        stream.seek(self.offset)
        while stream.tell() < size:
            start = stream.tell()
            line = stream.readline(min(LINE_LIMIT + 1, size - start))
            if not line:
                break
            if not line.endswith(b'\n'):
                if not self.discard_line and len(line) <= LINE_LIMIT:
                    break
                self.discard_line = True
            elif self.discard_line:
                self.discard_line = False
            elif len(line) <= LINE_LIMIT and (b'"event_msg"' in line or b'"turn_context"' in line):
                self.apply(decode(line), tid, fallback)
            self.offset = stream.tell()
        stream.seek(max(0, self.offset - 64))
        self.tail = stream.read(min(64, self.offset))


class CodexHistory:
    def __init__(self, home):
        self.home = home.resolve()
        self.lock = threading.Lock()
        self.paths = {}
        self.next_scan = 0
        self.cache = OrderedDict()

    def locate(self, tid):
        path = self.paths.get(tid)
        if path is not None and path.is_file():
            return path
        now = time.monotonic()
        if now < self.next_scan:
            return None
        paths = {}
        for folder in ('archived_sessions', 'sessions'):
            root = self.home / folder
            if not root.resolve().is_relative_to(self.home):
                continue
            scoped = {}
            for candidate in root.rglob('rollout-*.jsonl'):
                sid = candidate.stem[-36:]
                if valid_thread_id(sid):
                    scoped[sid] = candidate if sid not in scoped else None
            paths.update(scoped)
        self.paths = paths
        self.next_scan = now + SCAN_INTERVAL
        return paths.get(tid)

    def read(self, tid, *, include_commands=False):
        if not valid_thread_id(tid):
            return [], [], ()
        with self.lock:
            try:
                path = self.locate(tid)
                if path is None or path.is_symlink() or not path.resolve().is_relative_to(self.home):
                    return [], [], ()
                status = path.stat()
                if not stat.S_ISREG(status.st_mode):
                    return [], [], ()
                stamp = (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)
                current = self.cache.get(tid)
                if current is None or current.path != path or current.stamp != stamp:
                    with path.open('rb') as stream:
                        current = self.refresh(tid, path, stream, current)
                    if current is None:
                        self.cache.pop(tid, None)
                        return [], [], ()
                    self.cache[tid] = current
                self.cache.move_to_end(tid)
                if len(self.cache) > CACHE_LIMIT:
                    self.cache.popitem(last=False)
                completed = list(current.completed)
                if include_commands:
                    completed.extend(current.commands)
                return completed, list(current.legacy), tuple(current.removed)
            except (OSError, ValueError):
                return [], [], ()

    @staticmethod
    def refresh(tid, path, stream, current):
        status = os.fstat(stream.fileno())
        stamp = (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)
        size = status.st_size
        append = (current is not None and current.path == path and current.stamp[:2] == stamp[:2]
                  and size > current.stamp[2] and size - current.offset <= TAIL_LIMIT and current.matches_tail(stream))
        if not append:
            stream.seek(0)
            line = stream.readline(LINE_LIMIT + 1)
            meta = decode(line) if line.endswith(b'\n') and len(line) <= LINE_LIMIT else {}
            payload = meta.get('payload')
            if meta.get('type') != 'session_meta' or not isinstance(payload, dict) or payload.get('id') != tid:
                return None
            begin = max(stream.tell(), size - TAIL_LIMIT)
            current = Rollout(path, stamp, offset=begin)
            stream.seek(begin - 1)
            current.discard_line = stream.read(1) != b'\n'
        current.read(stream, size, tid, status.st_mtime)
        current.stamp = stamp
        return current


@lru_cache(maxsize=2)
def history_for(home):
    return CodexHistory(home)


def merge_messages(completed, legacy, recorded, removed, activity=()):
    exact = OrderedDict(((message['turnId'], message['id']), message) for message in completed)
    modern = {(turn, message['role']) for (turn, _), message in exact.items()
              if turn and message['role'] != 'user'}
    modern_users = Counter((turn, message['text']) for (turn, _), message in exact.items()
                           if turn and message['role'] == 'user')
    unique_legacy = []
    for message in legacy:
        turn, role = message['turnId'], message['role']
        if role == 'user':
            key = (turn, message['text'])
            # A modern message replaces only its matching legacy copy. A turn
            # can contain multiple user inputs while the agent is running.
            if modern_users[key]:
                modern_users[key] -= 1
                continue
        elif (turn, role) in modern:
            continue
        unique_legacy.append(message)
    legacy = unique_legacy
    legacy_keys = Counter((m['turnId'], m['role'], m['text']) for m in legacy if m['turnId'])
    extra = []
    absorbed = set()
    removed = set(removed)
    for message in recorded:
        turn, item = message.get('turnId'), message.get('id')
        if turn in removed or (turn, item) in exact:
            continue
        key = (turn, message['role'], message['text'])
        if legacy_keys[key]:
            legacy_keys[key] -= 1
            absorbed.add((turn, item))
        else:
            extra.append(message)
    keyed = {**{(m['turnId'], m['id']): m for m in extra}, **exact}
    for item in activity:
        turn, item_id = item['turnId'], item['id']
        if turn in removed or (turn, item_id) in absorbed:
            continue
        key = turn, item_id
        durable = keyed.get(key)
        if durable is None:
            keyed[key] = item
        elif durable['role'] == item['role']:
            if durable['role'] == 'process' and item['status'] in ('completed', 'failed', 'declined'):
                durable = {**durable, **item}
            keyed[key] = {**durable, 'time': min(durable['time'], item['time'])}
    messages = sorted((*keyed.values(), *legacy), key=lambda m: (m['time'], m.get('position', 0)))
    remaining = {False: MESSAGE_LIMIT, True: MESSAGE_LIMIT}
    visible = []
    for message in reversed(messages):
        process = message['role'] == 'process'
        if remaining[process]:
            visible.append(message)
            remaining[process] -= 1
    result = []
    for message in reversed(visible):
        fields = ('role', 'text', 'time', 'truncated')
        if message['role'] == 'process':
            fields += ('command', 'status', 'exitCode', 'durationMs')
        result.append({key: message[key] for key in fields})
    return result


def read_codex_conversation(path, tid, *, activity=None):
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    completed, legacy, removed = history_for(home).read(tid, include_commands=activity is not None)
    try:
        journal = read_conversation(path, tid, include_ids=True, include_turns=True)
    except sqlite3.Error:
        if not completed and not legacy and not activity:
            raise
        journal = {'messages': [], 'events': []}
    return {'messages': merge_messages(completed, legacy, journal['messages'], removed, activity or ()),
            'events': journal['events']}
