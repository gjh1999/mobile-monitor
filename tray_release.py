"""Coordinate local write shutdown and verified task handoff while in the tray."""

import queue
import subprocess
import threading

from rpc_errors import RPCError

CLIENTS = ("codex", "claude")


class TrayRelease:
    def __init__(self):
        self.lock = threading.Lock()
        self.hidden = False
        self.generation = 0
        self.active = set()
        self.progress = {}

    def set(self, hidden):
        with self.lock:
            if hidden and not self.hidden:
                self.generation += 1
                self.progress = dict.fromkeys(CLIENTS, ("releasing", 0, "正在检查本机任务。"))
            self.hidden = hidden

    def control_allowed(self):
        with self.lock:
            return not self.hidden and not self.active

    def managed_allowed(self):
        with self.lock:
            return not self.generation

    def begin(self, client):
        with self.lock:
            if self.hidden and self.progress[client][0] != "released":
                self.active.add(client)
                return self.generation
            return None

    def continuing(self, generation):
        with self.lock:
            return self.hidden and self.generation == generation

    def finish(self, client, generation, result):
        with self.lock:
            self.active.discard(client)
            if generation == self.generation:
                self.progress[client] = result

    def status(self):
        with self.lock:
            if not self.progress or not self.hidden and not self.active:
                return {"state": "idle", "pending": 0, "detail": ""}
            if not self.hidden:
                return {"state": "releasing", "pending": 0, "detail": "正在结束本次任务交还。"}
            rows = list(self.progress.values())
            waiting = [row for row in rows if row[0] == "waiting"]
            releasing = [row for row in rows if row[0] == "releasing"]
            state, _, detail = (waiting or releasing or rows)[0]
            return {"state": state, "pending": sum(row[1] for row in rows), "detail": detail}


class CodexTrayRelease:
    def __init__(self, stop=None, continuing=lambda: True, pending_approval=lambda thread: False):
        self.stop = stop
        self.sync = None
        self.flow = None
        self.continuing = continuing
        self.pending_approval = pending_approval

    def step(self, rpc, flow, drained):
        if rpc is not None and not rpc.drain_for_tray():
            return "waiting", len(rpc.loaded_threads | rpc.owned_threads), "等待本机 Codex 轮次和审批结束。"
        writers = 0
        for previous in [*drained, rpc]:
            if previous is not None and previous.project_writes:
                if previous.p.poll() is None:
                    return "waiting", 0, "等待本机 Codex 进程退出。"
                previous.project_writes.release_closed()
                writers += len(previous.project_writes.tickets)
        locks = flow.task_locks() if flow else None
        if not locks:
            if writers:
                return "waiting", writers, "登记项目的写入权等待服务器确认。"
            return "released", 0, "本机任务控制权已交还。"

        active = locks.request()
        threads = sorted(tid for tid, owner in active["tasks"].items() if owner == locks.client.device)
        if threads:
            if self.flow is not flow or self.sync is None:
                self.flow = flow
                self.sync = flow.request_manual_sync(fresh=True)
            if not self.sync.done():
                return "waiting", len(threads) + writers, "正在等待代码和已完成会话同步。"
            if self.sync.cancelled() or self.sync.result() is None:
                self.sync = None
                return "waiting", len(threads) + writers, "同步尚未完成，后台将重试。"
            ready = [tid for tid in threads if not self.pending_approval(tid)]
            receipts, errors = flow.handoff_checkpoints(
                ready, handoff=locks.handoff_held,
                continuing=lambda: self.continuing() and (self.stop is None or not self.stop.is_set()),
            )
            pending = len(threads) - len(receipts)
            if pending:
                detail = next(iter(errors.values()), "等待待审批的任务结束。" if len(ready) != len(threads)
                              else "等待服务器确认任务交还。")
                return "waiting", pending + writers, detail
        if writers:
            return "waiting", writers, "登记项目的写入权等待服务器确认。"
        return "released", 0, "本机任务控制权已交还。"


def release_step(callback):
    try:
        return callback()
    except (RPCError, OSError, ValueError, TypeError, KeyError, queue.Empty, subprocess.TimeoutExpired):
        return "waiting", 0, "本机或协调服务暂时无法确认交还，后台将重试。"
