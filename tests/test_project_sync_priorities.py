import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from personal_memory import pack
from personal_relay import Records, RemoteRecords
from personal_sync import PersonalSync, ProjectSyncRound
from plain_projects import folder_id
from project_library import set_project_root
from project_sync_policy import ProjectSyncSettings


HIGH, TWELVE, DAY, OPEN = ("owner/" + name for name in ("high", "twelve", "day", "open"))
THREAD = "7b3e57a8-167d-420e-ac1d-146aa17b5305"


class LocalRelay(RemoteRecords):
    def __init__(self, path):
        self.client = SimpleNamespace(device="fixture")
        self.records = Records(path, "owner")

    def index(self):
        return self.records.index()

    def read(self, key):
        return self.records.read(key)

    def put(self, key, previous, data):
        self.records.put(key, previous, data)

    def retirement_supported(self):
        return False


class ProjectSyncPrioritiesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "codex"
        self.home.mkdir()
        self.parent = self.root / "projects"
        self.parent.mkdir()
        set_project_root(self.root, self.parent)
        self.flow = self.new_flow()
        self.stop = threading.Event()

    def new_flow(self):
        return PersonalSync(self.root, {}, {"url": "https://example.invalid", "device": "fixture"}, self.home)

    def test_atomic_group_and_policy_edit_is_local_and_persistent(self):
        settings = ProjectSyncSettings(self.root)
        groups = [{"id": "one", "name": "项目一", "projects": [HIGH, OPEN]}]
        settings.save_project_settings(groups, {HIGH: "high", OPEN: "on_open"})
        self.assertEqual(self.flow.project_groups(), groups)
        self.assertEqual(self.flow.project_policy(OPEN), "on_open")
        with self.assertRaises(ValueError):
            settings.save_project_settings([], {HIGH: "unknown"})
        self.assertEqual(ProjectSyncSettings(self.root).project_groups(), groups)
        with self.assertRaises(ValueError):
            settings.project_groups(groups + [{"id": "two", "name": "项目二", "projects": [OPEN]}])
        self.flow.save_project_settings(groups, {OPEN: None})
        self.assertIsNone(settings.project_policy(OPEN))
        self.assertEqual(settings.project_policy(HIGH), "high")

    def test_high_priority_and_folder_alias_apply_to_the_same_workspace(self):
        local = {key: self.parent / key.split("/")[1] for key in (TWELVE, HIGH, OPEN, DAY)}
        alias = folder_id("open")
        last = {TWELVE: [30, str(local[TWELVE])], DAY: [30, str(local[DAY])]}
        policies = {TWELVE: "12h", HIGH: "high", alias: "on_open", DAY: "24h"}
        with patch("personal_sync.time.time", return_value=30 + 12 * 60 * 60):
            plan = ProjectSyncRound(policies, last, self.parent, local, set(), {alias}, False)
        self.assertEqual(sorted(local, key=lambda key: plan.priority(key, local[key])), [HIGH, OPEN, TWELVE, DAY])
        self.assertTrue(plan.due(TWELVE, local[TWELVE]))
        self.assertFalse(plan.due(DAY, local[DAY]))
        self.assertTrue(plan.due(OPEN, local[OPEN]))
        self.assertTrue(plan.due(alias, local[OPEN]))
        with patch("personal_sync.time.time", return_value=30 + 24 * 60 * 60):
            later = ProjectSyncRound(policies, last, self.parent, local, set(), set(), False)
        self.assertTrue(later.due(DAY, local[DAY]))
        self.assertFalse(later.due(OPEN, local[OPEN]))
        delayed = ProjectSyncRound(policies, last, self.parent, local, set(), set(), False, waiting={HIGH})
        self.assertFalse(delayed.due(HIGH, local[HIGH]))
        manual = ProjectSyncRound(policies, last, self.parent, local, set(), set(), True, waiting={HIGH})
        self.assertTrue(manual.due(HIGH, local[HIGH]))

    def test_skip_expensive_channels_delay_open_and_preserve_success_after_restart(self):
        relay = LocalRelay(self.root / "relay.sqlite3")
        local = {key: self.parent / key.split("/")[1] for key in (TWELVE, HIGH, OPEN)}
        specs = {key: {"remote": f"https://github.com/{key}.git", "memoryPath": "notes"} for key in local}
        for directory in local.values():
            (directory / ".git").mkdir(parents=True)
            (directory / "notes").mkdir()
            (directory / "notes" / "status.md").write_text("first", encoding="utf-8")
        filename = f"rollout-2026-11-22T10-00-00-{THREAD}.jsonl"
        native = self.home / "sessions" / "2026" / "11" / "22" / filename
        native.parent.mkdir(parents=True)

        def turn(turn_id):
            return b"".join(pack({"type": "event_msg", "payload": {"type": kind, "turn_id": turn_id}}) + b"\n"
                            for kind in ("task_started", "task_complete"))

        initial = pack({"type": "session_meta", "payload": {"id": THREAD, "cwd": str(local[TWELVE])}}) + b"\n" + turn("one")
        native.write_bytes(initial)
        self.flow.save_project_settings([], {HIGH: "high", TWELVE: "12h", OPEN: "on_open"})
        git, backup = Mock(), Mock(return_value="已备份")

        def use(flow):
            flow.store = lambda: relay
            flow.bindings = lambda *_: (local, specs)
            flow.code_backups = lambda: SimpleNamespace(sync=backup)

        def transferred():
            return {call.args[0] for call in git.call_args_list}, {call.args[1] for call in backup.call_args_list}

        use(self.flow)
        with (patch("personal_sync.sync_project", git), patch("personal_sync.verify_checkout"),
              patch("personal_sync.exclude_memory"), patch("personal_sync.value", side_effect=lambda root, *_: str(root / ".git")),
              patch("large_inventory.sync_inventory", return_value=({}, {})) as inventory,
              patch("personal_sync.time.time", return_value=10000),
              patch("personal_sync.time.monotonic", return_value=100) as monotonic):
            first = self.flow.cycle(self.stop)
            self.assertEqual(transferred(), ({local[HIGH], local[TWELVE]}, {HIGH, TWELVE}))
            self.assertEqual(set(inventory.call_args.args[3]), {HIGH, TWELVE})
            self.assertEqual(first["deferredProjects"], {OPEN: "on_open"})
            self.assertIsNone(relay.read(f"notes/{OPEN}/status.md"))
            stored = f"sessions/{TWELVE}/{filename}"
            self.assertTrue(relay.read(stored).endswith(initial))
            native.write_bytes(initial + turn("two"))
            (local[TWELVE] / "notes" / "status.md").write_text("second", encoding="utf-8")

            self.flow.wake.clear()
            self.assertEqual(self.flow.request_after_open(OPEN), 0)
            self.assertFalse(self.flow.wake.is_set())
            monotonic.return_value = 104.99
            self.flow.request_after_open(OPEN)
            git.reset_mock()
            backup.reset_mock()
            self.flow.cycle(self.stop)
            self.assertEqual(transferred(), ({local[HIGH]}, {HIGH}))
            self.assertEqual(relay.read(f"notes/{TWELVE}/status.md"), b"first")
            self.assertTrue(relay.read(stored).endswith(initial))
            self.assertIn(THREAD, self.flow.snapshot()["sessions"])
            self.assertFalse(self.flow._opened_due())

            monotonic.return_value = 105
            self.assertTrue(self.flow._opened_due())
            git.reset_mock()
            backup.reset_mock()
            def backup_fails(_, key, *__):
                if key == OPEN:
                    raise OSError("fixture network failure")
                return "已备份"
            backup.side_effect = backup_fails
            self.flow.cycle(self.stop)
            self.assertEqual(transferred(), ({local[HIGH], local[OPEN]}, {HIGH, OPEN}))
            self.assertFalse(self.flow._opened_due())

            monotonic.return_value = 119.99
            git.reset_mock()
            backup.reset_mock()
            self.flow.cycle(self.stop)
            self.assertEqual(transferred(), ({local[HIGH]}, {HIGH}))
            monotonic.return_value = 120
            self.assertTrue(self.flow._opened_due())
            backup.side_effect = None
            self.flow.cycle(self.stop)
            self.assertEqual(relay.read(f"notes/{OPEN}/status.md"), b"first")
            self.assertFalse(self.flow._opened_due())

            fresh = self.new_flow()
            use(fresh)
            git.reset_mock()
            backup.reset_mock()
            fresh.cycle(self.stop)
            self.assertEqual(transferred(), ({local[HIGH]}, {HIGH}))
            self.assertEqual(fresh.snapshot()["deferredProjects"], {TWELVE: "12h", OPEN: "on_open"})

            fresh.request(None)
            git.reset_mock()
            backup.reset_mock()
            fresh.cycle(self.stop)
            self.assertEqual(transferred(), (set(local.values()), set(local)))
            self.assertEqual(relay.read(f"notes/{TWELVE}/status.md"), b"second")
            self.assertTrue(relay.read(stored).endswith(initial + turn("two")))

            fresh.request_after_open(OPEN)
            request = fresh.request_manual_sync(fresh=True)
            git.reset_mock()
            backup.reset_mock()
            worker = threading.Thread(target=fresh.worker, args=(self.stop,), daemon=True)
            worker.start()
            try:
                result = request.result(timeout=5)
                self.assertFalse(result["deferredProjects"])
                self.assertEqual(transferred(), (set(local.values()), set(local)))
            finally:
                self.stop.set()
                worker.join(5)
                self.assertFalse(worker.is_alive())
