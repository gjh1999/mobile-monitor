"""Local project introductions, stored independently of project sync."""

import hashlib
import json
import re
from pathlib import Path

from handoff_sync import atomic_write
from personal_memory import safe_path
from project_sync_policy import project_key


class ProjectIntroStore:
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, key):
        key = project_key(key)
        folder = re.fullmatch(r"(?:\.folder/)?([a-f0-9]{64})", key)
        canonical = ".folder/" + folder[1] if folder else key
        name = hashlib.sha256(canonical.encode("utf-8")).hexdigest() + ".json"
        return safe_path(self.root, f".local/project-intros/{name}")

    def load(self, key):
        path = self._path(key)
        try:
            record = json.loads(path.read_bytes())
        except FileNotFoundError:
            return "", ""
        except ValueError as error:
            raise ValueError("项目介绍文件内容无效") from error
        except OSError as error:
            raise OSError(f"无法读取项目介绍。{error}") from error
        if (not isinstance(record, dict) or not isinstance(record.get("purpose"), str)
                or not isinstance(record.get("progress"), str)):
            raise ValueError("项目介绍文件内容无效")
        return record["purpose"], record["progress"]

    def save(self, key, purpose, progress):
        path = self._path(key)
        if not isinstance(purpose, str) or not isinstance(progress, str):
            raise ValueError("项目用途和进度必须为文本")
        data = (json.dumps({"purpose": purpose, "progress": progress}, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, data)
        except OSError as error:
            raise OSError(f"无法保存项目介绍。{error}") from error
