"""Keep the installed desktop's data and workspace independent of its executable."""

import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
from contextlib import closing, nullcontext
from pathlib import Path

from service_lock import ServiceLock

PROFILE = "desktop-profile.json"
FILES = (
    "project-library.json",
    "local-config.json",
    "personal-sync.json",
    "local-model-api.json",
    "claude-drafts.json",
    "lanxi-notebook.sqlite3",
    "lanxi-groups.sqlite3",
    "scheduler.sqlite3",
    "managed.sqlite3",
    "task-control.sqlite3",
    "claude-task-control.sqlite3",
    "replies.sqlite3",
    "claude-replies.sqlite3",
    "notifications.sqlite3",
    "windows-notifications.sqlite3",
    "claude-windows-notifications.sqlite3",
    ".local/handoff-sync.json",
    ".local/ide-close.sqlite3",
    ".local/personal-sync",
    ".local/desktop-wallpapers",
    ".local/remote-completion-notifications",
)


def user_data_root():
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "Lanxi" / "data"


def has_legacy_data(folder):
    return folder.is_dir() and any((folder / name).exists() for name in FILES)


def legacy_candidates(program):
    if program.parent.name.lower() != "release":
        return ()
    folders = [program.parent, *sorted(program.parent.glob("Lanxi-R*-Portable"), reverse=True)]
    return tuple(folder for folder in folders if folder != program and has_legacy_data(folder))


def installed_workspace(target):
    profile = json.loads((target / PROFILE).read_text(encoding="utf-8"))
    path = Path(profile["workspace"])
    if profile["version"] != 1 or not path.is_absolute():
        raise ValueError("澜汐数据目录记录无效")
    return path


def copy_state(source, target):
    info = source.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError(f"旧数据包含链接，请检查 {source}")
    if stat.S_ISDIR(info.st_mode):
        target.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            if not child.name.endswith(("-wal", "-shm", "-journal")):
                copy_state(child, target / child.name)
        return
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"旧数据包含特殊文件，请检查 {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix in (".db", ".sqlite3"):
        deadline = time.monotonic() + 5

        def progress(status, _remaining, _total):
            nonlocal deadline
            if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"旧数据库仍被占用，请关闭旧版澜汐后重试。\n{source}")
            else:
                deadline = time.monotonic() + 5

        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=3)) as old,
            closing(sqlite3.connect(target)) as new,
        ):
            old.backup(new, pages=256, progress=progress, sleep=0.05)
    else:
        shutil.copyfile(source, target)


def prepare_data(program, target, select_legacy, source_override=None):
    """Return the preserved workspace, or None when the first run is canceled."""
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = ServiceLock(target.parent / "upgrade.lockfile")
    try:
        if target.exists():
            if source_override is not None:
                raise ValueError("固定用户数据目录已建立，请直接启动新版澜汐")
            return installed_workspace(target)
        source = source_override
        if source is None:
            source = program if has_legacy_data(program) else select_legacy(legacy_candidates(program))
        if source is None:
            return None
        if source is not False:
            source = source.resolve()
            if not has_legacy_data(source) or target.is_relative_to(source) or source.is_relative_to(target):
                raise ValueError("所选文件夹须为含数据的旧版澜汐目录")
        workspace = (source if source else program).parent
        source_lock = source / "monitor.lockfile" if source else None
        # The old desktop owns this lock through shutdown. Copy only after it releases it.
        guard = closing(ServiceLock(source_lock)) if source_lock and source_lock.is_file() else nullcontext()
        with guard, tempfile.TemporaryDirectory(prefix=".upgrade-", dir=target.parent) as scratch:
            stage = Path(scratch) / "data"
            stage.mkdir()
            if source:
                for name in FILES:
                    path = source / name
                    if path.exists() or path.is_symlink():
                        # Paths within .local share the same ancestry.
                        if path.parent != source:
                            parent_info = path.parent.lstat()
                            if (
                                path.parent.is_symlink()
                                or getattr(parent_info, "st_file_attributes", 0)
                                & stat.FILE_ATTRIBUTE_REPARSE_POINT
                            ):
                                raise ValueError(f"旧数据包含目录链接，请检查 {path.parent}")
                        copy_state(path, stage / name)
            (stage / PROFILE).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "workspace": str(workspace),
                        "importedFrom": str(source) if source else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            stage.rename(target)
        return workspace
    finally:
        lock.close()
