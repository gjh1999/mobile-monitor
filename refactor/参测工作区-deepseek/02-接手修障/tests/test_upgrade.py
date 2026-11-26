import json
import unittest
from pathlib import Path

from subscriptions import configure, current, status

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "upgrade.json"


def load_upgrade():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class UpgradeConfig(unittest.TestCase):
    def test_status_outdated_current_missing(self):
        data = load_upgrade()
        # lab 目前是 v2 → outdated
        self.assertEqual(status(data, "lab"), "outdated")
        # studio 也是 v2 → outdated
        self.assertEqual(status(data, "studio"), "outdated")
        # 没有的工作区 → missing
        self.assertEqual(status(data, "nowhere"), "missing")
        # 启用后变 current
        configure(data, "lab", True)
        self.assertEqual(status(data, "lab"), "current")

    def test_enable_upgrades_v2_and_preserves_others(self):
        data = load_upgrade()
        self.assertTrue(configure(data, "lab", True))
        rows = data["subscriptions"]
        # calendar（第三方）与 studio（其他工作区）保留，顺序不变
        self.assertEqual(rows[0]["provider"], "calendar")
        self.assertEqual(rows[0]["renderer"], "toast-v2")
        self.assertEqual(rows[2]["provider"], "deskbell")
        self.assertEqual(rows[2]["workspace"], "studio")
        self.assertEqual(rows[2]["renderer"], "toast-v2")
        # lab 被替换为 v3，且只有一条 deskbell/lab 记录
        lab_rows = [r for r in rows if r.get("provider") == "deskbell" and r.get("workspace") == "lab"]
        self.assertEqual(len(lab_rows), 1)
        self.assertEqual(lab_rows[0], current("lab"))
        self.assertEqual(lab_rows[0]["renderer"], "toast-v3")

    def test_enable_idempotent(self):
        data = load_upgrade()
        self.assertTrue(configure(data, "lab", True))
        snapshot = json.dumps(data, ensure_ascii=False, sort_keys=True)
        # 再次启用：无变化
        self.assertFalse(configure(data, "lab", True))
        self.assertEqual(json.dumps(data, ensure_ascii=False, sort_keys=True), snapshot)

    def test_disable_removes_legacy_v2(self):
        data = load_upgrade()
        # 未升级、仍是 v2 的情况下，disable 也要能停掉（修复“关掉再开也不总能恢复”）
        self.assertTrue(configure(data, "lab", False))
        self.assertEqual(status(data, "lab"), "missing")
        # 其他工作区保留
        self.assertEqual(status(data, "studio"), "outdated")

    def test_v1_owner_project_event(self):
        data = {"subscriptions": [
            {"owner": "deskbell", "project": "lab", "event": "task", "renderer": "toast-v1"},
            {"owner": "calendar", "project": "lab", "event": "task"},
        ]}
        self.assertEqual(status(data, "lab"), "outdated")
        self.assertTrue(configure(data, "lab", True))
        rows = data["subscriptions"]
        self.assertEqual(len(rows), 2)
        # 第一条 deskbell 被替换为 v3
        self.assertEqual(rows[0], current("lab"))
        # 第三条 calendar 保留
        self.assertEqual(rows[1]["owner"], "calendar")

    def test_workspace_case_sensitive(self):
        data = {"subscriptions": [current("Lab")]}
        # 大小写敏感：lab != Lab
        self.assertEqual(status(data, "lab"), "missing")
        self.assertEqual(status(data, "Lab"), "current")

    def test_dedupe_multiple_legacy_rows(self):
        data = {"subscriptions": [
            {"provider": "deskbell", "workspace": "lab", "topic": "task", "renderer": "toast-v2"},
            {"provider": "deskbell", "workspace": "lab", "topic": "task", "renderer": "toast-v2"},
            {"provider": "calendar", "workspace": "lab", "topic": "task"},
        ]}
        self.assertTrue(configure(data, "lab", True))
        desk_rows = [r for r in data["subscriptions"] if r.get("provider") == "deskbell"]
        self.assertEqual(len(desk_rows), 1)
        self.assertEqual(desk_rows[0], current("lab"))
        # calendar 仍在
        self.assertEqual(len(data["subscriptions"]), 2)


if __name__ == "__main__":
    unittest.main()
