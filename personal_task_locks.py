"""Persistent, private-space-scoped ownership of a Codex session."""

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Path
from pydantic import BaseModel, Field

from coordination import Conflict, Ownership
from personal_relay import RETIREMENT_PREFIX

Session = Annotated[str, Path(pattern=r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")]


class Claim(BaseModel):
    project: str = Field(pattern=r"^[a-z0-9_.-]{1,100}/[a-z0-9_.-]{1,100}$")
    checkpoint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class Release(BaseModel):
    ticket: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    checkpoint: str = Field(pattern=r"^[a-f0-9]{64}$")


class Activity(BaseModel):
    project: str = Field(pattern=r"^[a-z0-9_.-]{1,100}/[a-z0-9_.-]{1,100}$")
    session: str = Field(pattern=r"^[a-f0-9-]{36}$")
    event: str = Field(pattern=r"^(open|heartbeat|close)$")


class TaskLocks(Ownership):
    def __init__(self, store):
        self.path = store.path
        with self.transaction() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS personal_task_locks (
                    space TEXT, session TEXT, device TEXT, ticket TEXT, checkpoint TEXT, project TEXT,
                    PRIMARY KEY(space, session)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS personal_project_activity (
                    space TEXT, project TEXT, session TEXT, device TEXT, closed INTEGER,
                    PRIMARY KEY(space, project, session)
                )
            """)

    @staticmethod
    def entry(db, space, session):
        return db.execute("SELECT device,ticket,checkpoint,project FROM personal_task_locks WHERE space=? AND session=?",
                          (space, session)).fetchone()

    @staticmethod
    def project_owner(db, space, project):
        row = db.execute("""
            SELECT device FROM personal_task_locks WHERE space=? AND project=? AND device IS NOT NULL
            UNION SELECT device FROM personal_project_activity WHERE space=? AND project=? AND closed=0
        """, (space, project, space, project)).fetchone()
        return row["device"] if row else None

    def active(self, space):
        with self.transaction() as db:
            tasks = {session: device for session, device in db.execute(
                "SELECT session,device FROM personal_task_locks WHERE space=? AND device IS NOT NULL", (space,)
            )}
            projects = {project: device for project, device in db.execute("""
                SELECT project,device FROM personal_task_locks WHERE space=? AND device IS NOT NULL
                UNION SELECT project,device FROM personal_project_activity WHERE space=? AND closed=0
            """, (space, space))}
            return {"tasks": tasks, "projects": projects}

    def report(self, space, device, event):
        with self.transaction() as db:
            key = space, event.project, event.session
            existing = db.execute(
                "SELECT device,closed FROM personal_project_activity WHERE space=? AND project=? AND session=?", key
            ).fetchone()
            if existing and (existing["device"] != device or existing["closed"] and event.event != "close"):
                raise Conflict("已关闭的 IDE 窗口需要新的活动编号。")
            if event.event != "close":
                owner = self.project_owner(db, space, event.project)
                if owner and owner != device:
                    raise Conflict(f"设备 {owner} 正在使用此项目，请等待交还。")
            db.execute("INSERT OR REPLACE INTO personal_project_activity VALUES(?,?,?,?,?)",
                       (*key, device, event.event == "close"))

    def status(self, space, session):
        with self.transaction() as db:
            row = self.entry(db, space, session)
            return {"device": row["device"], "checkpoint": row["checkpoint"]} if row else {
                "device": None, "checkpoint": None,
            }

    def claim(self, space, session, device, request):
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='personal_records' AND type='table'").fetchone():
                if db.execute("SELECT 1 FROM personal_records WHERE space=? AND key=?",
                              (space, RETIREMENT_PREFIX + session)).fetchone():
                    raise Conflict("此 Codex 会话已删除，无法再接入。")
            row = self.entry(db, space, session)
            if row and row["device"]:
                if row["device"] != device:
                    raise Conflict(f"设备 {row['device']} 正在控制此任务，当前仅可查看。")
                if row["project"] != request.project:
                    raise Conflict("本机任务项目与服务器登记不一致。")
                return row["ticket"]
            owner = self.project_owner(db, space, request.project)
            if owner and owner != device:
                raise Conflict(f"设备 {owner} 正在使用此项目，当前仅可查看。")
            if row and row["project"] != request.project:
                raise Conflict("本机任务项目与服务器登记不一致。")
            if (row["checkpoint"] if row else None) != request.checkpoint:
                raise Conflict("任务交接版本已变化，请等同步后重试。")
            ticket = secrets.token_urlsafe(32)
            db.execute("INSERT OR REPLACE INTO personal_task_locks VALUES(?,?,?,?,NULL,?)",
                       (space, session, device, ticket, request.project))
            return ticket

    def handoff(self, space, session, device, claim):
        with self.transaction() as db:
            row = self.entry(db, space, session)
            if row and row["device"] is None and row["checkpoint"] == claim.checkpoint:
                return
            if not row or row["device"] != device or not secrets.compare_digest(row["ticket"] or "", claim.ticket):
                raise Conflict("当前设备没有此任务的交还权限。")
            db.execute("UPDATE personal_task_locks SET device=NULL,ticket=NULL,checkpoint=? WHERE space=? AND session=?",
                       (claim.checkpoint, space, session))


def install_task_locks(app, store, enrollment):
    locks = TaskLocks(store)

    def identity(x_lanxi_device: str = Header(default=""), authorization: str = Header(default="")):
        space = enrollment.space(x_lanxi_device, authorization)
        if not space:
            raise HTTPException(403, "此设备未配对个人空间")
        return space, x_lanxi_device

    owner = Depends(identity)

    @app.get("/api/v1/personal/tasks")
    def active(key=owner):
        return locks.active(key[0])

    @app.post("/api/v1/personal/project-activity")
    def report(event: Activity, key=owner):
        try:
            locks.report(key[0], key[1], event)
        except Conflict as error:
            raise HTTPException(409, str(error)) from error
        return {"accepted": True}

    @app.get("/api/v1/personal/tasks/{session}")
    def status(session: Session, key=owner):
        return locks.status(key[0], session)

    @app.post("/api/v1/personal/tasks/{session}/claim")
    def claim(session: Session, request: Claim, key=owner):
        try:
            return {"ticket": locks.claim(key[0], session, key[1], request)}
        except Conflict as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/v1/personal/tasks/{session}/handoff")
    def handoff(session: Session, request: Release, key=owner):
        try:
            locks.handoff(key[0], session, key[1], request)
        except Conflict as error:
            raise HTTPException(409, str(error)) from error
        return {"released": True}
