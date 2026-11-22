"""Local project groups and preferences for background synchronization."""

import json
import threading
from pathlib import Path

from handoff_sync import atomic_write


POLICIES = frozenset(("high", "12h", "24h", "on_open"))
PERIODS = {"12h": 12 * 60 * 60, "24h": 24 * 60 * 60}
_UNSET = object()


def project_key(project):
    if (not isinstance(project, str) or not project or len(project) > 300 or project != project.strip()
            or any(ord(char) < 32 for char in project)):
        raise ValueError("项目标识无效")
    return project


def groups_value(groups):
    if not isinstance(groups, list):
        raise ValueError("项目分组格式无效")
    result, seen_id, seen_name, seen_project = [], set(), set(), set()
    for row in groups:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not isinstance(row.get("projects"), list):
            raise ValueError("项目分组格式无效")
        identifier = project_key(row.get("id"))
        name = " ".join(row["name"].split())
        if not name or len(name) > 80 or identifier in seen_id or name.casefold() in seen_name:
            raise ValueError("项目分组名称或标识重复、过长或为空")
        seen_id.add(identifier)
        seen_name.add(name.casefold())
        members = []
        for project in row["projects"]:
            project_key(project)
            if project in seen_project:
                raise ValueError("一个项目只能加入一个手动分组")
            seen_project.add(project)
            members.append(project)
        result.append({"id": identifier, "name": name, "projects": members})
    return result


class ProjectSyncSettings:
    def __init__(self, root):
        self.path = Path(root) / ".local" / "project-sync-settings.json"
        self.lock = threading.RLock()
        self._stamp = None
        self._data = {"projectPolicies": {}, "projectGroups": []}

    def _load(self):
        try:
            info = self.path.stat()
        except FileNotFoundError:
            self._stamp = None
            self._data = {"projectPolicies": {}, "projectGroups": []}
            return
        stamp = info.st_ino, info.st_mtime_ns, info.st_size
        if stamp == self._stamp:
            return
        data = json.loads(self.path.read_bytes())
        if not isinstance(data, dict) or not isinstance(data.get("projectPolicies"), dict):
            raise ValueError("项目同步设置格式无效")
        policies = data["projectPolicies"]
        if any(not isinstance(policy, str) or policy not in POLICIES for policy in policies.values()):
            raise ValueError("项目同步策略无效")
        self._data = {"projectPolicies": {project_key(key): policy for key, policy in policies.items()},
                      "projectGroups": groups_value(data.get("projectGroups"))}
        self._stamp = stamp

    def _save(self, data):
        if data == self._data:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        atomic_write(self.path, serialized)
        self._stamp = None
        self._data = data

    def snapshot(self):
        with self.lock:
            try:
                self._load()
            except (OSError, ValueError):
                # A damaged external edit must neither stop background sync nor
                # replace the last settings that could be read and validated.
                pass
            return {"projectPolicies": dict(self._data["projectPolicies"]),
                    "projectGroups": [{**group, "projects": group["projects"][:]}
                                      for group in self._data["projectGroups"]]}

    def project_policy(self, project, policy=_UNSET):
        project_key(project)
        if policy is _UNSET:
            return self.snapshot()["projectPolicies"].get(project)
        if policy is not None and (not isinstance(policy, str) or policy not in POLICIES):
            raise ValueError("项目同步策略无效")
        with self.lock:
            self._load()
            updated = self._data["projectPolicies"].copy()
            if policy is None:
                updated.pop(project, None)
            else:
                updated[project] = policy
            self._save({**self._data, "projectPolicies": updated})
        return policy

    def project_groups(self, groups=_UNSET):
        if groups is _UNSET:
            return self.snapshot()["projectGroups"]
        rows = groups_value(groups)
        with self.lock:
            self._load()
            self._save({**self._data, "projectGroups": rows})
        return groups_value(rows)

    def save_project_settings(self, groups, changes):
        rows = groups_value(groups)
        if not isinstance(changes, dict):
            raise ValueError("项目同步策略修改格式无效")
        for project, policy in changes.items():
            project_key(project)
            if policy is not None and (not isinstance(policy, str) or policy not in POLICIES):
                raise ValueError("项目同步策略无效")
        with self.lock:
            self._load()
            policies = self._data["projectPolicies"] | {key: value for key, value in changes.items() if value is not None}
            for key, value in changes.items():
                if value is None:
                    policies.pop(key, None)
            self._save({"projectPolicies": policies, "projectGroups": rows})
            return self.snapshot()
