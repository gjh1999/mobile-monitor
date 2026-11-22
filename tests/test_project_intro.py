import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from plain_projects import folder_id
from project_intro import ProjectIntroStore


class ProjectIntroStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = ProjectIntroStore(self.root)

    def test_empty_and_multiline_introductions_follow_the_stable_identity(self):
        project = folder_id("开发/图像处理")
        other = folder_id("其他/图像处理")
        self.assertEqual(self.store.load(project), ("", ""))
        self.store.save(project, "用途\n第二行", "进度\n待验收")
        reopened = ProjectIntroStore(self.root)
        self.assertEqual(reopened.load(project.removeprefix(".folder/")), ("用途\n第二行", "进度\n待验收"))
        self.assertEqual(reopened.load(other), ("", ""))
        reopened.save(project, "", "")
        self.assertEqual(self.store.load(project), ("", ""))

    def test_concurrent_writes_keep_each_project_separate(self):
        entries = [(folder_id(f"项目/{i}"), f"用途 {i}", f"进度 {i}") for i in range(12)]
        with ThreadPoolExecutor(max_workers=6) as workers:
            list(workers.map(lambda row: self.store.save(*row), entries))
        for key, purpose, progress in entries:
            self.assertEqual(self.store.load(key), (purpose, progress))

    def test_corrupt_record_and_failed_write_are_reported(self):
        project = folder_id("fixture")
        self.store.save(project, "旧用途", "旧进度")
        with patch("project_intro.atomic_write", side_effect=PermissionError("fixture error")):
            with self.assertRaisesRegex(OSError, "无法保存项目介绍"):
                self.store.save(project, "新用途", "新进度")
        self.assertEqual(self.store.load(project), ("旧用途", "旧进度"))
        with self.assertRaisesRegex(ValueError, "必须为文本"):
            self.store.save(project, None, "错误类型")
        path, = (self.root / ".local" / "project-intros").glob("*.json")
        path.write_bytes(b"damaged record")
        with self.assertRaisesRegex(ValueError, "项目介绍文件内容无效"):
            self.store.load(project)
