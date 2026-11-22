"""Keep the desktop task tree's manual layout on the current computer."""

import json
import threading
from pathlib import Path

from handoff_sync import atomic_write


_WRITE_LOCK = threading.Lock()


def _key(value):
    if not isinstance(value, str):
        raise ValueError("任务树位置标识无效")
    return value


def _unique(ids):
    return list(dict.fromkeys(_key(identifier) for identifier in ids))


class TaskTreePreferences:
    def __init__(self, root):
        self.path = Path(root) / ".local" / "task-tree-preferences.json"
        try:
            self._data = self._read()
        except (OSError, ValueError):
            self._data = {"orders": {}, "destinations": {}}

    def _read(self):
        try:
            data = json.loads(self.path.read_bytes())
        except FileNotFoundError:
            return {"orders": {}, "destinations": {}}
        if not isinstance(data, dict):
            raise ValueError("任务树布局文件格式无效")
        normalized = {}
        for section in ("orders", "destinations"):
            clients = data.get(section, {})
            if not isinstance(clients, dict) or any(not isinstance(rows, dict) for rows in clients.values()):
                raise ValueError("任务树布局文件格式无效")
            if section == "orders":
                if any(not isinstance(ids, list) for rows in clients.values() for ids in rows.values()):
                    raise ValueError("任务树排序文件格式无效")
                normalized[section] = {client: {parent: _unique(ids) for parent, ids in rows.items()}
                                       for client, rows in clients.items()}
            else:
                normalized[section] = {client: {tid: _key(project) for tid, project in rows.items()}
                                       for client, rows in clients.items()}
        return normalized

    def _save(self, section, client, name, value):
        client, name = _key(client), _key(name)
        with _WRITE_LOCK:
            data = self._read()
            clients = data[section]
            rows = clients.get(client, {})
            if rows.get(name) != value:
                if value is None:
                    rows.pop(name, None)
                else:
                    rows[name] = value
                if rows:
                    clients[client] = rows
                else:
                    clients.pop(client, None)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                atomic_write(self.path, payload)
            self._data = data

    def ordered(self, client, parent, existing):
        ids = _unique(existing)
        saved = self._data["orders"].get(client, {}).get(parent, ())
        available = set(ids)
        first = [identifier for identifier in saved if identifier in available]
        placed = set(first)
        return first + [identifier for identifier in ids if identifier not in placed]

    def order(self, client, parent, ids):
        self._save("orders", client, parent, _unique(ids) or None)

    def destination(self, client, tid):
        return self._data["destinations"].get(client, {}).get(tid)

    def move(self, client, tid, project_key_or_none):
        project = None if project_key_or_none is None else _key(project_key_or_none)
        self._save("destinations", client, tid, project)
