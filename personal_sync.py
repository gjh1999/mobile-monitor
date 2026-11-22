"""One personal sync worker per workstation. GitHub carries code; paired storage carries private state."""

import hashlib
import json
import os
import queue
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from concurrent.futures import Future
from pathlib import Path

from code_sync import (
    SyncIssue,
    UnsafeRepository,
    exclude_memory,
    github_id,
    run,
    sync_project,
    value,
    verify_checkout,
)
from coordination_client import ControlClient, ControlDenied, PersonalTaskLocks, device_token
from handoff import native_publication_lock
from handoff_sync import atomic_write
from personal_memory import (
    Mirror, confirm_published_session, pack, purge_session_copies, read, read_transcript, replace, safe_path, sync_memories,
)
from personal_relay import (
    EMPTY, RETIREMENT_ACK_PREFIX, RETIREMENT_PREFIX, RETIREMENT_SOURCES_PREFIX, Changed, Records, RemoteRecords,
    digest, retirement_markdown,
)
from plain_projects import folder_bindings, folder_id, folder_index
from project_library import configured_root, discover
from rpc_errors import RPCError


def save_retirement_summary(root, tid, markdown, expected_sha):
    if str(uuid.UUID(tid)) != tid or not isinstance(markdown, str):
        raise ValueError("会话摘要标识无效")
    data = markdown.encode("utf-8")
    if not markdown.strip() or len(data) > 64 * 1024 or digest(data) != expected_sha:
        raise ValueError("会话摘要内容或校验值无效")
    path = safe_path(root, f"会话摘要/{tid}.md")
    previous = read(path, limit=64 * 1024)
    if previous is None:
        replace(path, None, data, None, limit=64 * 1024)
    elif previous != data:
        raise Changed("同名会话摘要与服务器内容不同，已保留本机文档")
    if read(path, limit=64 * 1024) != data:
        raise Changed("会话摘要保存后校验失败")
    return path


class PersonalSync:
    def __init__(self, root, settings, coordination=None, home=None):
        self.root = Path(root)
        self.settings = settings
        self.home = Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        self.registry = Path(settings.get("registry", self.home / "git-sync-projects.json"))
        self.checkout = Path(settings.get("checkoutRoot", Path.home() / "LanxiProjects"))
        if not self.checkout.is_absolute():
            raise ValueError("本机项目根目录需要绝对路径")
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.projects = {}
        self.folders = {}
        self.code_folders = set()
        self.git_retries = {}
        self.threads = {}
        self.status = {}
        self.backup_status = {}
        self.completed = {}
        self.epoch = 0
        self.coordination = coordination
        self.pending_settings = None
        self.priority = None
        self._active_sync = None
        self._pending_sync = None
        self._sync_phase = ""
        self._sync_started = 0
        self._sync_total = 0
        self._sync_finished = 0
        self._sync_projects = {}
        self.poll_cache = {}
        self.local_state = self.root / ".local" / "personal-sync"
        self.scope = None
        self.connection = None
        self.task_owners = None
        self.notebook_revision = 0
        self.large_inventory = {}
        self.large_available = {}
        self.file_exchange = None
        self.retirements = {}
        self.retirement_supported = False
        self.retirement_delete = None

    def store(self):
        if self.settings.get("codeOnly"):
            return None
        if self.settings.get("exchange"):
            share = Path(self.settings["exchange"])
            if not share.is_absolute() or not share.is_dir():
                raise SyncIssue("私人共享目录不可用")
            if any((p / ".git").exists() for p in (share, *share.parents)):
                raise SyncIssue("私人共享目录必须位于 Git 项目之外")
            return Records(share / "lanxi-personal.sqlite", "personal")
        if self.coordination:
            client = ControlClient(self.coordination, device_token(self.coordination))
            return RemoteRecords(client)
        return None

    def snapshot(self):
        with self.lock:
            return {
                "projects": dict(self.projects),
                "folderProjects": dict(self.folders),
                "sessions": dict(self.threads),
                "status": dict(self.status),
                "codeBackups": dict(self.backup_status),
                "completed": dict(self.completed),
                "epoch": self.epoch,
                "connection": self.connection,
                "taskOwners": self.task_owners,
                "notebookRevision": self.notebook_revision,
                "largeInventory": dict(self.large_inventory),
                "largeAvailable": dict(self.large_available),
                "retirements": {tid: dict(row) for tid, row in self.retirements.items()},
                "retirementSupported": self.retirement_supported,
            }

    def _prepare_retirements(self, store, index, errors, stop):
        with self.lock:
            self.retirement_supported = False
        if not isinstance(store, RemoteRecords) or stop.is_set():
            return {}, {}
        try:
            supported = store.retirement_supported()
        except (OSError, ValueError):
            errors["会话摘要"] = "暂时无法核对服务器会话整理能力"
            return {}, {}
        if not supported:
            return {}, {}
        revisions = {key.removeprefix(RETIREMENT_PREFIX): rev for key, rev in index.items()
                     if key.startswith(RETIREMENT_PREFIX) and rev != EMPTY}
        canonical = re.compile(r"[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}")
        rows = {tid: {"acknowledged": [], "document": None, "status": "本机等待处理"}
                for tid in revisions if canonical.fullmatch(tid)}
        ack = pack({"v": 1})
        ack_digest = digest(ack)
        device = store.client.device
        for key, rev in index.items():
            if key.startswith(RETIREMENT_ACK_PREFIX) and rev == ack_digest:
                tid, slash, owner = key.removeprefix(RETIREMENT_ACK_PREFIX).partition("/")
                if tid in rows and slash and owner and "/" not in owner:
                    rows[tid]["acknowledged"].append(owner)

        pending = {}
        for tid, row in rows.items():
            if stop.is_set():
                break
            row["acknowledged"].sort()
            try:
                path = safe_path(self.root, f"会话摘要/{tid}.md")
                if device in row["acknowledged"] and path.is_file():
                    row.update(document=str(path), status="本机已处理")
                    continue
                data = store.read(RETIREMENT_PREFIX + tid)
                if digest(data) != revisions[tid]:
                    raise Changed("服务器会话整理记录校验失败")
                markdown = retirement_markdown(data)
                path = save_retirement_summary(self.root, tid, markdown, digest(markdown.encode("utf-8")))
                row["document"] = str(path)
                if device in row["acknowledged"]:
                    row["status"] = "本机已处理"
                    continue
                source_key = RETIREMENT_SOURCES_PREFIX + tid
                sources = store.read(source_key)
                if digest(sources) != index.get(source_key, EMPTY):
                    raise Changed("会话分块清单定位校验失败")
                part_info = json.loads(sources)
                directories = part_info.get("partDirectories") if isinstance(part_info, dict) else None
                if (not isinstance(directories, list) or type(part_info.get("v")) is not int or part_info["v"] != 1
                        or any(not isinstance(name, str) or not re.fullmatch(r"[a-f0-9]{16}", name) for name in directories)):
                    raise ValueError("会话分块清单定位无效")
                delete = self.retirement_delete
                if delete is None:
                    from session_retirement import delete_local_session
                    delete = delete_local_session
                delete(self.root, self.home, tid)
                row["status"] = "本机正在清理同步副本"
                pending[tid] = directories
            except (OSError, RPCError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
                row["status"] = str(error) if isinstance(error, ValueError) else "本机整理暂未完成，后台将重试"
                errors.setdefault("会话摘要", row["status"])
        with self.lock:
            self.retirement_supported = True
            self.retirements = rows
            self.threads = {tid: row for tid, row in self.threads.items() if tid not in rows}
        return rows, pending

    def _finish_retirements(self, store, rows, pending, projects, errors, stop):
        if not pending or stop.is_set():
            return
        mirror = Mirror(self.local_state, store, self.poll_cache)
        try:
            for tid, directories in pending.items():
                if stop.is_set():
                    break
                try:
                    purge_session_copies(mirror, self.home, projects, tid, part_directories=directories)
                    device = store.client.device
                    key = RETIREMENT_ACK_PREFIX + tid + "/" + device
                    receipt = pack({"v": 1})
                    try:
                        store.put(key, EMPTY, receipt)
                    except Changed:
                        if store.read(key) != receipt:
                            raise
                    with self.lock:
                        row = rows[tid]
                        row.update(acknowledged=sorted({*row["acknowledged"], device}), status="本机已处理")
                except (OSError, ValueError, sqlite3.Error) as error:
                    status = str(error) if isinstance(error, ValueError) else "本机同步副本暂不可清理，后台将重试"
                    with self.lock:
                        rows[tid]["status"] = status
                    errors.setdefault("会话摘要", status)
        finally:
            mirror.db.close()

    def reconfigure(self, settings, coordination):
        with self.lock:
            self.pending_settings = settings, coordination
        self.wake.set()

    def task_locks(self):
        with self.lock:
            settings, coordination = self.settings, self.coordination
        if coordination and not settings.get("exchange") and not settings.get("codeOnly"):
            return PersonalTaskLocks(ControlClient(coordination, device_token(coordination)))
        return None

    def watch_task_owners(self, stop):
        from ide_activity import close_queue, flush_closes

        outbox = close_queue(self.root)
        while not stop.is_set():
            try:
                with self.lock:
                    settings, coordination = self.settings, self.coordination
                locks = self.task_locks()
                owners = None
                if locks:
                    try:
                        flush_closes(outbox, locks)
                    except sqlite3.Error:
                        pass
                    owners = {"device": locks.client.device, **locks.request()}
                with self.lock:
                    if not stop.is_set() and settings is self.settings and coordination is self.coordination:
                        self.task_owners = owners
            except (ControlDenied, OSError, ValueError, KeyError, TypeError):
                pass
            stop.wait(3)

    def code_backups(self):
        if self.coordination and not self.settings.get("exchange") and not self.settings.get("codeOnly"):
            from code_backups import CodeBackups
            return CodeBackups(ControlClient(self.coordination, device_token(self.coordination)))
        return None

    def session_digest(self, tid):
        view = self.snapshot()
        session = view["sessions"].get(tid)
        if session and view["status"].get(session["project"]) == "已同步":
            data = read_transcript(Path(session["path"]))
            return digest(data) if data is not None else None
        return None

    def publish_handoff(self, tid):
        epoch = self.snapshot()["epoch"]
        self.request(None)
        deadline = time.monotonic() + 24
        while time.monotonic() < deadline:
            view = self.snapshot()
            session = view["sessions"].get(tid)
            if view["epoch"] > epoch and session and view["status"].get(session["project"]) == "已同步":
                try:
                    store = self.store()
                    checkpoint = confirm_published_session(store, session, tid) if store else None
                    if checkpoint:
                        return checkpoint
                except (OSError, ValueError, KeyError):
                    pass
            time.sleep(.25)
        raise ControlDenied(detail="最新代码和会话尚未同步，任务仍由本机持有。同步成功后可以重试交还。")

    def handoff_checkpoints(self, threads, *, handoff=None, continuing=lambda: True):
        view = self.snapshot()
        store = self.store()
        index = store.index() if store is not None else {}
        receipts, errors = {}, {}
        for tid in threads:
            if not continuing():
                break
            session = view["sessions"].get(tid)
            if not session:
                errors[tid] = f"任务 {tid[:8]} 在本机没有可核验的已完成会话。"
                continue
            project = session["project"]
            if view["status"].get(project) != "已同步":
                errors[tid] = f"{project} 的最新代码和会话等待同步。"
                continue
            try:
                with native_publication_lock(self.home, tid):
                    checkpoint = confirm_published_session(store, session, tid, index=index) if store else None
                    if checkpoint and handoff:
                        handoff(tid, project, checkpoint)
            except ControlDenied as error:
                errors[tid] = error.detail or "服务器尚未确认交还。"
                continue
            except ImportError:
                errors[tid] = f"任务 {tid[:8]} 的本机原生写入权尚未核对。"
                continue
            except (OSError, ValueError, KeyError):
                errors[tid] = f"任务 {tid[:8]} 的本机历史正在写入或暂时无法核对。"
                continue
            if checkpoint:
                receipts[tid] = project, checkpoint
            else:
                errors[tid] = f"任务 {tid[:8]} 的最新已完成会话等待服务器确认。"
        return receipts, errors

    def request(self, project):
        with self.lock:
            self.priority = project
            epoch = self.completed.get(project, 0)
        self.wake.set()
        return epoch

    def request_manual_sync(self, *, fresh=False):
        with self.lock:
            if self._pending_sync is not None:
                return self._pending_sync
            if self._active_sync is not None and not fresh:
                return self._active_sync
            self._pending_sync = Future()
            self.wake.set()
            return self._pending_sync

    def sync_progress(self):
        with self.lock:
            if self._active_sync is None:
                return None
            return (self._sync_phase, int(time.monotonic() - self._sync_started),
                    self._sync_finished, self._sync_total, dict(self._sync_projects))

    def _phase(self, label):
        with self.lock:
            self._sync_phase = label

    def resolve(self, tid, directory=None, remote=None):
        with self.lock:
            paths = {**self.projects, **self.folders}
            thread = self.threads.get(tid, {})
            project = thread.get("project") or remote
            if project not in paths and directory:
                current = Path(directory)
                candidates = [
                    (len(Path(path).parts), p)
                    for p, path in paths.items()
                    if current.is_relative_to(path)
                ]
                project = max(candidates)[1] if candidates else None
            if project in paths:
                root = Path(paths[project])
                stable = next((key for key, folder in self.folders.items() if Path(folder) == root), project)
                return stable, root
        return None

    def bindings(self, store, index, cached, published, errors, untrusted=None):
        local = {}
        parent = configured_root(self.root)
        found = {}
        pending_git = set()
        self.code_folders.clear()
        if parent and parent.is_dir():
            from plain_git import complete, managed
            for path in discover(parent, {}):
                if not (path / ".git").exists():
                    if any((path / name).is_file() for name in (
                            "package.json", "pyproject.toml", "Cargo.toml", "go.mod", "CMakeLists.txt", "pom.xml",
                            "requirements.txt")) or any(path.glob("*.sln")) or any(path.glob("*.slnx")):
                        self.code_folders.add(path)
                    continue
                if managed(path):
                    self.code_folders.add(path)
                    if not complete(path):
                        pending_git.add(path)
                        continue
                try:
                    origin = run(path, "config", "--get", "remote.origin.url", check=False, timeout=5)
                    if origin.returncode:
                        # Git config can omit the local configuration of an untrusted
                        # repository without surfacing the ownership error itself.
                        run(path, "rev-parse", "--show-toplevel", timeout=5)
                        continue
                    remote = origin.stdout.decode().strip()
                except UnsafeRepository:
                    if untrusted is not None:
                        untrusted.add(path)
                    continue
                except (OSError, SyncIssue, subprocess.TimeoutExpired):
                    continue
                identity = github_id(remote)
                if identity:
                    found.setdefault(identity, []).append({"path": str(path), "remote": remote})
        discovered = [rows[0] for rows in found.values() if len(rows) == 1]
        entries = (
            json.loads(self.registry.read_text(encoding="utf-8-sig")).get("projects", [])
            if self.registry.is_file()
            else []
        )
        entries = [*discovered, *entries, *self.settings.get("projects", [])]
        remote_specs = {} if store else cached.copy()
        if store:
            for key, revision in index.items():
                project = key.removeprefix("repo/")
                if project == key or revision == EMPTY:
                    continue
                record = cached.get(project)
                if record is None or digest(pack(record)) != revision:
                    data = store.read(key)
                    if digest(data) != revision:
                        raise SyncIssue("项目登记正在更新")
                    record = json.loads(data)
                if github_id(record.get("remote")) == project:
                    remote_specs[project] = record
        overrides = {}
        for entry in entries:
            project = github_id(entry.get("remote"))
            if not project:
                continue
            raw = entry.get("path")
            if isinstance(raw, (str, os.PathLike)) and Path(raw) in pending_git:
                continue
            remote_specs.setdefault(project, {"remote": entry["remote"], "memoryPath": ".codex/memories"})
            path = entry.get("path")
            if path:
                if not isinstance(path, (str, os.PathLike)) or not Path(path).is_absolute():
                    errors[project] = "项目映射需要本机绝对路径"
                    continue
                target = Path(path)
                if target.exists() or project not in local:
                    local[project] = target
            overrides.setdefault(project, {}).update(
                {k: entry[k] for k in ("branch", "memoryPath") if k in entry}
            )
        for project, spec in remote_specs.items():
            if project in local or project in errors:
                continue
            legacy = self.checkout.joinpath(*project.split("/"))
            if len(found.get(project, [])) > 1:
                errors[project] = "本机有多份相同仓库，请在个人同步配置中选择项目路径"
            elif (legacy / ".git").exists():
                local[project] = legacy
            elif parent and not parent.is_dir():
                errors[project] = "本机项目母目录暂不可用"
            else:
                relative = spec.get("relativePath", project)
                try:
                    if not isinstance(relative, str):
                        raise ValueError
                    base = parent or self.checkout
                    dest = safe_path(base, relative)
                    if dest in pending_git:
                        raise ValueError
                    if any((d / ".git").exists() for d in dest.parents
                           if d.is_relative_to(base) and (d != parent or d in local.values())):
                        raise ValueError
                    local[project] = dest
                except ValueError:
                    errors[project] = "项目相对目录不可用，请核对母目录和仓库映射"
        for project, fields in overrides.items():
            if project not in local or project in errors:
                continue
            spec = remote_specs[project].copy()
            target = local[project]
            try:
                if parent and target != parent and target.is_relative_to(parent) and (target / ".git").exists():
                    relative = target.relative_to(parent).as_posix()
                    safe_path(parent, relative)
                    spec.setdefault("relativePath", relative)
                for key, val in fields.items():
                    if project not in cached or published.get(project, {}).get(key) != val:
                        spec[key] = val
                subdir = spec["memoryPath"]
                if not isinstance(subdir, str):
                    raise ValueError("项目记忆目录无效")
                safe_path(target, subdir)
            except ValueError as error:
                errors[project] = str(error)
                continue
            if store:
                key = "repo/" + project
                data = pack(spec)
                if digest(data) != index.get(key, EMPTY):
                    store.put(key, index.get(key, EMPTY), data)
                published[project] = fields
            remote_specs[project] = spec
        owners = {}
        for project, path in local.items():
            owners.setdefault(path, []).append(project)
        for path, projects in owners.items():
            if len(projects) > 1 or any(folder in owners and not folder.exists() for folder in path.parents):
                for project in projects:
                    errors[project] = "项目目标目录与另一仓库重叠，请核对项目映射"
        return local, remote_specs

    def cycle(self, stop):
        with self.lock:
            if self.pending_settings:
                self.settings, self.coordination = self.pending_settings
                self.pending_settings = None
                self.task_owners = None
        errors = {}
        untrusted = set()
        channel = (
            ""
            if self.settings.get("codeOnly")
            else self.settings.get("exchange") or (self.coordination or {}).get("url", "")
        )
        scope = hashlib.sha256(
            pack([str(channel), str(self.home), (self.coordination or {}).get("device")])
        ).hexdigest()[:24]
        if self.scope != scope:
            self.scope = scope
            self.poll_cache.clear()
            self.local_state = self.root / ".local" / "personal-sync" / scope
            with self.lock:
                self.retirements = {}
                self.retirement_supported = False
        self.local_state.mkdir(parents=True, exist_ok=True)
        catalog = self.local_state / "catalog.json"
        preferences = self.local_state / "published.json"
        cached = json.loads(catalog.read_bytes()) if catalog.is_file() else {}
        published = json.loads(preferences.read_bytes()) if preferences.is_file() else {}
        store, index = None, {}
        cloud = self.coordination and not self.settings.get("exchange") and not self.settings.get("codeOnly")
        connection = None
        try:
            store = self.store()
            index = store.index() if store else {}
            self._phase("正在扫描本机仓库并核对项目映射")
            local, specs = self.bindings(store, index, cached, published, errors, untrusted)
            if cloud:
                connection = "已连接协调服务"
        except (OSError, ValueError, sqlite3.Error):
            errors["配对"] = "协调服务暂不可用，后台将重试" if self.coordination else "个人同步通道暂不可用"
            if cloud:
                connection = "协调服务暂不可用，后台将重试"
            store = None
            local, specs = self.bindings(None, {}, cached, published, errors, untrusted)
        retirements, deletion_pending = self._prepare_retirements(store, index, errors, stop)
        if connection == "已连接协调服务":
            self._phase("正在同步随记")
            try:
                from notebook_store import sync_notebook
                changed, notebook_errors = sync_notebook(self.root, store, index, stop)
                if changed:
                    with self.lock:
                        self.notebook_revision += 1
                if notebook_errors:
                    errors["吉光片羽"] = notebook_errors[0]
            except (ValueError, OSError, sqlite3.Error):
                errors["吉光片羽"] = "随记暂不可同步，后台将重试"
        if store:
            for file, data in ((catalog, pack(specs)), (preferences, pack(published))):
                if not file.is_file() or file.read_bytes() != data:
                    atomic_write(file, data)
        with self.lock:
            self.projects = {p: str(path) for p, path in local.items()}
            first = self.priority
            self.priority = None
        ready = {}
        backups = self.code_backups() if isinstance(store, RemoteRecords) else None
        backup_status = {project: "等待服务器或本机项目可用" for project in specs} if cloud else {}

        def update(project):
            try:
                path, spec = local[project], specs[project]
                subdir = spec["memoryPath"]
                memory = safe_path(path, subdir)
                if not memory.is_relative_to(path) or subdir.split("/")[0].lower() in (
                    ".git",
                    ".ssh",
                    ".aws",
                ):
                    raise SyncIssue("项目记忆目录无效")
                if store and (path / ".git").exists() and path.resolve() == path:
                    verify_checkout(path, project)
                    exclude_memory(path, subdir)
                    ready[project] = (path, subdir)
                relative = spec.get("relativePath")
                sync_project(path, spec["remote"], spec.get("branch"), memory_subdir=subdir,
                             placeholder_id=folder_id(relative) if relative else None)
                ready[project] = (path, subdir)
                errors[project] = "已同步"
            except UnsafeRepository as error:
                if (path / ".git").exists():
                    with self.lock:
                        untrusted.add(path)
                errors[project] = str(error)
            except (
                ValueError,
                OSError,
                KeyError,
                TypeError,
                sqlite3.Error,
                subprocess.TimeoutExpired,
            ) as error:
                errors[project] = str(error) if isinstance(error, ValueError) else "本机目录或网络暂不可用"

        jobs = queue.Queue()
        for project in sorted(local, key=lambda p: p != first):
            if project not in errors:
                jobs.put(project)
        with self.lock:
            self._sync_phase = "正在检查 GitHub 与服务器代码备份"
            self._sync_total = jobs.qsize()

        def consume():
            while not stop.is_set():
                try:
                    project = jobs.get_nowait()
                except queue.Empty:
                    return
                try:
                    with self.lock:
                        self._sync_projects[project] = "GitHub"
                    update(project)
                    if backups is not None and not stop.is_set():
                        with self.lock:
                            self._sync_projects[project] = "服务器备份"
                        path = local[project]
                        if not (path / ".git").exists():
                            backup_status[project] = "本机没有项目，可查找已有服务器备份"
                            continue
                        backup_status[project] = backups.sync(path, project, specs[project]["memoryPath"], stop)
                except UnsafeRepository as error:
                    with self.lock:
                        untrusted.add(local[project])
                    backup_status[project] = str(error)
                except (OSError, KeyError, TypeError, ValueError, subprocess.TimeoutExpired) as error:
                    backup_status[project] = str(error) if isinstance(error, ValueError) else "服务器或本机目录暂不可用"
                finally:
                    with self.lock:
                        self._sync_projects.pop(project, None)
                        self._sync_finished += 1

        workers = [threading.Thread(target=consume, daemon=True) for _ in range(min(4, len(specs)))]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        check_memory = store is not None and self.settings.get("memory", True) and not stop.is_set()
        self._phase("正在同步普通项目映射、会话和记忆")
        folders, sources = {}, {}
        if (check_memory or isinstance(store, RemoteRecords)) and not stop.is_set():
            device = store.client.device if isinstance(store, RemoteRecords) else self.scope
            folders, sources = folder_bindings(configured_root(self.root), self.home, store, index,
                                               self.poll_cache, local, self.code_folders, device, errors, stop)
            if not check_memory:
                for project, root in folders.items():
                    if root.is_dir():
                        marker = folder_index(root, project, source=project in sources)
                        if not marker.is_file():
                            atomic_write(marker, pack({}))
        if deletion_pending:
            retirement_projects = {project: (path, specs[project]["memoryPath"])
                                   for project, path in local.items() if project in specs}
            retirement_projects.update({project: (root, ".lanxi") for project, root in folders.items()})
            self._finish_retirements(store, retirements, deletion_pending, retirement_projects, errors, stop)
        if check_memory:

            def report(key, error):
                errors.setdefault(
                    "记忆", key + " · " + (str(error) if isinstance(error, ValueError) else "暂不可读写")
                )

            mirror = Mirror(self.local_state, store, self.poll_cache)
            try:
                # Keep the same session namespace when a folder gains a private Git origin.
                aliased = set(folders.values())
                session_roots = {project: row for project, row in ready.items() if row[0] not in aliased}
                session_roots.update({project: (root, ".lanxi") for project, root in folders.items()})
                sessions = sync_memories(mirror, index, self.home, session_roots, self.poll_cache, report, stop,
                                         git_projects=ready, retired=retirements)
                for project, (root, _) in session_roots.items():
                    records = {
                        tid: {"cwd": s["relative"], "updated": s["updated"]}
                        for tid, s in sessions.items()
                        if s["project"] == project
                    }
                    if project in folders:
                        if not records and not root.is_dir():
                            continue
                        marker = folder_index(root, project, source=project in sources)
                    else:
                        marker = Path(value(root, "rev-parse", "--absolute-git-dir")) / "lanxi-personal-sessions.json"
                    data = pack(records)
                    if not stop.is_set() and (not marker.is_file() or marker.read_bytes() != data):
                        atomic_write(marker, data)
                with self.lock:
                    self.threads = sessions
            finally:
                mirror.db.close()
        folder_git = {}
        if isinstance(store, RemoteRecords):
            from plain_git import promote
            synchronized = {local[repo] for repo in ready if errors.get(repo) == "已同步"}
            for project, directory in sorted(sources.items()):
                if stop.is_set():
                    break
                if directory in synchronized:
                    folder_git[project] = "已接入 GitHub"
                    self.git_retries.pop((self.scope, project), None)
                    continue
                retry, last = self.git_retries.get((self.scope, project), (0, ""))
                if time.monotonic() < retry:
                    folder_git[project] = last
                    continue
                try:
                    self._phase(f"正在为 {directory.name} 接入 GitHub")
                    folder_git[project] = promote(directory)
                    self.git_retries.pop((self.scope, project), None)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    message = str(error) if isinstance(error, SyncIssue) else "自动 Git 接入暂未完成，稍后重试"
                    self.git_retries[self.scope, project] = time.monotonic() + 60, message
                    folder_git[project] = message
        inventory = None
        if isinstance(store, RemoteRecords) and not stop.is_set():
            from large_inventory import sync_inventory
            self._phase("正在核对大文件清单")
            try:
                available, inventory_errors = sync_inventory(self.root, store, index, ready, stop, include_available=True)
                inventory = {project: {name: row for name, row in files.items()
                                       if row["localSize"] not in row["copies"]}
                             for project, files in available.items()}
                inventory = {project: rows for project, rows in inventory.items() if rows}
                if inventory_errors:
                    errors["大文件"] = next(iter(inventory_errors.values()))
                with self.lock:
                    self.large_inventory = {**{key: value for key, value in self.large_inventory.items()
                                                if key in inventory_errors or key not in ready}, **inventory}
                    self.large_available = available
            except (ValueError, OSError, TypeError):
                errors["大文件"] = "大文件清单暂不可用，后台将重试"
        with self.lock:
            self.folders = {project: str(root) for project, root in folders.items()}
            # This status gates session handoff. Per-thread transfer failures omit
            # that thread from self.threads; code/backup counters remain Git-only.
            for project in folders:
                errors.setdefault(project, "已同步")
            for project in (*specs, *folders):
                self.completed[project] = self.completed.get(project, 0) + 1
            self.epoch += 1
            self.status = errors
            self.backup_status = backup_status
            self.connection = connection
        return {"connection": connection, "projects": len(specs), "memoryChecked": check_memory,
                "untrustedGit": sorted(map(str, untrusted), key=str.casefold),
                "largeInventory": inventory, "status": errors,
                "codeBackups": backup_status,
                "folderProjects": dict(self.folders),
                "folderGit": folder_git,
                "projectPaths": {p: str(local[p]) if p in local else "" for p in specs}}

    def worker(self, stop):
        interval = max(2, min(300, int(self.settings.get("intervalSeconds", 10))))
        while not stop.is_set():
            with self.lock:
                self.wake.clear()
                current, self._pending_sync = self._pending_sync, None
                if current is None or not current.set_running_or_notify_cancel():
                    current = Future()
                    current.set_running_or_notify_cancel()
                self._active_sync = current
                self._sync_started = time.monotonic()
                self._sync_phase = "正在连接个人工作区"
                self._sync_total = self._sync_finished = 0
                self._sync_projects.clear()
            result = None
            try:
                result = self.cycle(stop)
            except (OSError, ValueError, sqlite3.Error, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired):
                with self.lock:
                    self.epoch += 1
                    self.status = {"配对": "个人同步暂不可用，请检查项目登记和配对连接"}
                    if self.connection:
                        self.connection = self.status["配对"]
            finally:
                with self.lock:
                    self._active_sync = None
                current.set_result(None if stop.is_set() else result)
            for _ in range(interval * 4):
                if stop.wait(0.25) or self.wake.is_set():
                    break
        with self.lock:
            pending, self._pending_sync = self._pending_sync, None
        if pending is not None:
            pending.cancel()
