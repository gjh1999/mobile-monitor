"""Claude Code stream-json adapter for the monitor's existing task controls.

The CLI owns authentication, model choice, tools and session persistence. No model
request is made when discovering/attaching a session. External open sessions stay
read-only; only subprocesses launched here can be interrupted here.
"""
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from claude_sessions import ClaudeSessions, valid_session
from rpc_errors import RPCError


def find_claude():
    explicit = os.environ.get('CLAUDE_CODE_PATH')
    exe = explicit or shutil.which('claude.exe') or shutil.which('claude')
    if not exe:
        native = Path.home() / '.local/bin/claude.exe'
        if native.exists():
            exe = str(native)
    if not exe:
        raise FileNotFoundError('未找到 Claude Code，请先安装 CLI 并登录，或设置 CLAUDE_CODE_PATH。')
    if str(exe).lower().endswith(('.cmd', '.bat')):
        cli = Path(exe).parent / 'node_modules/@anthropic-ai/claude-code/cli.js'
        node = shutil.which('node.exe') or shutil.which('node')
        if cli.exists() and node:
            return [node, str(cli)]
        raise FileNotFoundError('请使用 Claude Code 原生安装，或配置 CLAUDE_CODE_PATH 指向可执行文件。')
    return [str(exe)]


def _result_messages(event):
    errors = event.get('errors') or []
    if not isinstance(errors, (list, tuple)):
        errors = [errors]
    return list(dict.fromkeys(str(value).strip() for value in [event.get('result'), *errors] if value))


def _is_interruption_message(text):
    # This CLI diagnostic accompanies an interrupted user input. It is NOT a
    # general success signal: callers must also match a local interrupt intent.
    if re.fullmatch(r'\[ede_diagnostic\]\s+result_type=user\s+last_content_type=n/a\s+stop_reason=(?:null|tool_use)', text):
        return True
    return text.lower().strip(' .![]') in (
        'interrupted', 'interrupted by user', 'request interrupted',
        'request interrupted by user', 'request was interrupted by user',
    )


class _Session:
    def __init__(self, owner, tid, cwd, resume):
        self.owner, self.tid, self.cwd = owner, tid, cwd
        self.lock = threading.RLock()
        self.command_lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.stopping_turn = None
        self.responses = {}
        self.pending_permissions = set()
        self.active_turn = None
        self.inputs = {}  # In-flight CLI UUID -> local operation/turn ID
        self.message_turns = {}  # Retain associations through lifecycle/result races.
        self.reply_text = {}
        self.consumed = set()
        self.intentional_interrupts = set()
        self.interrupting = False
        self.last_error = None
        self.last_reply = ''
        self.updated = time.time()
        self.stopping = False
        self.stderr = deque(maxlen=8)
        env = os.environ.copy()
        # A monitor opened from a Claude terminal must still start its own CLI.
        env.pop('CLAUDECODE', None)
        command = owner.command + ['-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
                                  '--verbose', '--replay-user-messages', '--permission-prompt-tool', 'stdio']
        command += ['--resume' if resume else '--session-id', tid]
        self.process = owner.popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                   bufsize=1, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.reader = threading.Thread(target=self._read, daemon=True, name='claude-' + tid[:8])
        self.error_reader = threading.Thread(target=self._stderr, daemon=True)
        self.reader.start()
        self.error_reader.start()
        try:
            self.control('initialize', timeout=25)
        except Exception:
            self.close()
            raise

    def send(self, data):
        with self.write_lock:
            if self.process.poll() is not None:
                raise ConnectionError('Claude Code 进程已经退出。')
            self.process.stdin.write(json.dumps(data, ensure_ascii=False) + '\n')
            self.process.stdin.flush()

    def control(self, subtype, timeout=20, **fields):
        request = str(uuid.uuid4())
        response = queue.Queue()
        with self.lock:
            self.responses[request] = response
        try:
            self.send({'type': 'control_request', 'request_id': request, 'request': {'subtype': subtype, **fields}})
            result = response.get(timeout=timeout)
            if result.get('subtype') != 'success':
                raise RPCError(detail=str(result.get('error') or 'Claude Code 控制连接不可用。')[:500])
            return result.get('response') or {}
        finally:
            with self.lock:
                self.responses.pop(request, None)

    def prompt(self, text, turn_id, interject=False, guard=None):
        # Serialise only command admission/writes, never the stdout reader. A Stop
        # racing with a send must either follow that send or reject it as stopping.
        with self.command_lock:
            if guard is not None and not guard():
                raise RPCError(detail='任务已暂停或移出，没有发送这条指令。')
            self._prompt(text, turn_id, interject)

    def _prompt(self, text, turn_id, interject):
        self.owner.ensure_available(self.tid)
        with self.lock:
            active = self.active_turn
            if active and not interject:
                raise RPCError(detail='任务状态已变化，请重新发送；未启动重复轮次。')
            if self.interrupting:
                raise RPCError(detail='正在等待暂停完成，请稍后继续。')
            message_id = str(uuid.uuid4())
            self.inputs[message_id] = turn_id
            self.message_turns[message_id] = turn_id
            self.reply_text[turn_id] = ''
            if not active:
                self.active_turn = turn_id
                self.last_reply = ''
            else:
                self.intentional_interrupts.add(active)
            self.last_error = None
            self.updated = time.time()
        if not active:
            self.owner.record(self.tid, turn_id, 'turn/started', turn={'id': turn_id, 'status': 'inProgress'})
        # Priority now is the CLI's native interjection path. It lets the CLI move
        # work aside / interrupt at the proper point; never opens a second session.
        self.send({'type': 'user', 'session_id': self.tid, 'uuid': message_id,
                   'message': {'role': 'user', 'content': text}, 'parent_tool_use_id': None,
                   'origin': {'kind': 'human'},
                   **({'priority': 'now', 'requeue_if_interrupted_within_ms': 5000} if active or interject else {})})
        self.owner.record(self.tid, turn_id, 'item/completed', item={
            'id': message_id, 'type': 'userMessage', 'content': [{'type': 'text', 'text': text}]})

    def interrupt(self, timeout=20):
        with self.command_lock:
            return self._interrupt(timeout)

    def _interrupt(self, timeout):
        self.owner.ensure_available(self.tid)
        with self.lock:
            if self.interrupting:
                return {'stopping': True}
            self.interrupting = True
            self.stopping_turn = self.active_turn
            self.intentional_interrupts.add(self.active_turn)
            self.intentional_interrupts.update(self.inputs.values())
        # A Stop is not a prompt. Cancel pending input too, but leave independent
        # background jobs to their own controls. The result confirms completion.
        try:
            result = self.control('interrupt', timeout=timeout, cancel_queued=True, spare_background_tasks=True)
        except Exception:
            with self.lock:
                self.interrupting = False  # Allow an explicit person-driven retry.
                self.stopping_turn = None
            raise
        with self.lock:
            for message_id in result.get('cancelled', []):
                self.inputs.pop(message_id, None)
                self.consumed.discard(message_id)
            self._select_active()
            if result.get('still_queued'):
                self.interrupting = False
                raise RPCError(detail='暂停已请求，但仍有等待中的消息尚未取消，请再次暂停。')
        return result


    def _stderr(self):
        for line in self.process.stderr:
            self.stderr.append(line.strip()[:500])

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                self.dispatch(event)
        finally:
            with self.lock:
                active = self.active_turn
                self.active_turn = None
                self.last_error = self.last_error or (None if self.stopping else 'Claude Code 进程已退出，请重新接入。')
                for response in self.responses.values():
                    response.put({'subtype': 'error', 'error': self.last_error or '连接已关闭。'})
            if active and not self.stopping:
                self.owner.record(self.tid, active, 'error', error={'message': self.last_error})
            with self.lock:
                self.inputs.clear()
                self.consumed.clear()
                self.active_turn = None
                self.stopping_turn = None
                self.interrupting = False
                self.intentional_interrupts.clear()
            self.owner.resolve_permissions(self)

    def _select_active(self):
        # A stop receipt may cancel queued input before the running turn emits its
        # final result. Keep that turn visible as stopping until the result arrives.
        self.active_turn = self.stopping_turn or next(
            (turn for message_id, turn in self.inputs.items() if message_id in self.consumed),
            next(iter(self.inputs.values()), None))
        if self.active_turn is None:
            self.interrupting = False
            self.stopping_turn = None

    def dispatch(self, event):
        kind = event.get('type')
        if event.get('parent_tool_use_id') is not None:
            return  # A subagent result is not completion of the controlled turn.
        if kind == 'command_lifecycle':
            message_id = event.get('command_uuid')
            with self.lock:
                if event.get('state') == 'started' and message_id in self.inputs:
                    self.consumed.add(message_id)
                    self.active_turn = self.inputs[message_id]
                    self.last_reply = ''
                elif event.get('state') in ('completed', 'cancelled', 'discarded', 'refused'):
                    self.inputs.pop(message_id, None)
                    self.consumed.discard(message_id)
                    self._select_active()
            return
        if kind == 'control_response':
            response = event.get('response', {})
            with self.lock:
                pending = self.responses.get(response.get('request_id'))
            if pending:
                pending.put(response)
            return
        if kind == 'control_request':
            request = event.get('request') or {}
            rid = event.get('request_id')
            if request.get('subtype') == 'can_use_tool':
                self.owner.permission(self, rid, request)
            else:
                self.send({'type': 'control_response', 'response': {'subtype': 'error', 'request_id': rid,
                                                                   'error': 'Unsupported control request'}})
            return
        if kind == 'control_cancel_request':
            self.owner.resolve_permission(self, event.get('request_id'))
            return
        self.updated = time.time()
        message_ids = event.get('user_message_uuids') or ([event['user_message_uuid']] if event.get('user_message_uuid') else [])
        with self.lock:
            addressed = [message_id for message_id in message_ids if message_id in self.message_turns]
            if kind == 'assistant':
                self.consumed.update(message_id for message_id in addressed if message_id in self.inputs)
            turn = next((self.message_turns[message_id] for message_id in reversed(addressed)), self.active_turn or 'session')
        if kind == 'assistant':
            content = (event.get('message') or {}).get('content') or []
            text = '\n'.join(b.get('text', '') for b in content if b.get('type') == 'text')
            if text:
                self.last_reply += text
                self.reply_text[turn] = self.reply_text.get(turn, '') + text
                self.owner.record(self.tid, turn, 'item/completed', item={
                    'id': event.get('uuid') or str(uuid.uuid4()), 'type': 'agentMessage', 'text': text})
            if event.get('error'):
                self.last_error = text or str(event['error'])[:1000]
                self.owner.record(self.tid, turn, 'error', error={'message': self.last_error})
        elif kind == 'result':
            messages = _result_messages(event)
            interrupted = turn in self.intentional_interrupts and (
                not event.get('is_error') or bool(messages) and all(_is_interruption_message(message) for message in messages))
            reply_text = self.reply_text.get(turn, '')
            if event.get('is_error') and not interrupted:
                self.last_error = ('; '.join(messages) or 'Claude Code 执行失败。')[:1500]
                self.owner.record(self.tid, turn, 'error', error={'message': self.last_error})
            elif not event.get('is_error') and not reply_text and event.get('result'):
                self.owner.record(self.tid, turn, 'item/completed', item={
                    'id': turn + '-result', 'type': 'agentMessage', 'text': str(event['result'])})
            self.owner.record(self.tid, turn, 'turn/completed', turn={
                'id': turn, 'status': 'interrupted' if interrupted else ('failed' if self.last_error else 'completed')})
            with self.lock:
                completed = addressed or ([next(iter(self.inputs))] if not message_ids and self.inputs else [])
                for message_id in completed:
                    self.inputs.pop(message_id, None)
                    self.consumed.discard(message_id)
                stopping_result = self.stopping_turn is not None and (not message_ids or self.stopping_turn in (self.message_turns.get(mid) for mid in message_ids))
                if stopping_result:
                    self.inputs.clear()
                    self.consumed.clear()
                    self.stopping_turn = None
                self.intentional_interrupts.discard(turn)
                self._select_active()
                if self.active_turn is not None:
                    self.last_reply = self.reply_text.get(self.active_turn, '')
                    self.last_error = None
                else:
                    self.last_reply = ''
            self.owner.resolve_permissions(self)

    def close(self):
        self.stopping = True
        if self.process.poll() is None:
            try:
                if self.active_turn:
                    self.control('interrupt', timeout=3)
            except Exception:
                pass
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        for reader in (self.reader, self.error_reader):
            if reader is not threading.current_thread():
                reader.join(timeout=5)
        self.owner.resolve_permissions(self)


class ClaudeCodeRPC:
    client_type = 'claude'

    def control_allowed(self):
        return True

    def __init__(self, approvals=None, journal=None, sessions=None, command=None, popen=subprocess.Popen, drafts_path=None):
        self.approvals = approvals
        self.journal = journal
        self.sessions = sessions or ClaudeSessions()
        self.command = command or find_claude()
        self.popen = popen
        self.owned_threads = set()
        self.closed = False
        self.lock = threading.RLock()
        self.running = {}
        self.drafts = {}
        self.drafts_path = Path(drafts_path) if drafts_path else None
        if self.drafts_path and self.drafts_path.exists():
            data = json.loads(self.drafts_path.read_text(encoding='utf-8'))
            self.drafts = {k: v for k, v in data.items() if valid_session(k) and isinstance(v, dict)}
        self.permissions = {}

    def save_drafts(self):
        if self.drafts_path:
            temporary = self.drafts_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.drafts, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.drafts_path)

    def record(self, tid, turn_id, method, **values):
        if self.journal:
            self.journal.record({'method': method, 'params': {'threadId': tid, 'turnId': turn_id, **values}}, self.owned_threads)

    def permission(self, session, rid, request):
        if not self.approvals:
            session.send({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': rid,
                         'response': {'behavior': 'deny', 'message': '监控未配置人工审批。'}}})
            return
        key = session.tid + ':' + str(rid)
        details = {'threadId': session.tid, 'client': 'claude', 'cwd': session.cwd,
                   'reason': 'Claude Code 请求使用工具：' + str(request.get('tool_name')),
                   'permissions': {'tool': request.get('tool_name'), 'input': request.get('input', {})}}
        if request.get('tool_name') == 'Bash':
            details['command'] = (request.get('input') or {}).get('command')
        with self.lock:
            self.permissions[key] = (session, rid, request.get('input', {}))
            session.pending_permissions.add(key)
        self.approvals.add(self, {'id': key, 'method': 'item/permissions/requestApproval', 'params': details})

    def send(self, reply):
        with self.lock:
            value = self.permissions.pop(reply.get('id'), None)
        if not value:
            raise ConnectionError('审批已失效。')
        session, rid, original = value
        session.pending_permissions.discard(reply['id'])
        allowed = bool((reply.get('result') or {}).get('permissions'))
        response = {'behavior': 'allow', 'updatedInput': original} if allowed else {
            'behavior': 'deny', 'message': '用户拒绝了本次操作。'}
        session.send({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': rid, 'response': response}})

    def resolve_permission(self, session, rid):
        key = session.tid + ':' + str(rid)
        with self.lock:
            self.permissions.pop(key, None)
            session.pending_permissions.discard(key)
        if self.approvals:
            self.approvals.resolve(self, key)

    def resolve_permissions(self, session):
        for key in list(session.pending_permissions):
            self.resolve_permission(session, key.split(':', 1)[1])

    def external_live(self):
        with self.lock:
            pids = {s.process.pid for s in self.running.values() if s.process.poll() is None}
        return self.sessions.live(exclude_pids=pids)

    def ensure_available(self, tid):
        if tid in self.external_live():
            raise RPCError(detail='already has an active writer: 原 Claude Code 窗口仍然打开，未发送操作。')

    def control_available(self, tid):
        return tid not in self.external_live()

    def _thread(self, tid):
        with self.lock:
            session = self.running.get(tid)
            draft = self.drafts.get(tid)
        history = self.sessions.thread(tid)
        thread = history or (draft.copy() if draft else None)
        if thread and draft:
            thread['title'] = draft['title']
        if thread is None:
            raise RPCError(detail='找不到 Claude Code 会话历史。')
        if session and tid not in self.external_live():
            flags = ['waitingOnApproval'] if session.pending_permissions else []
            thread['status'] = {'type': 'active' if session.active_turn else ('systemError' if session.last_error else 'idle'), 'activeFlags': flags}
            thread['updatedAt'] = session.updated
        return thread

    def call(self, method, params, timeout=20):
        if self.closed:
            raise ConnectionError('Claude Code 适配器已经关闭。')
        if method in ('thread/start', 'thread/resume', 'thread/name/set', 'turn/start', 'turn/steer', 'turn/interrupt'):
            if not self.control_allowed():
                raise RPCError(detail='任务控制暂停，正在交还本机写入权。')
        tid = params.get('threadId')
        if method == 'thread/list':
            threads = {t['id']: t for t in self.sessions.threads(limit=params.get('limit', 50))}
            with self.lock:
                for key in self.drafts.keys() | self.running.keys():
                    threads[key] = self._thread(key)
            return {'data': sorted(threads.values(), key=lambda t: t.get('updatedAt', 0), reverse=True)[:params.get('limit', 50)]}
        if method == 'thread/start':
            tid = str(uuid.uuid4())
            thread = {'id': tid, 'title': 'Claude Code 测试任务', 'cwd': params.get('cwd') or str(Path.cwd()),
                      'status': {'type': 'idle'}, 'updatedAt': time.time(), 'client': 'claude'}
            with self.lock:
                self.drafts[tid] = thread
                self.save_drafts()
            return {'thread': thread.copy()}
        if not valid_session(tid):
            raise RPCError(detail='无效的 Claude Code 会话标识。')
        if method == 'thread/read':
            return {'thread': self._thread(tid)}
        if method == 'thread/resume':
            thread = self._thread(tid)
            with self.lock:
                previous = self.running.get(tid)
                if previous and previous.process.poll() is None and tid not in self.external_live():
                    return {'thread': thread}
            if tid in self.external_live():
                raise RPCError(detail='already has an active writer: Claude Code 原窗口仍然打开')
            cwd = thread.get('cwd')
            if not cwd or not Path(cwd).is_dir():
                raise RPCError(detail='Claude Code 会话工作目录不存在，无法恢复。')
            # Test drafts are created without a CLI/model call; start when messaged.
            if tid not in self.drafts:
                self.owned_threads.add(tid)
                session = _Session(self, tid, cwd, resume=True)
                with self.lock:
                    self.running[tid] = session
            return {'thread': thread}
        if method == 'thread/name/set':
            # Existing session display names live in TaskControl's local aliases.
            # A rename must never send a model prompt or edit CLI transcripts.
            if tid in self.drafts:
                self.drafts[tid]['title'] = params.get('name') or 'Claude Code 测试任务'
                self.save_drafts()
            return {}
        if method == 'thread/goal/get':
            # Claude Code exposes no Codex goal endpoint. A missing goal deliberately
            # prevents the Codex scheduler from inventing automatic continuation.
            return {'goal': None}
        if method == 'thread/turns/list':
            session = self.running.get(tid)
            return {'data': [{'id': session.active_turn, 'status': 'inProgress'}] if session and session.active_turn else []}
        if method in ('turn/start', 'turn/steer'):
            if tid not in self.owned_threads:
                raise RPCError(detail='请先接入 Claude Code 任务。')
            self.ensure_available(tid)
            session = self.running.get(tid)
            if not session or session.process.poll() is not None:
                if tid in self.external_live():
                    raise RPCError(detail='原 Claude Code 窗口仍然打开，未发送消息。')
                thread = self._thread(tid)
                resume = self.sessions.thread(tid) is not None
                session = _Session(self, tid, thread['cwd'], resume=resume)
                with self.lock:
                    self.running[tid] = session
            text = '\n'.join(b.get('text', '') for b in params.get('input', []) if b.get('type') == 'text')
            if not text.strip():
                raise RPCError(detail='请输入要发送的指令。')
            turn = params.get('clientUserMessageId') or str(uuid.uuid4())
            previous = params.get('_sendGuard')
            session.prompt(text, turn, interject=method == 'turn/steer' or bool(session.active_turn),
                           guard=lambda: self.control_allowed() and (previous is None or previous()))
            return {'turn': {'id': turn, 'status': 'inProgress'}}
        if method == 'turn/interrupt':
            if tid not in self.owned_threads:
                raise RPCError(detail='请先接入 Claude Code 任务。')
            self.ensure_available(tid)
            session = self.running.get(tid)
            if not session or session.process.poll() is not None:
                return {}  # No owned CLI process is executing.
            # Stop belongs to the selected session, not to a stale UI turn ID.
            # A new input may have become active since the last status snapshot.
            return session.interrupt(timeout=timeout)
        raise RPCError(detail='Claude Code 不支持此操作：' + method)

    def conversation(self, tid):
        from conversation import read_conversation
        data = self.sessions.conversation(tid)
        if not self.journal:
            return data
        with self.journal.lock:
            path = self.journal.db.execute('PRAGMA database_list').fetchone()[2]
        if not path:
            return data
        journal = read_conversation(Path(path), tid, include_ids=True)
        messages = list(data['messages'])
        seen = {m['id'] for m in messages if m.get('id')}
        for message in journal['messages']:
            if message.get('id') in seen:
                continue
            # Older transcripts may omit UUIDs; do not duplicate the same nearby
            # message just because one copy came from stream-json.
            duplicate = any(m['role'] == message['role'] and m['text'] == message['text']
                            and abs(m['time'] - message['time']) <= 5
                            and (not m.get('id') or not message.get('id')) for m in messages)
            if not duplicate:
                messages.append(message)
                if message.get('id'):
                    seen.add(message['id'])
        events = []
        event_keys = set()
        for event in sorted(data['events'] + journal['events'], key=lambda e: e.get('time', 0), reverse=True):
            key = (event.get('method'), json.dumps(event.get('status'), sort_keys=True), event.get('time'))
            if key not in event_keys:
                events.append(event)
                event_keys.add(key)
        return {'messages': sorted(messages, key=lambda m: m['time'])[-50:], 'events': events[:12]}

    def close(self):
        self.closed = True
        for session in list(self.running.values()):
            session.close()
        if self.approvals:
            self.approvals.disconnect(self)

    def drain_for_tray(self):
        if not self.closed:
            if self.approvals and self.approvals.pending_on(self):
                return False
            with self.lock:
                for session in self.running.values():
                    with session.lock:
                        if session.active_turn or session.inputs or session.pending_permissions or session.interrupting:
                            return False
            self.close()
        return all(session.process.poll() is not None for session in self.running.values())
