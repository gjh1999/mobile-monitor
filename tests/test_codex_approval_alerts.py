import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from codex_approval_alerts import _hook, _inbox_header, _legacy_hook, configure, hook_entry, hook_status, take_alerts


class CodexApprovalAlertsTests(unittest.TestCase):
    def test_update_replaces_stale_embedded_hooks_without_touching_other_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "codex"
            home.mkdir()
            path = home / "hooks.json"
            previous = _hook(_inbox_header(root) +
                             "# Notify the local desktop and let Codex open its original approval prompt.\nexit 0")
            current = hook_entry(root)
            other = hook_entry(root / "other data")
            path.write_text(json.dumps({"hooks": {"PermissionRequest": [previous, other, current]}}), encoding="utf-8")
            self.assertEqual(hook_status(root, home), 'outdated')
            configure(root, home, True)
            self.assertEqual(json.loads(path.read_bytes())["hooks"]["PermissionRequest"], [other, current])
            self.assertEqual(hook_status(root, home), 'current')
            configure(root, home, False)
            self.assertEqual(json.loads(path.read_bytes())["hooks"]["PermissionRequest"], [other])

    def test_toggle_preserves_other_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "user codex"
            home.mkdir()
            path = home / "hooks.json"
            existing = {"description": "fixture", "hooks": {
                "PermissionRequest": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "fixture"}]}],
                "Stop": [{"hooks": [{"type": "command", "command": "other"}]}],
            }}
            path.write_text(json.dumps(existing), encoding="utf-8")
            configure(root, home, True)
            configure(root, home, True)
            self.assertEqual(hook_status(root, home), 'current')
            actual = json.loads(path.read_bytes())
            self.assertEqual(actual["hooks"]["PermissionRequest"], existing["hooks"]["PermissionRequest"]
                             + [hook_entry(root)])
            configure(root, home, False)
            self.assertEqual(hook_status(root, home), 'missing')
            self.assertEqual(json.loads(path.read_bytes()), existing)
            path.write_bytes(b"broken")
            with self.assertRaises(ValueError):
                configure(root, home, True)
            self.assertEqual(path.read_bytes(), b"broken")

    def test_enabling_replaces_the_legacy_hook_for_this_desktop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "codex"
            home.mkdir()
            path = home / "hooks.json"
            other = _legacy_hook(root / "other data")
            path.write_text(json.dumps({"hooks": {"PermissionRequest": [_legacy_hook(root), other]}}), encoding="utf-8")
            self.assertEqual(hook_status(root, home), 'outdated')
            configure(root, home, True)
            self.assertEqual(json.loads(path.read_bytes())["hooks"]["PermissionRequest"], [other, hook_entry(root)])

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell hook")
    def test_hook_emits_reminder_and_no_approval_decision(self):
        with tempfile.TemporaryDirectory(prefix="澜汐 hook's path ") as directory:
            root = Path(directory)
            configure(root, root / "codex", True)
            command = hook_entry(root)["hooks"][0]["command"].split()
            env = {name: value for name, value in os.environ.items() if name.casefold() != "psexecutionpolicypreference"}
            env["PSExecutionPolicyPreference"] = "Restricted"
            event = {"hook_event_name": "PermissionRequest", "cwd": str(root / "某项目"),
                     "session_id": "fixture", "tool_name": "Bash",
                     "tool_input": {"command": "private detail"}}
            output = subprocess.run(command, input=json.dumps(event, ensure_ascii=False).encode("utf-8"),
                                    env=env, capture_output=True, timeout=10, check=True)
            self.assertEqual(output.stdout, b"")
            self.assertEqual(take_alerts(root), [{"project": "某项目", "tool": "Bash", "thread": "fixture"}])
            self.assertEqual(take_alerts(root), [])
            event["hook_event_name"] = "Stop"
            subprocess.run(command, input=json.dumps(event).encode(), capture_output=True, timeout=10, check=True)
            self.assertEqual(take_alerts(root), [])
