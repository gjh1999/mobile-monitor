"""Install the official Codex approval hook and consume its local reminders."""

import base64
import json
import os
import re
import sys
import time
from contextlib import closing
from pathlib import Path

from handoff_sync import atomic_write
from service_lock import ServiceLock

HOOK_EVENT = "PermissionRequest"
ALERTS = "codex-approval-alerts"
MAX_AGE = 120
FILE_NAME = re.compile(r"[0-9a-f]{32}\.json")
COMMAND_PREFIX = "powershell.exe -NoProfile -NonInteractive -EncodedCommand "
STATUS_MESSAGE = "澜汐审批提醒"


def hook_home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _hook(command):
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    return {
        "hooks": [{
            "type": "command",
            "command": COMMAND_PREFIX + encoded,
            "timeout": 8,
            "statusMessage": STATUS_MESSAGE,
        }]
    }


def hook_entry(root):
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    source = (bundle / "scripts" / "codex-approval-alert.ps1").read_text(encoding="utf-8")
    # A command can run under Restricted without executing a .ps1 file.
    return _hook(_inbox_header(root) + source)


def _inbox_header(root):
    inbox = str(root / ".local" / ALERTS).replace("'", "''")
    return f"$inbox = '{inbox}'\n"


def _legacy_hook(root):
    script = str(root / ".local" / "codex-approval-alert.ps1").replace("'", "''")
    return _hook("& '" + script + "'")


def _owned_hook(row, root, legacy):
    if row == legacy:
        return True
    hooks = row.get("hooks")
    if set(row) != {"hooks"} or not isinstance(hooks, list) or len(hooks) != 1:
        return False
    entry = hooks[0]
    if not isinstance(entry, dict) or entry.get("type") != "command" or entry.get("statusMessage") != STATUS_MESSAGE:
        return False
    command = entry.get("command")
    if not isinstance(command, str) or not command.startswith(COMMAND_PREFIX):
        return False
    try:
        source = base64.b64decode(command[len(COMMAND_PREFIX):], validate=True).decode("utf-16-le")
    except ValueError:
        return False
    return source.startswith(_inbox_header(root) + "# Notify the local desktop and let Codex open its original approval prompt.")


def user_hook_config(path):
    raw = path.read_bytes() if path.exists() else None
    config = json.loads(raw.decode("utf-8-sig")) if raw is not None else {"hooks": {}}
    if not isinstance(config, dict) or not isinstance(config.setdefault("hooks", {}), dict):
        raise ValueError("现有 Codex hooks.json 格式无法识别。")
    events = config["hooks"]
    group = events.get(HOOK_EVENT, [])
    if not isinstance(group, list) or any(not isinstance(row, dict) for row in group):
        raise ValueError("现有 Codex PermissionRequest 配置格式无法识别。")
    return config, group, raw


def hook_status(root, home):
    path = home / "hooks.json"
    entry = hook_entry(root)
    _, existing, _ = user_hook_config(path)
    legacy = _legacy_hook(root)
    owned = [row for row in existing if _owned_hook(row, root, legacy)]
    return 'current' if owned == [entry] else 'outdated' if owned else 'missing'


def configure(root, home, enabled):
    path = home / "hooks.json"
    if not home.is_absolute():
        raise ValueError("Codex 用户配置目录需要使用绝对路径。")
    home.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("请在原始 Codex 配置目录中安装提醒 Hook。")
    entry = hook_entry(root)
    legacy = _legacy_hook(root)
    with closing(ServiceLock(home / "lanxi-approval-hook.lock")):
        config, existing, original = user_hook_config(path)
        if enabled and [row for row in existing if _owned_hook(row, root, legacy)] == [entry]:
            return
        updated = [row for row in existing if not _owned_hook(row, root, legacy)]
        if enabled:
            updated.append(entry)
        if updated == existing:
            return
        if updated:
            config["hooks"][HOOK_EVENT] = updated
        else:
            config["hooks"].pop(HOOK_EVENT, None)
        if (path.read_bytes() if path.exists() else None) != original:
            raise ValueError("Codex Hook 配置刚刚被修改，请重试。")
        atomic_write(path, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def take_alerts(root):
    inbox = root / ".local" / ALERTS
    alerts = []
    now = time.time()
    for path in inbox.glob("*.json"):
        if not FILE_NAME.fullmatch(path.name) or path.is_symlink():
            continue
        try:
            info = path.stat()
            if not path.is_file():
                continue
            alert = None
            if 0 <= now - info.st_mtime <= MAX_AGE and info.st_size <= 4096:
                try:
                    row = json.loads(path.read_bytes())
                except ValueError:
                    row = None
                if isinstance(row, dict) and all(isinstance(row.get(k), str) for k in ("project", "tool", "thread")):
                    alert = {k: " ".join(row[k].split())[:160] for k in ("project", "tool", "thread")}
            path.unlink()
            if alert:
                alerts.append(alert)
        except OSError:
            continue
        if len(alerts) == 12:
            break
    return alerts
