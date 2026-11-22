"""Project-relative Codex notes and closed rollouts; global preferences stay in CODEX_HOME."""

import json
import ntpath
import os
import re
import sqlite3
import stat
import tempfile
import uuid
from pathlib import Path, PureWindowsPath

from handoff import (
    MAX_FILE,
    native_publication_lock,
    session_info,
    session_location,
    session_progress,
)
from personal_relay import EMPTY, MAX_RECORD, Changed, digest
from session_chunks import MANIFESTS, MAX_ENVELOPE, manifest_key, read_manifest, sync_large


def windows_reserved(name):
    if hasattr(ntpath, "isreserved"):
        return ntpath.isreserved(name)
    return PureWindowsPath(name).is_reserved()


def safe_path(root, relative):
    path = Path(root)
    for part in relative.split("/"):
        if (
            not part
            or part in (".", "..")
            or part.endswith((" ", "."))
            or windows_reserved(part)
            or re.search(r'[\x00-\x1f\\:*?"<>|]', part)
            or part.lower() == ".git"
        ):
            raise ValueError("个人记忆相对路径无效")
        path /= part
        if (
            path.is_symlink()
            or path.exists()
            and getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("个人记忆路径包含链接")
    return path


def read(path, *, limit=MAX_RECORD - 1):
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("文件过大或不是普通文件")
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if size > limit:
            raise ValueError("文件超过同步上限")
        data = stream.read(size + 1)
    if len(data) != size:
        raise Changed("读取时文件大小发生变化")
    return data


def replace(path, expected, data, backups, *, limit=MAX_RECORD - 1):
    """Keep the exact old content and never overwrite a second editor's new path."""
    if read(path, limit=limit) != expected:
        raise Changed("本地文件刚刚变化")
    path.parent.mkdir(parents=True, exist_ok=True)
    if expected is not None:
        backups.mkdir(parents=True, exist_ok=True)
        backup = backups / digest(expected)
        try:
            with backup.open("xb") as stream:
                stream.write(expected)
        except FileExistsError:
            if read(backup, limit=limit) != expected:
                raise Changed("本地备份不完整")
    with tempfile.TemporaryDirectory(dir=path.parent, prefix=".lanxi-write-") as scratch:
        old = Path(scratch) / "old"
        staged = Path(scratch) / "new"
        if data is not None:
            staged.write_bytes(data)
        if expected is not None:
            path.rename(old)
        try:
            if expected is not None and read(old, limit=limit) != expected:
                raise Changed("保留了写入期间变化的本地文件")
            if data is not None:
                os.link(staged, path)
        except (OSError, ValueError):
            if old.exists():
                changed = path.with_name(".lanxi-conflict-" + uuid.uuid4().hex)
                old.rename(changed if path.exists() else path)
            raise


def pack(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class Mirror:
    def __init__(self, state, store, cache=None):
        state.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(state / "memory-sync.db")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS local_file(key TEXT,path TEXT,hash TEXT NOT NULL,PRIMARY KEY(key,path))"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS native_memory(key TEXT PRIMARY KEY,hash TEXT NOT NULL)")
        self.backups = state / "memory-backups"
        self.store = store
        self.cache = cache if cache is not None else {}

    def sync(self, key, path, index, *, rollout=False):
        rh = index.get(key, EMPTY)
        info = path.stat() if path.exists() else None
        stamp = (info.st_ino, info.st_size, info.st_mtime_ns) if info else None
        if self.cache.get((key, path)) == (stamp, rh):
            return rh
        local = read(path)
        lh = digest(local)
        row = self.db.execute(
            "SELECT hash FROM local_file WHERE key=? AND path=?", (key, str(path))
        ).fetchone()
        base = row[0] if row else EMPTY
        if lh != rh:
            incoming = None
            if rh != EMPTY and (rollout or lh == base):
                incoming = self.store.read(key)
                if digest(incoming) != rh:
                    raise Changed("远端版本已变化")
            if rollout and local and incoming:
                # The payload starts with a stable mapping header and continues with native JSONL.
                if local.startswith(incoming):
                    self.store.put(key, rh, local)
                elif incoming.startswith(local):
                    replace(path, local, incoming, self.backups)
                    lh = rh
                else:
                    raise Changed("会话发生分叉，两端版本均已保留")
            elif lh == base and (row or local is None):
                if rollout and incoming is None:
                    raise Changed("保留已有会话")
                replace(path, local, incoming, self.backups)
                lh = rh
            elif rh == base and not (rollout and local is None):
                self.store.put(key, rh, local)
            else:
                raise Changed("同一条记忆在两端都已修改")
        if base != lh or row is None:
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO local_file VALUES(?,?,?)", (key, str(path), lh))
        if digest(local) == lh:
            self.cache[(key, path)] = stamp, lh
        return lh


def notes(root):
    names = set()
    for folder, dirs, files in os.walk(root):
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".")
            and not os.path.islink(Path(folder) / d)
            and not getattr((Path(folder) / d).lstat(), "st_file_attributes", 0)
            & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ]
        for name in files:
            if not name.startswith(".") and Path(name).suffix.lower() in NOTE_TYPES:
                names.add((Path(folder) / name).relative_to(root).as_posix())
    return names


MEMORY_FIELDS = (
    "thread_id",
    "source_updated_at",
    "raw_memory",
    "rollout_summary",
    "rollout_slug",
    "generated_at",
)
NOTE_TYPES = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml"}


def native_row(db, tid):
    row = db.execute(
        "SELECT " + ",".join(MEMORY_FIELDS) + " FROM stage1_outputs WHERE thread_id=?", (tid,)
    ).fetchone()
    return dict(zip(MEMORY_FIELDS, row)) if row else None


def sync_native_row(mirror, db, key, path, tid, index):
    if db is None:
        mirror.sync(key, path, index)
        return
    seen = mirror.db.execute("SELECT hash FROM native_memory WHERE key=?", (key,)).fetchone()
    current = native_row(db, tid)
    payload = pack(current) if current else None
    local = digest(payload)
    data = read(path)
    if seen is None and current and data and data != payload:
        raise Changed("项目副本与本机已有长期记忆不同，已保留双方")
    if (current if seen is None else local != seen[0]) and payload != data:
        if data and seen and digest(data) != seen[0]:
            raise Changed("项目副本与原生长期记忆同时变化")
        if payload is not None and len(payload) >= MAX_RECORD:
            raise ValueError("长期记忆超过上限")
        replace(path, data, payload, mirror.backups)
    revision = mirror.sync(key, path, index)
    data = read(path)
    if digest(data) != revision:
        raise Changed("长期记忆刚刚更新")
    other = json.loads(data) if data else None
    if other is not None and (
        not isinstance(other, dict)
        or set(other) != set(MEMORY_FIELDS)
        or other["thread_id"] != tid
        or type(other["source_updated_at"]) is not int
        or not isinstance(other["raw_memory"], str)
        or not isinstance(other["rollout_summary"], str)
    ):
        raise ValueError("长期记忆记录无效")
    with db:
        db.execute("BEGIN IMMEDIATE")
        if native_row(db, tid) != current:
            raise Changed("Codex 刚刚更新了长期记忆")
        if other != current and other:
            fields = ",".join(MEMORY_FIELDS)
            update = ",".join(k + "=excluded." + k for k in MEMORY_FIELDS[1:])
            db.execute(
                f"INSERT INTO stage1_outputs({fields}) VALUES(?,?,?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET "
                + update,
                [other[k] for k in MEMORY_FIELDS],
            )
        elif current and other is None:
            db.execute("DELETE FROM stage1_outputs WHERE thread_id=?", (tid,))
    if seen is None or seen[0] != revision:
        with mirror.db:
            mirror.db.execute("INSERT OR REPLACE INTO native_memory VALUES(?,?)", (key, revision))


def read_transcript(path):
    return read(path, limit=MAX_FILE)


def read_envelope(path):
    return read(path, limit=MAX_ENVELOPE)


def replace_envelope(path, expected, data, backups):
    replace(path, expected, data, backups, limit=MAX_ENVELOPE)


def confirm_published_session(store, session, tid, *, index=None):
    native = Path(session["path"])
    key = f"sessions/{session['project']}/{native.name}"
    if index is None:
        index = store.index()
    data = read_transcript(native)
    if not data or session_info(data)[0] != tid:
        return None
    envelope = pack({"name": native.name, "cwd": session["relative"]}) + b"\n" + data
    tall = manifest_key(key)
    if index.get(tall, EMPTY) != EMPTY:
        spec = read_manifest(store, tall, index[tall])
        if len(envelope) == spec["size"] and digest(envelope) == spec["sha256"]:
            return digest(data)
    elif index.get(key) == digest(envelope):
        return digest(data)
    return None


def split_session(payload, filename, tid):
    header, transcript = payload.split(b"\n", 1)
    meta = json.loads(header)
    if len(transcript) > MAX_FILE or meta.get("name") != filename or session_info(transcript)[0] != tid:
        raise ValueError("会话映射与内容不一致")
    return meta["cwd"], transcript


def install_history(home, filename, tid, data, backups):
    home.mkdir(parents=True, exist_ok=True)
    state, path, prior = session_progress(home, tid, data, distinguish_ahead=True)
    if state in ("ahead", "current"):
        return
    with native_publication_lock(home, tid):
        state, path, prior = session_progress(home, tid, data, distinguish_ahead=True)
        if state == "missing":
            path = safe_path(home, session_location(filename, tid).as_posix())
            replace(path, None, data, backups, limit=MAX_FILE)
        elif state == "advance":
            replace(path, prior, data, backups, limit=MAX_FILE)


def _saved_session_id(path):
    with path.open("rb") as stream:
        head = json.loads(stream.readline(MAX_RECORD))
        if not isinstance(head, dict):
            return None
        if set(head) == {"name", "cwd"}:
            head = json.loads(stream.readline(262144))
        if isinstance(head, dict) and head.get("type") == "session_meta":
            payload = head.get("payload")
            return payload.get("id") if isinstance(payload, dict) else None
        return head.get("thread_id") if isinstance(head, dict) and set(head) == set(MEMORY_FIELDS) else None


def purge_session_copies(mirror, home, projects, tid, *, source_keys=()):
    """Clear app-managed replicas and recognizable backups for one retired UUID."""
    if str(uuid.UUID(tid)) != tid:
        raise ValueError("会话 ID 无效")
    suffix = f"-{tid}.jsonl"
    part_keys = set(source_keys)
    for project, (root, subdir) in projects.items():
        if not root.is_dir():
            continue
        root = safe_path(root, subdir)
        folder = safe_path(root, ".lanxi/sessions")
        for entry in folder.glob(f"rollout-*{suffix}"):
            session_location(entry.name, tid)
            path = safe_path(folder, entry.name)
            if _saved_session_id(path) != tid:
                raise ValueError("本机会话副本标识不匹配")
            part_keys.add(MANIFESTS + project + "/" + entry.name)
            path.unlink()
        row = safe_path(root, f".lanxi/long-term/{tid}.json")
        if row.exists():
            if _saved_session_id(row) != tid:
                raise ValueError("本机长期记忆标识不匹配")
            row.unlink()

    for key, in mirror.db.execute("SELECT key FROM local_file WHERE key LIKE ?", (f"sessions/%{suffix}",)):
        part_keys.add(manifest_key(key))
    for key in part_keys:
        scratch = safe_path(mirror.backups.parent, "parts/" + digest(key.encode())[:16])
        for path in scratch.iterdir() if scratch.is_dir() else ():
            if re.fullmatch(r"[a-f0-9]{24}", path.name):
                part = safe_path(scratch, path.name)
                if part.is_file():
                    part.unlink()

    native_backups = safe_path(home, f"lanxi-handoff-backups/{tid}")
    backup_roots = ((mirror.backups, r"[a-f0-9]{64}"), (native_backups, r"[a-f0-9]{64}\.jsonl"))
    for folder, pattern in backup_roots:
        if folder.is_symlink():
            raise ValueError("本机会话备份路径包含链接")
        for path in folder.iterdir() if folder.is_dir() else ():
            if not re.fullmatch(pattern, path.name):
                continue
            path = safe_path(folder, path.name)
            if not path.is_file():
                continue
            try:
                owner = _saved_session_id(path)
            except ValueError:
                continue  # This shared backup directory also contains unrelated notes.
            if owner == tid:
                path.unlink()

    memdb = safe_path(home, "memories_1.sqlite")
    if memdb.is_file():
        with closing(sqlite3.connect(memdb, timeout=1)) as db, db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stage1_outputs'").fetchone():
                db.execute("DELETE FROM stage1_outputs WHERE thread_id=?", (tid,))
    with mirror.db:
        mirror.db.execute("DELETE FROM local_file WHERE key LIKE ? OR key LIKE ?",
                          (f"sessions/%{suffix}", f"long-term/%/{tid}"))
        mirror.db.execute("DELETE FROM native_memory WHERE key LIKE ?", (f"long-term/%/{tid}",))


def sync_memories(mirror, index, home, projects, poll_cache, report, stop, *, git_projects=None, retired=()):
    """Keep native sessions under their stable identity and notes under their Git repository."""

    def attempt(key, fn, *args):
        try:
            return fn(*args)
        except (OSError, ValueError, KeyError, IndexError, TypeError, sqlite3.Error) as error:
            report(key, error)
            return None

    def sync_notes(prefix, root):
        local = notes(root)
        names = local | {k[len(prefix) :] for k in index if k.startswith(prefix)}
        for name in sorted(names):
            if stop.is_set():
                return
            if any(s.startswith(".") for s in name.split("/")) or Path(name).suffix.lower() not in NOTE_TYPES:
                continue
            attempt(
                prefix + name, lambda name: mirror.sync(prefix + name, safe_path(root, name), index), name
            )

    attempt("global/AGENTS.md", lambda: mirror.sync("global/AGENTS.md", safe_path(home, "AGENTS.md"), index))
    sync_notes("global/notes/", safe_path(home, "memories"))
    roots = {}
    thread_projects = {}
    for project, (root, subdir) in projects.items():
        roots[project] = safe_path(root, subdir)
    if git_projects is None:
        git_projects = {key: row for key, row in projects.items() if not key.startswith(".folder/")}
    for project, (root, subdir) in git_projects.items():
        sync_notes("notes/" + project + "/", safe_path(root, subdir))
    # Cache unchanged native files; never move an active writer's live database or WAL.
    for path in (home / "sessions").rglob("rollout-*.jsonl"):
        if stop.is_set():
            return {}
        tid = path.stem[-36:]
        stamp = None
        try:
            if str(uuid.UUID(tid)) != tid or tid in retired:
                continue
            path = safe_path(home, path.relative_to(home).as_posix())
            statinfo = path.stat()
            stamp = statinfo.st_mtime_ns, statinfo.st_size
            association = poll_cache.get(("project", path))
            if association is None:
                with path.open("rb") as source:
                    meta = json.loads(source.readline(262144))["payload"]
                if meta.get("id") == tid and meta.get("cwd"):
                    source_root = Path(meta["cwd"])
                    association = next(
                        (
                            p
                            for p, (r, _) in sorted(projects.items(), key=lambda x: -len(x[1][0].parts))
                            if source_root.is_relative_to(r)
                        ),
                        None,
                    )
                    if association:
                        poll_cache["project", path] = association
            if association:
                thread_projects[tid] = association
            elif not any((dest / ".lanxi" / "sessions" / path.name).is_file() for dest in roots.values()):
                continue
            cached = poll_cache.get(path)
            if cached == stamp:
                continue
            if statinfo.st_size > MAX_FILE:
                report("session/" + tid, ValueError("原生会话超过 64 MiB 同步上限"))
                continue
            data = read_transcript(path)
            ident, _, cwd = session_info(data)
            if ident != tid:
                continue
            mapped = next(
                (
                    (p, Path(cwd).relative_to(root).as_posix())
                    for p, (root, _) in sorted(projects.items(), key=lambda x: -len(x[1][0].parts))
                    if Path(cwd).is_relative_to(root)
                ),
                None,
            )
            # Imported rollouts retain the original paths. Reuse the stable mapping from their envelope.
            if mapped is None:
                for project, dest in roots.items():
                    envelope = safe_path(dest, ".lanxi/sessions/" + path.name)
                    if envelope.is_file():
                        with envelope.open("rb") as stored:
                            header = json.loads(stored.readline(64 * 1024))
                        mapped = project, header["cwd"]
                        break
            if mapped:
                project, relative = mapped
                envelope = safe_path(roots[project], ".lanxi/sessions/" + path.name)
                header = pack({"name": path.name, "cwd": relative}) + b"\n"
                old = read_envelope(envelope)
                new = header + data
                if old is None or new.startswith(old) and new != old:
                    replace_envelope(envelope, old, new, mirror.backups)
                elif not old.startswith(new):
                    raise Changed("项目副本和原生会话已分叉")
                poll_cache[path] = stamp
        except (ValueError, TypeError, IndexError, KeyError, OSError) as error:
            # Incomplete turns are revisited when the file changes.
            if isinstance(error, Changed):
                report("session/" + tid, error)
            if not isinstance(error, Changed) and stamp is not None:
                poll_cache[path] = stamp
    sessions = {}
    db_path = safe_path(home, "memories_1.sqlite")
    memdb = sqlite3.connect(db_path, timeout=1) if db_path.is_file() else None
    try:
        for project, dest in roots.items():
            prefix = "sessions/" + project + "/"
            folder = safe_path(dest, ".lanxi/sessions")
            manifests = MANIFESTS + project + "/"
            files = {f.name for f in folder.glob("rollout-*.jsonl")} | {
                k[len(prefix) :] for k in index if k.startswith(prefix)
            } | {k[len(manifests):] for k, rev in index.items() if k.startswith(manifests) and rev != EMPTY}
            for filename in sorted(files):
                if stop.is_set():
                    return sessions

                def apply(project, prefix, folder, filename):
                    tid = filename[-42:-6]
                    session_location(filename, tid)
                    if str(uuid.UUID(tid)) != tid:
                        raise ValueError("会话 ID 无效")
                    if tid in retired:
                        return
                    envelope = safe_path(folder, filename)
                    if (index.get(manifests + filename, EMPTY) != EMPTY
                            or envelope.is_file() and envelope.stat().st_size >= MAX_RECORD):
                        sync_large(mirror, index, prefix + filename, envelope, stop, read_envelope,
                                   replace_envelope, lambda raw: split_session(raw, filename, tid))
                    else:
                        mirror.sync(prefix + filename, envelope, index, rollout=True)
                    native = safe_path(home, session_location(filename, tid).as_posix())

                    def stamps():
                        return tuple(
                            (s.st_ino, s.st_size, s.st_mtime_ns) if p.is_file() and (s := p.stat()) else None
                            for p in (envelope, native)
                        )

                    cache_key = ("installed", envelope, native)
                    cached = poll_cache.get(cache_key)
                    if cached and cached[0] == stamps():
                        sessions[tid] = cached[1]
                        thread_projects[tid] = project
                        return
                    data = read_envelope(envelope)
                    if data is None:
                        return
                    rel, transcript = split_session(data, filename, tid)
                    root = projects[project][0]
                    cwd = root if rel == "." else safe_path(root, rel)
                    install_history(home, filename, tid, transcript, mirror.backups)
                    last = json.loads(transcript.rstrip().rsplit(b"\n", 1)[-1]).get("timestamp", "")
                    sessions[tid] = {
                        "project": project,
                        "cwd": str(cwd),
                        "relative": rel,
                        "updated": str(last),
                        "path": str(native),
                    }
                    poll_cache[cache_key] = stamps(), sessions[tid]
                    thread_projects[tid] = project

                attempt(prefix + filename, apply, project, prefix, folder, filename)
        session_owner = {root: project for project, (root, _) in projects.items()}
        for project, (root, subdir) in git_projects.items():
            dest = safe_path(root, subdir)
            row_prefix = "long-term/" + project + "/"
            known = {tid for tid, owner in thread_projects.items() if owner == session_owner.get(root)}
            known.update(k[len(row_prefix) :] for k in index if k.startswith(row_prefix))
            for tid in known.difference(retired):
                if stop.is_set():
                    return sessions

                def sync_row(dest, row_prefix, tid):
                    if str(uuid.UUID(tid)) != tid:
                        raise ValueError("长期记忆线程编号无效")
                    rowpath = safe_path(dest, ".lanxi/long-term/" + tid + ".json")
                    sync_native_row(mirror, memdb, row_prefix + tid, rowpath, tid, index)

                attempt(row_prefix + tid, sync_row, dest, row_prefix, tid)
    finally:
        if memdb:
            memdb.close()
    return sessions


def latest_session(cwd):
    from plain_projects import INDEX

    for root in (cwd, *cwd.parents):
        if (root / INDEX).is_file():
            marker = safe_path(root, INDEX)
        elif (root / ".git").exists():
            from code_sync import value
            marker = Path(value(root, "rev-parse", "--absolute-git-dir")) / "lanxi-personal-sessions.json"
        else:
            continue
        if marker.is_file():
            records = json.loads(marker.read_bytes())
            for tid, row in sorted(records.items(), key=lambda item: item[1]["updated"], reverse=True):
                if str(uuid.UUID(tid)) == tid:
                    location = root if row["cwd"] == "." else safe_path(root, row["cwd"])
                    if location.is_dir() and (cwd == root or cwd == location):
                        return tid, location
        return None
