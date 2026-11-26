import json
import tempfile
import unittest
from pathlib import Path

from exporter import export


class ExportTest(unittest.TestCase):
    def test_visible_rows(self):
        rows = [{"updated": n, "id": str(n), "archived": n == 2} for n in (3, 2, 1)]
        with tempfile.TemporaryDirectory() as root:
            dest = Path(root, "tasks.jsonl")
            export(rows, dest, page_size=1)
            self.assertEqual([json.loads(line)["id"] for line in dest.read_text(encoding="utf-8").splitlines()], ["1", "3"])
