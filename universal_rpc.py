"""Universal RPC interface supporting both Codex and Claude Code."""
import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from automation import APPROVAL_METHODS


from rpc_errors import RPCError
from coordination_client import ControlDenied, ProjectWrites, personal_project_at


class UniversalRPC:
    """Base RPC client for Anthropic CLI tools."""

    def __init__(self, executable_name: str, journal=None, custom_process=None, env_var: Optional[str] = None):
        """
        Args:
            executable_name: 'codex' or 'claude'
            journal: Optional journal for recording operations
            custom_process: Override process detection
            env_var: Optional environment variable to check for executable path
        """
        self.journal = journal
        self.pending = {}
        self.seq = 0
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.owned_threads = set()
        self.closed = False
        self.executable_name = executable_name

        if custom_process is None:
            process = self._find_process(executable_name, env_var)
        else:
            process = custom_process

        self.p = process
        threading.Thread(target=self.read, daemon=True).start()
        try:
            self.call('initialize', {
                'clientInfo': {
                    'name': 'mobile_monitor',
                    'version': '0.4.0',
                    'client': executable_name
                },
                'capabilities': {'experimentalApi': True, **(
                    {'requiredHumanApproval': True} if getattr(self, 'monitor_only', False) else {})}
            })
            self.send({'method': 'initialized'})
        except Exception:
            self.close()
            raise

    @staticmethod
    def _find_process(executable_name: str, env_var: Optional[str] = None, *, arguments=None):
        """Locate and launch the executable."""
        exe = None

        # Check environment variable first
        if env_var and env_var in os.environ:
            exe = os.environ[env_var]

        # Try PATH
        if not exe:
            exe = shutil.which(f'{executable_name}.exe')

        # Try npm global modules
        if not exe and executable_name == 'claude':
            try:
                exe = str(next(
                    (Path(os.environ['APPDATA']) / 'npm/node_modules/@anthropic-ai/claude-code')
                    .rglob('claude.exe')
                ))
            except (StopIteration, KeyError):
                pass

        if not exe and executable_name == 'codex':
            npm = Path(os.environ.get('APPDATA', '')) / 'npm/node_modules/@openai/codex'
            exe = next((str(p) for p in npm.glob('**/codex.exe')), None)
        if not exe:
            exe = shutil.which(executable_name)
        if not exe:
            raise FileNotFoundError(f"Cannot find {executable_name} CLI; install it or add it to PATH")


        process = subprocess.Popen(
            [exe, *(arguments if arguments is not None else ('app-server', '--stdio'))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        )
        return process

    def send(self, x: Dict[str, Any], guard=None):
        """Recheck local stop intent after acquiring the transport write lock."""
        with self.write_lock:
            if guard is not None and not guard():
                raise RPCError(detail='任务状态已变化，未发送指令。')
            if self.closed:
                raise ConnectionError('RPC disconnected')
            self.p.stdin.write(json.dumps(x) + '\n')
            self.p.stdin.flush()

    def dispatch(self, x: Dict[str, Any]):
        """Dispatch incoming message."""
        completed = getattr(self, 'report_completion', None)
        if completed is not None and x.get('method') == 'turn/completed':
            completed(x.get('params') or {})
        if getattr(self, 'journal', None):
            self.journal.record(x, self.owned_threads)
        if 'id' in x and 'method' in x:
            if x['method'] in APPROVAL_METHODS:
                # Handle approval if available
                if hasattr(self, 'approvals'):
                    self.approvals.add(self, x)
            else:
                self.send({'id': x['id'], 'error': {'code': -32601, 'message': 'Unsupported request'}})
        elif 'id' in x:
            with self.lock:
                q = self.pending.get(x['id'])
            if q:
                q.put(x)
        elif x.get('method') == 'serverRequest/resolved':
            if hasattr(self, 'approvals'):
                self.approvals.resolve(self, x.get('params', {}).get('requestId'))

    def read(self):
        """Read from process stdout."""
        try:
            for line in self.p.stdout:
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                self.dispatch(x)
        finally:
            self.mark_closed()

    def mark_closed(self):
        """Mark RPC as closed."""
        self.closed = True
        if hasattr(self, 'approvals'):
            self.approvals.disconnect(self)
        with self.lock:
            for q in self.pending.values():
                q.put({'disconnected': True})

    def close(self):
        """Close RPC connection."""
        self.mark_closed()
        if self.p.poll() is None:
            self.p.terminate()

    def call(self, method: str, params: Dict[str, Any], timeout: int = 20) -> Dict[str, Any]:
        """Make RPC call; local guards never cross the JSON-RPC boundary."""
        params = dict(params)
        guard = params.pop('_sendGuard', None)
        with self.lock:
            self.seq += 1
            i = self.seq
            q = queue.Queue()
            self.pending[i] = q
        try:
            self.send({'id': i, 'method': method, 'params': params}, guard=guard)
            x = q.get(timeout=timeout)
            if x.get('disconnected'):
                raise ConnectionError('RPC disconnected')
            if 'error' in x:
                error = x['error'] if isinstance(x['error'], dict) else {}
                raise RPCError(
                    'RPC request rejected',
                    code=error.get('code'),
                    detail=str(error.get('message', ''))[:500]
                )
            result = x.get('result', {})
            if method == 'turn/start' and getattr(self, 'journal', None):
                self.journal.track_turn(params['threadId'], (result.get('turn') or {}).get('id'))
            return result
        finally:
            with self.lock:
                self.pending.pop(i, None)


class CodexRPC(UniversalRPC):
    """Codex-specific RPC client."""
    project_writes = None
    personal_flow = None
    handing_off = False
    control_methods = frozenset({
        'thread/start', 'thread/resume', 'thread/name/set', 'thread/settings/update',
        'thread/goal/set', 'turn/start', 'turn/steer', 'turn/interrupt',
    })

    def control_allowed(self):
        return True

    def __init__(self, approvals, journal=None, custom_process=None, coordination=None):
        self.approvals = approvals
        self.project_writes = ProjectWrites(coordination) if coordination and coordination.paths else None
        self.loaded_threads = set()
        self.draining = False
        super().__init__('codex', journal=journal, custom_process=custom_process)

    def call(self, method, params, timeout=20):
        if method in self.control_methods:
            if not self.control_allowed():
                raise ControlDenied(detail='任务控制暂停，正在交还本机写入权。')
            initial_guard = params.get('_sendGuard')
            params = {**params, '_sendGuard': lambda: self.control_allowed() and
                      (initial_guard is None or initial_guard())}
        automatic_resume = method == 'thread/resume' and not params.get('cwd')
        if automatic_resume and getattr(self, 'personal_session', None):
            session = self.personal_session(params['threadId'])
            if session:
                params = {**params, 'cwd': session['cwd'], 'path': session['path']}
        writes = self.project_writes
        if method == 'thread/start':
            result = super().call(method, params, timeout)
            self.loaded_threads.add(result['thread']['id'])
            return result
        thread = params.get('threadId')
        reading = method in ('thread/read', 'thread/turns/list', 'thread/goal/get', 'thread/settings/read')
        if thread and not reading and self.personal_flow:
            flow = self.personal_flow()
            locks = flow.task_locks() if flow else None
            project = self.personal_project(thread, params.get('cwd')) if locks else None
            if project:
                previous = params.get('_sendGuard')

                def personal_guard():
                    if self.handing_off:
                        raise ControlDenied(detail='当前正在交还任务，请稍候。')
                    if previous and not previous():
                        return False
                    try:
                        locks.claim(thread, project, flow.session_digest)
                    except ControlDenied as error:
                        if method == 'thread/resume':
                            raise ControlDenied(detail='active writer: ' + error.detail) from error
                        raise
                    return previous is None or previous()

                params = {**params, '_sendGuard': personal_guard}
        if not writes:
            result = super().call(method, params, timeout)
            if method == 'thread/resume':
                self.loaded_threads.add(thread)
            return result
        if method not in ('turn/start', 'turn/steer', 'thread/goal/set', 'thread/resume', 'thread/settings/update'):
            return super().call(method, params, timeout)
        thread = params['threadId']
        project = writes.project_for(self, thread, params.get('cwd'))
        if project is None:
            result = super().call(method, params, timeout)
            if method == 'thread/resume':
                self.loaded_threads.add(thread)
            return result
        previous_guard = params.get('_sendGuard')
        reservation = None

        def guard():
            nonlocal reservation
            if previous_guard and not previous_guard():
                return False
            try:
                reservation = writes.reserve(project, thread)
            except ControlDenied as error:
                if method == 'thread/resume':
                    raise ControlDenied(detail='active writer: ' + error.detail) from error
                raise
            return previous_guard is None or previous_guard()

        confirmed = False
        accepted = False
        turn = None
        wired = {**params, '_sendGuard': guard}
        if automatic_resume:
            mapped = writes.client.restored_directory(project, thread)
            if params.get('cwd') and writes.client.project_at(params['cwd']) != project:
                raise ControlDenied(detail='个人会话目录与协调服务登记的项目不一致')
            if mapped and not params.get('cwd'):
                wired['cwd'] = mapped
        try:
            result = super().call(method, wired, timeout)
            accepted = True
            if method == 'thread/resume':
                self.loaded_threads.add(thread)
            elif method == 'turn/start':
                turn = result.get('turn', {}).get('id')
            elif method == 'turn/steer':
                turn = params.get('expectedTurnId')
            confirmed = method not in ('turn/start', 'turn/steer') or bool(turn)
            return result
        except RPCError:
            # An explicit RPC error settles a sent request. A refused preflight
            # never created a new local reservation for this request.
            confirmed = True
            raise
        finally:
            if reservation is not None:
                with self.write_lock:
                    reservation.pending -= 1
                    reservation.uncertain = reservation.uncertain or not confirmed
                    if thread in self.loaded_threads:
                        reservation.attached = True
                    reservation.needs_idle |= accepted
                    if turn:
                        reservation.turn = turn

    def approval_guard(self, thread):
        writes = self.project_writes
        flow = self.personal_flow() if self.personal_flow else None
        locks = flow.task_locks() if flow else None
        if locks:
            if not thread:
                raise ControlDenied(detail='无法核对此审批所属的任务。')
            project = self.personal_project(thread)
            if not project:
                return self.project_approval_guard(thread) if writes else None
            owner_guard = self.project_approval_guard(thread) if writes else None

            def guard():
                if self.handing_off:
                    return False
                if self.control_allowed():
                    locks.claim(thread, project, flow.session_digest)
                else:
                    locks.require_approval_owner(thread, project)
                return owner_guard() if owner_guard else True
            return guard
        return self.project_approval_guard(thread) if writes else None

    def project_approval_guard(self, thread):
        writes = self.project_writes
        if not writes:
            return None
        if not thread:
            raise ControlDenied(detail='无法核对此审批所属的线程')
        project = writes.project_for(self, thread)
        if project is None:
            return None

        def guard():
            writes.reserve(project, thread, approval=True)
            return True

        return guard

    def personal_project(self, thread, cwd=None):
        session = self.personal_session(thread) if getattr(self, 'personal_session', None) else None
        if session:
            return session['project']
        if not cwd:
            cwd = self.call('thread/read', {'threadId': thread, 'includeTurns': False}).get('thread', {}).get('cwd')
        if not cwd or not Path(cwd).is_dir():
            raise ControlDenied(detail='active writer: 尚未核对会话对应的本机项目，请等目录同步后重试。')
        return personal_project_at(cwd)

    def handoff_personal(self, thread):
        flow = self.personal_flow() if self.personal_flow else None
        locks = flow.task_locks() if flow else None
        if not locks:
            raise ControlDenied(detail='请先完成澜汐云配对。')
        route = '/' + thread.lower()
        state = locks.request(route)
        if not state['device'] and state['checkpoint']:
            return
        if state['device'] != locks.client.device:
            raise ControlDenied(detail='只有当前持有任务的电脑可以交还。')
        project = self.personal_project(thread)
        if not project:
            raise ControlDenied(detail='尚未找到任务对应的 GitHub 项目。')
        ticket = locks.claim(thread, project, flow.session_digest)
        with self.write_lock:
            self.handing_off = True
        try:
            if not all(ProjectWrites.idle(self, tid, allow_unloaded=True)
                       for tid in self.loaded_threads | self.owned_threads):
                raise ControlDenied(detail='请等本机运行中的 Codex 对话结束并处理审批，再交还任务。')
            with self.write_lock:
                self.closed = True
                self.p.stdin.close()
            self.p.wait(timeout=5)
            if self.project_writes:
                self.project_writes.release_closed()
            checkpoint = flow.publish_handoff(thread)
            locks.handoff(thread, ticket, checkpoint)
        finally:
            self.handing_off = False

    def drain_for_tray(self):
        if self.closed:
            return self.p.poll() is not None
        if self.approvals.pending_on(self):
            return False
        with self.write_lock:
            writes = self.project_writes.tickets if self.project_writes else {}
            if any(write.pending or write.uncertain for write in writes.values()):
                return False
            tracked = {thread: write.turn for (_, thread), write in writes.items()}
            loaded = self.loaded_threads | self.owned_threads
        if not all(ProjectWrites.idle(self, tid, tracked.get(tid), allow_unloaded=True)
                   for tid in sorted(loaded | set(tracked))):
            return False
        if self.approvals.pending_on(self):
            return False
        with self.write_lock:
            self.draining = True
            self.closed = True
            self.p.stdin.close()
        self.p.wait(timeout=3)
        return True

    def reconcile_writes(self, shutdown=False):
        self.project_writes.release_finished(self)
        return self.project_writes.yield_to_foreground(self, shutdown)


# Claude Code uses its own stream-json control protocol, not Codex app-server.
from claude_rpc import ClaudeCodeRPC

