"""Versioned private records shared only by the paired devices of one person."""

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

MAX_RECORD = 10 * 1024**2
EMPTY = "-"
RETIREMENT_PREFIX = "retirement/"
RETIREMENT_ACK_PREFIX = "retirement-acks/"
RETIREMENT_SOURCES_PREFIX = "retirement-sources/"
MAX_RETIREMENT_MARKDOWN = 64 * 1024
_MAX_RETIREMENT_JSON = 6 * MAX_RETIREMENT_MARKDOWN + 256
_THREAD_ID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_SHA256 = r"[0-9a-f]{64}"
_CHUNKS = "session-chunks/"
_MANIFESTS = "session-manifests/"


class Changed(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else EMPTY


def retirement_markdown(data):
    try:
        if len(data) > _MAX_RETIREMENT_JSON:
            raise ValueError
        marker = json.loads(data.decode("utf-8"))
        markdown = marker["markdown"]
        body = markdown.encode("utf-8")
        if (marker.keys() != {"v", "markdown", "sha256"} or type(marker["v"]) is not int
                or marker["v"] != 1 or not markdown.strip() or len(body) > MAX_RETIREMENT_MARKDOWN
                or marker["sha256"] != digest(body)):
            raise ValueError
        return markdown
    except (AttributeError, KeyError, RecursionError, TypeError, ValueError):
        raise ValueError("会话摘要格式或校验值无效") from None


def _session_thread(key):
    category, _, rest = key.partition("/")
    project, _, name = rest.rpartition("/")
    if not project:
        return None
    if category == "long-term":
        return name if re.fullmatch(_THREAD_ID, name) else None
    if category in ("sessions", "session-manifests"):
        match = re.fullmatch(r"rollout-.+-({})\.jsonl".format(_THREAD_ID), name)
        return match[1] if match else None
    return None


def _manifest_parts(data):
    try:
        parts = json.loads(data)["parts"]
    except (KeyError, RecursionError, TypeError, ValueError):
        return set()
    if not isinstance(parts, list):
        return set()
    return {part for part in parts if isinstance(part, str) and re.fullmatch(_SHA256, part)}


class Records:
    def __init__(self, path, space, device=None, *, check_task_owner=False):
        self.path, self.space, self.device = path, space, device
        self.check_task_owner = check_task_owner
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS personal_records(
                    space TEXT,key TEXT,hash TEXT,data BLOB,PRIMARY KEY(space,key)
                );
                CREATE TABLE IF NOT EXISTS personal_retired_parts(
                    space TEXT,sha TEXT,PRIMARY KEY(space,sha)
                );
                CREATE TABLE IF NOT EXISTS personal_session_parts(
                    space TEXT,thread TEXT,sha TEXT,PRIMARY KEY(space,thread,sha)
                );
            """)

    def index(self):
        with closing(sqlite3.connect(self.path)) as db:
            return dict(db.execute("SELECT key,hash FROM personal_records WHERE space=?", (self.space,)))

    def read(self, key):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT data FROM personal_records WHERE space=? AND key=?", (self.space, key)
            ).fetchone()
            return row[0] if row else None

    def _retired(self, db, thread):
        return db.execute("SELECT 1 FROM personal_records WHERE space=? AND key=?",
                          (self.space, RETIREMENT_PREFIX + thread)).fetchone() is not None

    def _retired_part(self, db, sha):
        return db.execute("SELECT 1 FROM personal_retired_parts WHERE space=? AND sha=?",
                          (self.space, sha)).fetchone() is not None

    def _record_parts(self, db, thread, parts):
        db.executemany("INSERT OR IGNORE INTO personal_session_parts VALUES(?,?,?)",
                       ((self.space, thread, sha) for sha in parts))

    def _purge_session(self, db, thread):
        if self.check_task_owner:
            owner = db.execute("SELECT device FROM personal_task_locks WHERE space=? AND session=?",
                               (self.space, thread)).fetchone()
            if owner and owner[0] is not None and owner[0] != self.device:
                raise Changed("另一台设备仍持有此会话，请先交还")
            db.execute("DELETE FROM personal_task_locks WHERE space=? AND session=?", (self.space, thread))
        names = [row[0] for row in db.execute(
            "SELECT key FROM personal_records WHERE space=? AND "
            "(key GLOB ? OR key GLOB ? OR key GLOB ?)",
            (self.space, "sessions/*-" + thread + ".jsonl",
             _MANIFESTS + "*-" + thread + ".jsonl", "long-term/*/" + thread),
        ) if _session_thread(row[0]) == thread]
        candidates = {row[0] for row in db.execute(
            "SELECT sha FROM personal_session_parts WHERE space=? AND thread=?", (self.space, thread)
        )}
        for name in names:
            if name.startswith(_MANIFESTS):
                raw = db.execute("SELECT data FROM personal_records WHERE space=? AND key=?",
                                 (self.space, name)).fetchone()[0]
                candidates.update(_manifest_parts(raw))
        part_directories = sorted({digest((_MANIFESTS + name.partition("/")[2]).encode())[:16]
                                   for name in names if name.startswith(("sessions/", _MANIFESTS))})
        source = json.dumps({"v": 1, "partDirectories": part_directories}, separators=(",", ":")).encode("ascii")
        db.execute("INSERT OR REPLACE INTO personal_records VALUES(?,?,?,?)",
                   (self.space, RETIREMENT_SOURCES_PREFIX + thread, digest(source), source))
        db.executemany("DELETE FROM personal_records WHERE space=? AND key=?", ((self.space, name) for name in names))
        db.execute("DELETE FROM personal_session_parts WHERE space=? AND thread=?", (self.space, thread))
        if not candidates:
            return

        # A hash may be shared by a different session or one that is still uploading.
        for (raw,) in db.execute("SELECT data FROM personal_records WHERE space=? AND key GLOB ?",
                                 (self.space, _MANIFESTS + "*")):
            candidates.difference_update(_manifest_parts(raw))
            if not candidates:
                return
        for (sha,) in db.execute("SELECT DISTINCT sha FROM personal_session_parts WHERE space=?", (self.space,)):
            candidates.discard(sha)
        db.executemany("DELETE FROM personal_records WHERE space=? AND key=?",
                       ((self.space, _CHUNKS + sha) for sha in candidates))
        db.executemany("INSERT OR IGNORE INTO personal_retired_parts VALUES(?,?)",
                       ((self.space, sha) for sha in candidates))

    def put(self, key, previous, data, *, session_id=None):
        if len(key) > 650 or data is not None and len(data) >= MAX_RECORD:
            raise ValueError("同步记录过大")
        if key.startswith(RETIREMENT_SOURCES_PREFIX):
            raise ValueError("会话缓存定位信息由服务器维护")
        marker = re.fullmatch(RETIREMENT_PREFIX + f"({_THREAD_ID})", key)
        ack = re.fullmatch(RETIREMENT_ACK_PREFIX + f"({_THREAD_ID})/([A-Za-z0-9_-]{{1,80}})", key)
        if not (marker or ack) and key.startswith((RETIREMENT_PREFIX, RETIREMENT_ACK_PREFIX)):
            raise ValueError("会话删除记录键无效")
        if marker and data is not None:
            retirement_markdown(data)
        if ack and (ack[2] != self.device or data not in (None, b'{"v":1}')):
            raise ValueError("只能确认当前认证设备的会话删除回执")
        part = re.fullmatch(_CHUNKS + f"({_SHA256})", key)
        if session_id is not None and (not part or not re.fullmatch(_THREAD_ID, session_id)
                                       or data is None or part[1] != digest(data)):
            raise ValueError("会话分块归属或校验值无效")
        with closing(sqlite3.connect(self.path)) as db, db:
            if marker:
                db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT hash FROM personal_records WHERE space=? AND key=?", (self.space, key)
            ).fetchone()
            if marker or ack:
                if data is None or (row and row[0] != digest(data)):
                    raise Changed("会话删除记录不可改写或移除")
                if row:
                    return  # The same confirmed write can be retried after losing its receipt.
                if ack and not self._retired(db, ack[1]):
                    raise Changed("会话删除记录尚未登记")
            thread = session_id or _session_thread(key)
            if thread and self._retired(db, thread):
                raise Changed("会话已删除，禁止重新上传")
            if part and data is not None and not session_id and self._retired_part(db, part[1]):
                raise Changed("会话分块已删除，上传需要会话归属")
            if (row[0] if row else EMPTY) != previous:
                raise Changed("另一台设备刚刚更新了记录")
            if marker:
                self._purge_session(db, marker[1])
            elif session_id:
                self._record_parts(db, session_id, (part[1],))
            elif thread and key.startswith(_MANIFESTS):
                parts = _manifest_parts(data)
                for sha in parts:
                    if self._retired_part(db, sha) and not db.execute(
                        "SELECT 1 FROM personal_records WHERE space=? AND key=? AND hash=?",
                        (self.space, _CHUNKS + sha, sha),
                    ).fetchone():
                        raise Changed("会话分块已清理，请重试同步")
                self._record_parts(db, thread, parts)
            db.execute(
                "INSERT OR REPLACE INTO personal_records VALUES(?,?,?,?)",
                (self.space, key, digest(data), data),
            )


class RemoteRecords:
    def __init__(self, client):
        self.client = client

    def request(self, method, path, data=None):
        request = Request(
            self.client.url + "/api/v1/personal/" + path,
            method=method,
            data=data,
            headers={
                "Authorization": "Bearer " + (self.client.token or ""),
                "X-Lanxi-Device": self.client.device,
                "Content-Type": "application/octet-stream",
            },
        )
        try:
            with self.client.opener.open(request, timeout=10) as response:
                result = response.read(MAX_RECORD + 1)
                if len(result) > MAX_RECORD:
                    raise ValueError("接收记录超过上限")
                return result
        except HTTPError as error:
            try:
                if error.code == 409:
                    try:
                        detail = json.loads(error.read(4096)).get("detail")
                    except (AttributeError, ValueError):
                        detail = None
                    raise Changed(detail if isinstance(detail, str) else "远端已更新") from error
            finally:
                error.close()
            raise

    def index(self):
        return json.loads(self.request("GET", "index"))

    def read(self, key):
        return self.request("GET", "record?" + urlencode({"key": key}))

    def retirement_supported(self):
        try:
            return json.loads(self.request("GET", "retirement-supported")) == {"v": 1}
        except HTTPError as error:
            if error.code == 404:
                return False
            raise

    def put(self, key, previous, data, *, session_id=None):
        query = {"key": key, "previous": previous}
        if session_id is not None:
            query["session_id"] = session_id
        self.request(
            "DELETE" if data is None else "PUT",
            "record?" + urlencode(query),
            data,
        )


def install_routes(app, path, enrollment):
    from fastapi import Depends, Header, HTTPException
    from fastapi import Request as APIRequest
    from fastapi.responses import Response
    from starlette.concurrency import run_in_threadpool
    from code_backup_server import install_code_backups

    install_code_backups(app, Path(path).parent / "code-backups", enrollment)

    def authorize(x_lanxi_device: str = Header(default=""), authorization: str = Header(default="")):
        space = enrollment.space(x_lanxi_device, authorization)
        if not space:
            raise HTTPException(403, "此设备未配对个人空间")
        return Records(path, space, x_lanxi_device, check_task_owner=True)

    authorized = Depends(authorize)

    @app.get("/api/v1/personal/index")
    def index(store=authorized):
        return store.index()

    @app.get("/api/v1/personal/retirement-supported")
    def retirement_supported(store=authorized):
        return {"v": 1}

    @app.get("/api/v1/personal/record")
    def read(key: str, store=authorized):
        data = store.read(key)
        if data is None:
            raise HTTPException(404)
        return Response(data, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})

    @app.api_route("/api/v1/personal/record", methods=["PUT", "DELETE"])
    async def write(request: APIRequest, key: str, previous: str, session_id: str | None = None, store=authorized):
        if len(key) > 650 or not re.fullmatch(r"-|[a-f0-9]{64}", previous):
            raise HTTPException(422)
        limit = _MAX_RETIREMENT_JSON if key.startswith(RETIREMENT_PREFIX) else MAX_RECORD
        content = bytearray()
        async for block in request.stream():
            if len(content) + len(block) >= limit:
                raise HTTPException(413)
            content.extend(block)
        try:
            await run_in_threadpool(
                store.put, key, previous, None if request.method == "DELETE" else bytes(content), session_id=session_id
            )
        except Changed as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return Response(status_code=204)
