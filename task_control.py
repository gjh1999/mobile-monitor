"""Persistent task controls; observing a session never grants write access."""
import json
import queue
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from rpc_errors import RPCError, RPCNotSent
from coordination_client import ControlDenied

CODEX_PERMISSIONS = {
    ':read-only': ('只读', '在只读沙箱内工作'),
    ':workspace': ('工作区', '可写入当前工作区'),
    ':danger-full-access': ('完全访问', '以当前 Windows 用户的权限运行'),
}


class TaskControl:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.tick_lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.connection = None
        self.runtime = {}
        self.fast_pauses = {}
        self.db.execute('CREATE TABLE IF NOT EXISTS targets(id TEXT PRIMARY KEY, enabled INTEGER, paused INTEGER)')
        if 'managed' not in [r[1] for r in self.db.execute('PRAGMA table_info(targets)')]:
            self.db.execute('ALTER TABLE targets ADD COLUMN managed INTEGER NOT NULL DEFAULT 1')
        self.db.execute('CREATE TABLE IF NOT EXISTS task_names(id TEXT PRIMARY KEY, name TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS auto_progress(thread TEXT PRIMARY KEY, due REAL NOT NULL, revision TEXT, state TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, thread TEXT, action TEXT, text TEXT, status TEXT, result TEXT, stamp REAL)')
        self.db.execute("UPDATE operations SET status='unknown',result='服务曾中断，请核查操作是否已执行；不会自动重发。' WHERE status='running'")
        self.db.execute("UPDATE operations SET status='cancelled',result='原 Codex 连接已结束，请重新选择设置。' "
                        "WHERE status='queued' AND action='codex_settings'")
        self.db.execute("UPDATE targets SET managed=0 WHERE id IN (SELECT thread FROM auto_progress WHERE state='reserved')")
        self.db.execute("UPDATE auto_progress SET state='unknown' WHERE state='reserved'")
        self.db.commit()

    def _execute(self, sql, params=()):
        with self.lock:
            self.db.execute(sql, params)
            self.db.commit()

    def control_allowed(self):
        return True

    def disable_for_tray(self):
        with self.lock, self.db:
            self.db.execute("UPDATE targets SET enabled=0 WHERE enabled=1")
            self.db.execute("UPDATE operations SET status='cancelled',result='本机正在交还控制权，未执行操作。' "
                            "WHERE status='queued'")
            self.runtime.clear()

    def seed(self, targets, managed=True):
        with self.lock:
            for target in targets:
                if re.fullmatch(r'[0-9a-fA-F-]{36}', str(target)):
                    self.db.execute('INSERT OR IGNORE INTO targets(id,enabled,paused,managed) VALUES(?,1,0,?)', (target, int(managed)))
            self.db.commit()

    def submit(self, thread, action, text='', request_id=None):
        if action == 'create_test' and not thread:
            thread = '00000000-0000-0000-0000-000000000000'
        actions = {'create_test', 'attach', 'detach', 'focus', 'manage', 'unmanage', 'pause', 'resume', 'message', 'rename',
                   'codex_settings', 'handoff'}
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', str(thread)) or action not in actions:
            raise ValueError('无效任务或操作。')
        if not isinstance(text, str) or len(text) > 10000 or (action == 'message' and not text.strip()):
            raise ValueError('请输入消息，最多10000字。')
        if action == 'rename':
            if any(ord(c) < 32 or c in '\u0085  ' for c in text):
                raise ValueError('任务名称须为单行文本。')
            text = ' '.join(text.split())
            if not 1 <= len(text) <= 80:
                raise ValueError('任务名称须为1到80个字符。')
        key = request_id or str(uuid.uuid4())
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key):
            raise ValueError('无效操作编号。')
        with self.lock:
            existing = self.db.execute('SELECT thread,action,text FROM operations WHERE id=?', (key,)).fetchone()
            if existing:
                if existing != (thread, action, text):
                    raise ValueError('操作编号已用于其他请求。')
                return key
            if not self.control_allowed():
                raise ValueError('本机正在交还控制权，请稍候重新执舟。')
            # Save stop intent before a worker round. Never hold this lock over RPC.
            if action == 'pause':
                self.db.execute('UPDATE targets SET paused=1 WHERE id=?', (thread,))
            if action in ('pause', 'detach'):
                self.db.execute("UPDATE operations SET status='cancelled',result='任务已暂停或移出，未发送这条指令。' WHERE thread=? AND action IN ('message','resume') AND status='queued'", (thread,))
            if action == 'detach':
                self.db.execute('UPDATE targets SET enabled=0 WHERE id=?', (thread,))
                self.db.execute("UPDATE operations SET status='cancelled',result='任务已移出，已取消接入。' "
                                "WHERE thread=? AND action IN ('attach','focus') AND status IN ('queued','running')", (thread,))
                self.db.execute("UPDATE operations SET status='cancelled',result='任务已移出，未修改设置。' "
                                "WHERE thread=? AND action='codex_settings' AND status='queued'", (thread,))
            if action == 'focus':
                self.db.execute("UPDATE operations SET status='cancelled',result='已选择更新的任务。' WHERE action='focus' AND status='queued'")
            self.db.execute("INSERT INTO operations VALUES(?,?,?,?,'queued','等待后台处理',?)", (key, thread, action, text, time.time()))
            self.db.commit()
            rpc = self.connection
            if action == 'pause' and rpc is not None and getattr(rpc, 'client_type', 'codex') == 'claude':
                # The CLI interrupt channel must not wait behind history reads or
                # a blocked model call. Only signal an already-owned subprocess.
                if thread in rpc.owned_threads and self.runtime.get(thread, {}).get('canControl'):
                    result = {'done': threading.Event(), 'error': None}
                    self.fast_pauses[key] = result
                    def interrupt_owned():
                        try:
                            rpc.call('turn/interrupt', {'threadId': thread, 'turnId': None})
                        except Exception as error:
                            result['error'] = error
                        finally:
                            result['done'].set()
                    threading.Thread(target=interrupt_owned, daemon=True, name='task-pause').start()
        return key

    def _goal(self, rpc, tid):
        try:
            return rpc.call('thread/goal/get', {'threadId': tid}).get('goal') or {}
        except RPCError:
            return {}

    def _load(self, rpc, tid, operation=None):
        try:
            params = {'threadId': tid, 'excludeTurns': True}
            if operation is not None:
                params['_sendGuard'] = lambda: not self._connection_cancelled(operation)
            response = rpc.call('thread/resume', params)
            thread = response.get('thread', {})
            if thread.get('id') != tid:
                raise ValueError('后台返回任务标识不一致。')
            rpc.owned_threads.add(tid)
            journal = getattr(rpc, 'journal', None)
            if journal and getattr(rpc, 'client_type', 'codex') == 'codex' and (thread.get('status') or {}).get('type') == 'active':
                try:
                    journal.track_turn(tid, self._active_turn(rpc, tid))
                except (RPCError, queue.Empty, TimeoutError):
                    pass  # Subsequent live events can still identify the turn.
            return dict(threadId=tid, mode='control', status='attached', message='已接入控制，可暂停或发送消息。', canControl=True,
                        threadStatus=thread.get('status'), title=thread.get('name') or thread.get('title'), cwd=thread.get('cwd'),
                        goalStatus=self._goal(rpc, tid).get('status'), retryAt=time.time() + 3600,
                        codexSettings=self._codex_settings(response))
        except RPCError as error:
            detail = error.detail or ''
            if 'active writer' in detail:
                rpc.owned_threads.discard(tid)
                try:
                    thread = rpc.call('thread/read', {'threadId': tid, 'includeTurns': False}).get('thread', {})
                except RPCError:
                    if not isinstance(error, ControlDenied):
                        raise
                    thread = {}
                client = 'Claude Code' if getattr(rpc, 'client_type', 'codex') == 'claude' else 'Codex'
                message = (detail.removeprefix('active writer: ') if isinstance(error, ControlDenied)
                           else '原' + client + '窗口仍在占用会话，只读关注中；请先退出原会话再接入控制。')
                return dict(threadId=tid, mode='observe', status='observing', title=thread.get('name') or thread.get('title'), cwd=thread.get('cwd'),
                            message=message, canControl=False,
                            threadStatus=thread.get('status'), goalStatus=self._goal(rpc, tid).get('status'),
                            retryAt=time.time() + (3 if isinstance(error, ControlDenied) else 60))
            return dict(threadId=tid, mode='none', status='failed', message='接入失败：' + detail[:180], canControl=False, retryAt=time.time() + 300)

    @staticmethod
    def _codex_settings(response):
        if not response.get('model') and not response.get('activePermissionProfile'):
            return {}
        return {
            'model': response.get('model'),
            'provider': response.get('modelProvider'),
            'effort': response.get('reasoningEffort'),
            'permissions': (response.get('activePermissionProfile') or {}).get('id'),
        }

    def _owns_codex_settings(self, rpc, tid):
        with self.lock:
            row = self.db.execute('SELECT enabled FROM targets WHERE id=?', (tid,)).fetchone()
            return bool(rpc is not None and rpc is self.connection and not getattr(rpc, 'closed', False)
                        and getattr(rpc, 'client_type', 'codex') == 'codex'
                        and row and row[0] and tid in rpc.owned_threads
                        and self.runtime.get(tid, {}).get('canControl'))

    def codex_options(self, tid, field):
        if field not in ('model', 'permissions'):
            raise ValueError('无效的 Codex 设置。')
        with self.lock:
            rpc = self.connection
            if not self._owns_codex_settings(rpc, tid):
                raise ValueError('请先执舟接入该 Codex 会话。')
            target = self.runtime[tid]
            settings = dict(target.get('codexSettings') or {})
            cwd = target.get('cwd')
        method = 'model/list' if field == 'model' else 'permissionProfile/list'
        params = {'limit': 100}
        if field == 'model' and settings.get('provider'):
            params['modelProvider'] = settings['provider']
        if field == 'permissions' and cwd:
            params['cwd'] = cwd
        rows, cursors = [], set()
        while True:
            page = rpc.call(method, params, timeout=8)
            rows.extend(page.get('data', []))
            cursor = page.get('nextCursor')
            if not cursor or cursor in cursors:
                break
            params['cursor'] = cursor
            cursors.add(cursor)
        options = []
        for entry in rows:
            if field == 'model' and entry.get('model') and not entry.get('hidden'):
                options.append({'value': entry['model'], 'title': entry.get('displayName') or entry['model'],
                                'detail': entry.get('description'), 'allowed': True,
                                'effort': entry.get('defaultReasoningEffort'),
                                'efforts': [item.get('reasoningEffort') for item in entry.get('supportedReasoningEfforts', [])]})
            elif field == 'permissions' and entry.get('id'):
                title, detail = CODEX_PERMISSIONS.get(entry['id'], (entry['id'], ''))
                options.append({'value': entry['id'], 'title': title, 'detail': entry.get('description') or detail,
                                'allowed': entry.get('allowed') is True})
        return settings, options

    def _update_codex_settings(self, rpc, tid, text):
        try:
            change = json.loads(text)
            field, value = change['field'], change['value']
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError('无效的 Codex 设置。') from error
        settings, options = self.codex_options(tid, field)
        picked = next((item for item in options if item['value'] == value and item['allowed']), None)
        if picked is None:
            raise ValueError('Codex 当前未开放这一选项。')
        changes = {field: value}
        if field == 'model' and picked['effort'] and settings.get('effort') not in picked['efforts']:
            changes['effort'] = picked['effort']
        rpc.call('thread/settings/update', {'threadId': tid, **changes,
                                            '_sendGuard': lambda: self._owns_codex_settings(rpc, tid)})
        with self.lock:
            self.runtime[tid]['codexSettings'] = {**settings, **changes}
        return f'已选「{picked["title"]}」，后续轮次生效。'

    def _active_turn(self, rpc, tid):
        data = rpc.call('thread/turns/list', {'threadId': tid, 'limit': 1, 'sortDirection': 'desc'})
        return next((t.get('id') for t in data.get('data', data.get('turns', []))
                     if t.get('status') in ('inProgress', 'in_progress', 'active')), None)

    def _send_allowed(self, tid, operation, action):
        with self.lock:
            row = self.db.execute('SELECT enabled,paused FROM targets WHERE id=?', (tid,)).fetchone()
            stopped_after = self.db.execute("SELECT 1 FROM operations WHERE thread=? AND action IN ('pause','detach') AND rowid>(SELECT rowid FROM operations WHERE id=?) LIMIT 1", (tid, operation)).fetchone()
            return bool(self.control_allowed() and row and row[0] and not stopped_after
                        and (action == 'resume' or not row[1]))

    def _focus(self, rpc, tid, operation):
        with self.lock:
            if self._connection_cancelled(operation):
                return '任务已移出，已取消接入。'
            current = self.runtime.get(tid)
        owned = tid in rpc.owned_threads
        target = current if owned and current and current.get('canControl') else self._load(rpc, tid, operation)
        if not target.get('canControl'):
            raise ValueError(target.get('message') or '任务暂时无法接入。')
        with self.lock:
            newer = self.db.execute(
                "SELECT 1 FROM operations WHERE action='focus' AND status='queued' "
                "AND rowid>(SELECT rowid FROM operations WHERE id=?) LIMIT 1", (operation,),
            ).fetchone()
            if self._connection_cancelled(operation):
                rpc.owned_threads.discard(tid)
                return '任务已移出，已取消接入。'
            if newer:
                if not owned:
                    rpc.owned_threads.discard(tid)
                return '已选择更新的任务。'
            with self.db:
                others = self.db.execute('SELECT id FROM targets WHERE enabled=1 AND id!=?', (tid,)).fetchall()
                self.db.execute(
                    "UPDATE operations SET status='cancelled',result='已切换任务，未发送这条指令。' "
                    "WHERE status='queued' AND action IN ('message','resume') "
                    "AND thread IN (SELECT id FROM targets WHERE enabled=1 AND id!=?)", (tid,),
                )
                self.db.execute(
                    "UPDATE operations SET status='cancelled',result='已切换任务，未修改设置。' "
                    "WHERE status='queued' AND action='codex_settings' "
                    "AND thread IN (SELECT id FROM targets WHERE enabled=1 AND id!=?)", (tid,),
                )
                self.db.execute('UPDATE targets SET enabled=0 WHERE enabled=1 AND id!=?', (tid,))
                self.db.execute(
                    'INSERT INTO targets(id,enabled,paused,managed) VALUES(?,1,0,0) '
                    'ON CONFLICT(id) DO UPDATE SET enabled=1', (tid,),
                )
            for (previous,) in others:
                self.runtime.pop(previous, None)
                rpc.owned_threads.discard(previous)
            self.runtime[tid] = target
        return '已接入当前任务，其他任务已断开。' if others else '已接入当前任务。'

    def _connection_cancelled(self, operation):
        with self.lock:
            return self.db.execute("SELECT status='cancelled' FROM operations WHERE id=?", (operation,)).fetchone()[0]

    def _perform(self, rpc, tid, action, text, operation):
        is_claude = getattr(rpc, 'client_type', 'codex') == 'claude'
        if action == 'handoff':
            if is_claude:
                raise ValueError('跨设备交还仅用于 Codex 任务。')
            rpc.handoff_personal(tid)
            self._execute('UPDATE targets SET enabled=0 WHERE id=?', (tid,))
            with self.lock:
                self.runtime.pop(tid, None)
            return '最新会话已同步，任务已交还。'
        if action == 'codex_settings':
            return self._update_codex_settings(rpc, tid, text)
        if action == 'rename':
            self._execute('INSERT INTO task_names VALUES(?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name', (tid, text))
            return '任务名称已更新。'
        if action == 'focus':
            return self._focus(rpc, tid, operation)
        if action == 'create_test':
            response = rpc.call('thread/start', {'cwd': str(Path(__file__).resolve().parent), 'approvalPolicy': 'on-request', 'sandbox': 'workspace-write',
                                'developerInstructions': '这是独立功能测试任务。只响应用户发送的简单测试消息；不操作医学研究文件，不主动执行命令或修改文件。'})
            thread = response.get('thread', {})
            created = thread.get('id')
            if not created:
                raise ValueError('后台未返回新测试任务。')
            self._execute('INSERT INTO targets(id,enabled,paused,managed) VALUES(?,1,0,?)', (created, int(not is_claude)))
            rpc.owned_threads.add(created)
            with self.lock:
                self.runtime[created] = dict(threadId=created, mode='control', status='attached', canControl=True, title='桌面功能测试', goalStatus=None, cwd=thread.get('cwd'),
                                             message='测试任务已就绪，等待你发送第一条指令。', retryAt=time.time() + 3600,
                                             codexSettings=self._codex_settings(response))
            try:
                rpc.call('thread/name/set', {'threadId': created, 'name': '桌面功能测试'})
            except RPCError:
                pass
            return '已创建独立测试任务，请选择它发送消息。'
        if action == 'attach':
            update = 'enabled=1' if is_claude else 'enabled=1,managed=excluded.managed'
            with self.lock:
                if self._connection_cancelled(operation):
                    return '任务已移出，已取消接入。'
                self._execute('INSERT INTO targets(id,enabled,paused,managed) VALUES(?,1,0,?) ON CONFLICT(id) DO UPDATE SET ' + update, (tid, int(not is_claude)))
            loaded = self._load(rpc, tid, operation)
            with self.lock:
                if self._connection_cancelled(operation):
                    rpc.owned_threads.discard(tid)
                    return '任务已移出，已取消接入。'
                self.runtime[tid] = loaded
            return loaded['message']
        if action == 'detach':
            with self.lock:
                # Also settle requests queued by older versions before a restart.
                with self.db:
                    self.db.execute('UPDATE targets SET enabled=0 WHERE id=?', (tid,))
                    self.db.execute("UPDATE operations SET status='cancelled',result='任务已移出，已取消接入。' "
                                    "WHERE thread=? AND action IN ('attach','focus') AND status='queued' "
                                    "AND rowid<(SELECT rowid FROM operations WHERE id=?)", (tid, operation))
                self.runtime.pop(tid, None)
            rpc.owned_threads.discard(tid)
            return '已移出监控；未删除任务，也未中断其当前工作。'
        with self.lock:
            row = self.db.execute('SELECT enabled,paused FROM targets WHERE id=?', (tid,)).fetchone()
        if not row or not row[0]:
            raise ValueError('请先接入这个任务。')
        if action in ('manage', 'unmanage'):
            with self.lock:
                self.db.execute('UPDATE targets SET managed=? WHERE id=?', (int(action == 'manage'), tid))
                if is_claude and action == 'manage':
                    self.db.execute("INSERT INTO auto_progress VALUES(?,?,NULL,'scheduled') ON CONFLICT(thread) DO UPDATE SET due=excluded.due,revision=NULL,state='scheduled'", (tid, time.time() + 3600))
                elif is_claude:
                    self.db.execute("UPDATE auto_progress SET state='disabled' WHERE thread=?", (tid,))
                self.db.commit()
            if action == 'unmanage':
                return '已关闭自动推进，保留手动控制。'
            return ('已开启自动推进；一小时后检查空闲、控制权和审批，暂停时不会继续。'
                    if is_claude else '已开启自动推进；仍需控制权和有效目标。')
        if is_claude and not rpc.control_available(tid):
            loaded = self._load(rpc, tid)
            with self.lock:
                self.runtime[tid] = loaded
        with self.lock:
            current = self.runtime.get(tid, {})
        if tid not in rpc.owned_threads or not current.get('canControl'):
            if action == 'pause':
                raise ValueError('自动推进已暂停；原窗口仍在控制任务，无法从本监控中断。')
            reason = current.get('message') or '尚未取得此任务的写入权。'
            raise ValueError(reason + ' 指令尚未发送。')
        try:
            active = self._active_turn(rpc, tid)
            goal_status = self._goal(rpc, tid).get('status') if action == 'resume' else None
        except (ConnectionError, TimeoutError, queue.Empty) as error:
            if is_claude or action not in ('message', 'resume'):
                raise
            if isinstance(error, RPCNotSent) or getattr(rpc, 'closed', False):
                raise RPCNotSent('RPC disconnected during the state check before send') from error
            raise ValueError('未能读取当前轮次状态，指令尚未发送。请稍后重试。') from error
        if action == 'pause':
            self._execute('UPDATE targets SET paused=1 WHERE id=?', (tid,))
            with self.lock:
                fast = self.fast_pauses.pop(operation, None)
            if fast:
                if not fast['done'].wait(21):
                    raise TimeoutError('Pause confirmation unavailable')
                if fast['error']:
                    raise fast['error']
            elif active or is_claude:
                rpc.call('turn/interrupt', {'threadId': tid, 'turnId': active})
            return '已请求暂停当前执行，自动推进已停止。' if active else '已暂停；当前没有运行中的轮次。'
        if action == 'resume':
            if goal_status == 'complete':
                raise ValueError('目标已完成；如需新增工作，请先确认新的任务范围。')
            if goal_status == 'budgetLimited':
                raise ValueError('任务预算已用完；请先在 Codex 中核对预算后再继续。')
            # usageLimited is a stored goal state, not a live account-quota check.
            # An explicit manual Continue may try once after the quota resets.
            # Never rewrite the goal, reset usage or loosen automatic scheduling:
            # the CLI remains authoritative and a rejected send keeps the pause.
            if is_claude and active:
                raise ValueError('正在等待中断完成；暂停状态保持不变，请稍后继续。')
            if not is_claude and active:
                # Reattaching a running Codex thread may leave an earlier local
                # pause intent in place. Explicit Continue releases that intent;
                # it must not steer/start another prompt on an already live turn.
                with self.lock:
                    if not self._send_allowed(tid, operation, action):
                        raise ValueError('任务已再次暂停或移出，没有解除暂停。')
                    self.db.execute('UPDATE targets SET paused=0 WHERE id=?', (tid,))
                    self.db.commit()
                return '当前任务已在运行，已解除本地暂停，可以发送补充指令。'
            text = '继续当前已授权任务，先核对进度和计划门槛，再推进可执行工作。'
        if action == 'message' and row[1]:
            raise ValueError('任务已暂停，请先点击继续。')
        if not self._send_allowed(tid, operation, action):
            raise ValueError('任务已暂停或移出，没有发送这条指令。')
        params = {'threadId': tid, 'input': [{'type': 'text', 'text': text}], 'clientUserMessageId': operation}
        params['_sendGuard'] = lambda: self._send_allowed(tid, operation, action)
        if active:
            params['expectedTurnId'] = active
            rpc.call('turn/steer', params)
        else:
            rpc.call('turn/start', params)
        if action == 'resume':
            with self.lock:
                if self._send_allowed(tid, operation, action):
                    self.db.execute('UPDATE targets SET paused=0 WHERE id=?', (tid,))
                    self.db.commit()
        return '插队指令已交给当前会话，等待回复。' if active else '消息已交给当前任务，等待回复。'

    def _drain(self, rpc, urgent=False):
        with self.lock:
            condition = " AND action IN ('pause','detach')" if urgent else ''
            pending = self.db.execute("SELECT id,thread,action,text FROM operations WHERE status='queued'" + condition +
                                      " ORDER BY CASE WHEN action='pause' THEN 0 WHEN action='detach' THEN 1 ELSE 2 END,stamp,rowid LIMIT 8").fetchall()
        for key, tid, action, text in pending:
            if getattr(rpc, 'closed', False):
                break
            with self.lock:
                cur = self.db.execute("UPDATE operations SET status='running' WHERE id=? AND status='queued'", (key,))
                self.db.commit()
                if not cur.rowcount:
                    continue
            try:
                if not self.control_allowed():
                    raise ValueError('本机正在交还控制权，未执行操作。')
                if action in ('message', 'resume') and getattr(rpc, 'client_type', 'codex') == 'codex' and getattr(rpc, 'closed', False):
                    raise RPCNotSent('RPC disconnected before dispatch')
                result = self._perform(rpc, tid, action, text, key)
                status = 'succeeded'
            except RPCNotSent:
                with self.lock:
                    if action in ('message', 'resume') and self._send_allowed(tid, key, action):
                        status, result = 'queued', '连接中断，指令尚未发送。重连后将继续发送。'
                    else:
                        status, result = 'cancelled', '连接中断，未执行操作。'
                    self._execute("UPDATE operations SET status=?,result=? WHERE id=? AND status='running'", (status, result, key))
                break
            except (RPCError, ValueError) as error:
                result = str(error) if isinstance(error, ValueError) else '接口拒绝操作：' + str(error.detail or '')[:300]
                status = 'failed'
            except Exception:
                result = '操作结果不确定，请核查任务；不会自动重发。'
                status = 'unknown'
            self._execute("UPDATE operations SET status=?,result=? WHERE id=? AND status='running'", (status, result, key))

    def tick_auto_progress(self, rpc, now=None):
        """Local Claude scheduling, separate from the unchanged manual RPC path.

        Enabling starts a one-hour timer. Live state is rechecked inside the
        adapter's existing send guard; an uncertain send is never replayed.
        """
        if getattr(rpc, 'client_type', 'codex') != 'claude' or not self.tick_lock.acquire(False):
            return []
        now = time.time() if now is None else now
        results = []
        try:
            with self.lock:
                rows = self.db.execute("SELECT a.thread,a.due,a.revision FROM auto_progress a JOIN targets t ON t.id=a.thread WHERE t.enabled=1 AND t.managed=1 AND t.paused=0 AND a.state='scheduled' AND a.due<=?", (now,)).fetchall()
            for tid, due, previous_revision in rows:
                def eligible(operation=None):
                    if not self.control_allowed():
                        return False
                    with self.lock:
                        target = self.db.execute('SELECT enabled,paused,managed FROM targets WHERE id=?', (tid,)).fetchone()
                        pending = self.db.execute("SELECT 1 FROM operations WHERE thread=? AND status IN ('queued','running') AND id!=? LIMIT 1", (tid, operation or '')).fetchone()
                        schedule = self.db.execute('SELECT due,state FROM auto_progress WHERE thread=?', (tid,)).fetchone()
                    if not target or not target[0] or target[1] or not target[2] or pending:
                        return False
                    if not schedule or schedule[1] != ('reserved' if operation else 'scheduled'):
                        return False
                    if self.connection is not rpc or getattr(rpc, 'closed', False) or tid not in rpc.owned_threads:
                        return False
                    if not self.runtime.get(tid, {}).get('canControl') or not rpc.control_available(tid):
                        return False
                    session = getattr(rpc, 'running', {}).get(tid)
                    if session is None or session.process.poll() is not None or session.active_turn or session.interrupting or session.last_error:
                        return False
                    approvals = getattr(rpc, 'approvals', None)
                    return not any(item.get('client', 'claude') == 'claude' and item.get('decision') is None and
                                   item.get('details', {}).get('threadId') == tid and item.get('expires', now + 1) > now
                                   for item in (approvals.public() if approvals else []))

                try:
                    if not eligible():
                        self._execute("UPDATE auto_progress SET due=? WHERE thread=? AND state='scheduled'", (now + 300, tid))
                        results.append('blocked:conditions')
                        continue
                    thread = rpc.call('thread/read', {'threadId': tid, 'includeTurns': False}).get('thread', {})
                    status = thread.get('status') or {}
                    revision = thread.get('updatedAt')
                    if status.get('type') != 'idle' or status.get('activeFlags') or revision is None:
                        self._execute("UPDATE auto_progress SET due=? WHERE thread=? AND state='scheduled'", (now + 300, tid))
                        results.append('blocked:state')
                        continue
                    revision = str(revision)
                    if revision == previous_revision:
                        self._execute("UPDATE auto_progress SET due=? WHERE thread=? AND state='scheduled'", (now + 300, tid))
                        results.append('blocked:duplicate')
                        continue
                    key = 'auto-' + uuid.uuid4().hex
                    with self.lock:
                        if not eligible():
                            continue
                        # Reserve atomically before transport. Restart turns running
                        # operations unknown and disables reserved schedules.
                        changed = self.db.execute("UPDATE auto_progress SET due=?,revision=?,state='reserved' WHERE thread=? AND state='scheduled' AND due=?", (now + 3600, revision, tid, due))
                        if not changed.rowcount:
                            self.db.commit()
                            continue
                        self.db.execute("INSERT INTO operations VALUES(?,?,'message',?,'running','自动推进：等待发送确认',?)",
                                        (key, tid, '继续当前已授权且尚未完成的任务；先核对进度。已完成、需要人工输入或额度受限时说明情况，不创建替代任务。', time.time()))
                        self.db.commit()
                    guard_blocked = False
                    def send_guard():
                        nonlocal guard_blocked
                        guard_blocked = not eligible(key)
                        return not guard_blocked
                    try:
                        rpc.call('turn/start', {'threadId': tid, 'input': [{'type': 'text', 'text': '继续当前已授权且尚未完成的任务；先核对进度。已完成、需要人工输入或额度受限时说明情况，不创建替代任务。'}],
                                                'clientUserMessageId': key, '_sendGuard': send_guard})
                    except Exception as error:
                        if guard_blocked:
                            with self.lock:
                                self.db.execute("UPDATE operations SET status='cancelled',result='条件已变化，本次自动推进未发送。' WHERE id=?", (key,))
                                self.db.execute("UPDATE auto_progress SET due=?,revision=?,state='scheduled' WHERE thread=? AND state='reserved'", (now + 300, previous_revision, tid))
                                self.db.commit()
                            results.append('blocked:changed')
                        else:
                            message = '自动推进发送失败，请检查会话后重新开启。' if isinstance(error, RPCError) else '自动推进送达结果不确定，已停止，不会自动重发。'
                            with self.lock:
                                self.db.execute('UPDATE operations SET status=?,result=? WHERE id=?', ('failed' if isinstance(error, RPCError) else 'unknown', message, key))
                                self.db.execute('UPDATE targets SET managed=0 WHERE id=?', (tid,))
                                self.db.execute("UPDATE auto_progress SET state='unknown' WHERE thread=?", (tid,))
                                self.db.commit()
                            results.append('blocked:delivery')
                    else:
                        with self.lock:
                            self.db.execute("UPDATE operations SET status='succeeded',result='自动推进指令已发送，等待回复。' WHERE id=?", (key,))
                            self.db.execute("UPDATE auto_progress SET state='scheduled' WHERE thread=? AND state='reserved'", (tid,))
                            self.db.commit()
                        results.append('sent')
                except Exception:
                    self._execute("UPDATE auto_progress SET due=? WHERE thread=? AND state='scheduled'", (now + 300, tid))
                    results.append('blocked:connection')
            return results
        finally:
            self.tick_lock.release()

    def tick(self, rpc, *, refresh=True):
        if getattr(rpc, 'closed', False) or not self.tick_lock.acquire(False):
            return
        try:
            with self.lock:
                if rpc is not self.connection:
                    if self.connection is not None:
                        self._execute("UPDATE operations SET status='cancelled',result='原 Codex 连接已结束，请重新选择设置。' "
                                      "WHERE status='queued' AND action='codex_settings'")
                    self.connection = rpc
                    self.runtime = {}
            # Existing owned sessions can be stopped before slow history reads.
            self._drain(rpc, urgent=True)
            with self.lock:
                targets = self.db.execute('SELECT id FROM targets WHERE enabled=1').fetchall()
            for (tid,) in targets:
                with self.lock:
                    old = self.runtime.get(tid)
                if not old or (refresh and time.time() >= old.get('retryAt', 0)):
                    loaded = self._load(rpc, tid)
                    if getattr(rpc, 'closed', False):
                        return
                    with self.lock:
                        self.runtime[tid] = loaded
                current = self.runtime.get(tid, {})
                if current.get('canControl') and getattr(rpc, 'client_type', 'codex') == 'claude' and not rpc.control_available(tid):
                    current = self._load(rpc, tid)
                    with self.lock:
                        self.runtime[tid] = current
            self._drain(rpc)
            if not refresh or getattr(rpc, 'closed', False):
                return
            with self.lock:
                targets = self.db.execute('SELECT id FROM targets WHERE enabled=1').fetchall()
            for (tid,) in targets:
                with self.lock:
                    current = self.runtime.get(tid, {})
                if current.get('canControl') and time.time() - current.get('checkedAt', 0) >= 1:
                    try:
                        status = rpc.call('thread/read', {'threadId': tid, 'includeTurns': False}).get('thread', {}).get('status')
                        goal = self._goal(rpc, tid).get('status')
                    except RPCError:
                        status, goal = {'type': 'unknown'}, None
                    with self.lock:
                        current.update(threadStatus=status, goalStatus=goal, checkedAt=time.time())
        except ConnectionError:
            if not getattr(rpc, 'closed', False):
                raise
        finally:
            self.tick_lock.release()

    def snapshot(self):
        with self.lock:
            names = dict(self.db.execute('SELECT id,name FROM task_names'))
            progress = {row[0]: {'due': row[1], 'state': row[2]} for row in self.db.execute('SELECT thread,due,state FROM auto_progress')}
            tasks = []
            for tid, paused, managed in self.db.execute('SELECT id,paused,managed FROM targets WHERE enabled=1'):
                item = dict(self.runtime.get(tid, {'threadId': tid, 'status': 'connecting', 'message': '后台正在自动接入。', 'canControl': False}),
                            paused=bool(paused), managed=bool(managed))
                if tid in names:
                    item['title'] = names[tid]
                if tid in progress:
                    item['autoProgress'] = progress[tid]
                tasks.append(item)
            operations = [dict(id=r[0], threadId=r[1], action=r[2], status=r[3], message=r[4], stamp=r[5])
                          for r in self.db.execute('SELECT id,thread,action,status,result,stamp FROM operations ORDER BY stamp DESC,rowid DESC LIMIT 100')]
            return dict(tasks=tasks, operations=operations, names=names, ready=True)

    def automatic_allowed(self, rpc, tid):
        """Read current stop intent again immediately before an automatic write."""
        with self.lock:
            row = self.db.execute('SELECT enabled,paused,managed FROM targets WHERE id=?', (tid,)).fetchone()
            pending = self.db.execute("SELECT 1 FROM operations WHERE thread=? AND status IN ('queued','running') LIMIT 1", (tid,)).fetchone()
            return bool(self.control_allowed() and row and row[0] and not row[1] and row[2] and not pending and self.connection is rpc and
                        tid in getattr(rpc, 'owned_threads', set()) and self.runtime.get(tid, {}).get('canControl'))

    def automatic_tasks(self, rpc, tasks, *, explicit_only=False):
        # Explicit local pause/detach/unmanage must also override duplicate entries
        # from configuration; those must not resurrect a removed wait on restart.
        with self.lock:
            targets = {r[0]: r[1:] for r in self.db.execute('SELECT id,enabled,paused,managed FROM targets')}
        result = {}
        for task in tasks:
            tid = task['threadId']
            if explicit_only and tid not in targets:
                continue
            if tid in targets:
                enabled, paused, managed = targets[tid]
                if not enabled or paused or not managed:
                    continue
                task = {**task, '_sendGuard': lambda tid=tid: self.automatic_allowed(rpc, tid),
                        '_goalStatus': self.runtime.get(tid, {}).get('goalStatus')}
            result.setdefault(tid, task)
        return list(result.values())

    def scheduled_tasks(self, rpc):
        if getattr(rpc, 'client_type', 'codex') == 'claude':
            return []
        with self.lock:
            return [dict(threadId=tid, interval=3600, prompt='检查当前长程任务进度，按照已授权计划推进可执行工作；尊重暂停、完成与额度限制，保存结果。', enabled=True, exclusiveOwner=True,
                         _sendGuard=lambda tid=tid: self.automatic_allowed(rpc, tid), _goalStatus=self.runtime.get(tid, {}).get('goalStatus'))
                    for (tid,) in self.db.execute('SELECT id FROM targets WHERE enabled=1 AND managed=1 AND paused=0')
                    if tid in rpc.owned_threads and self.runtime.get(tid, {}).get('canControl')]
