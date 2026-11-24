"""python check.py A|B <submission or snapshot directory>"""
import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run_a(src, tmp):
    cfg = tmp / "user.json"
    work = "desk-42"
    new = dict(provider="deskbell", workspace=work, topic="task", renderer="toast-v3", label="任务提醒")
    v1 = dict(owner="deskbell", project=work, event="task", label="Inbox")
    v2 = dict(new, renderer="toast-v1", label="收件箱", sound=False)
    others = [dict(v2, provider="planner"), dict(v2, workspace="Desk-42"),
              dict(v2, topic="calendar"), {"metadata": [1, 2]}]

    def command(action):
        p = subprocess.run([sys.executable, str(src / "deskbell.py"), str(cfg), work, action],
                           capture_output=True, timeout=6, check=True)
        return p.stdout.decode().strip()

    def seed(rows):
        cfg.write_text(json.dumps({"extra": ["retain"], "subscriptions": rows}), encoding="utf-8-sig")

    def check_config(rows):
        data = json.loads(cfg.read_text(encoding="utf-8-sig"))
        assert data["extra"] == ["retain"]
        assert [r for r in data["subscriptions"] if r in others] == others
        assert len(data["subscriptions"]) == len(rows)
        assert all(data["subscriptions"].count(r) == rows.count(r) for r in rows)

    def migration():
        for old in [v1, v2]:
            seed([old, *others])
            assert command("status") == "outdated"
            command("enable")
            check_config([*others, new])
            assert command("status") == "current"

    def mixed():
        seed([others[0], new, v1, *others[1:], new, v2])
        assert command("status") == "outdated"
        command("enable")
        check_config([*others, new])

    def disable():
        seed([v2, others[0], v1, new, new, *others[1:]])
        command("disable")
        check_config(others)
        assert command("status") == "missing"

    def stable():
        for rows, action, expected in [([new, *others], "enable", "current"), (others, "disable", "missing")]:
            seed(rows)
            original = cfg.read_bytes()
            assert command("status") == expected
            command(action)
            assert cfg.read_bytes() == original

    return [("A1", migration), ("A2", mixed), ("A3", disable), ("A4", stable)]


def run_b(src, tmp):
    spec = importlib.util.spec_from_file_location("candidate_exporter", src / "exporter.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dest = tmp / "out.jsonl"
    def row(i, time, hidden=False):
        return dict(id=i, updated=time, archived=hidden)
    def ids():
        return [json.loads(line)["id"] for line in dest.read_text(encoding="utf-8").splitlines()]
    def boundary():
        module.export([row("c", 2), row("b", 1), row("a", 1)], dest, 1)
        assert ids() == ["a", "b", "c"], ids()
    def previous():
        dest.write_bytes(b"previous\n")
        try:
            module.export([row("a", 1), dict(row("b", 2), value={1})], dest, 1)
        except TypeError:
            pass
        else:
            raise AssertionError("encoding error was not reported")
        assert dest.read_bytes() == b"previous\n", dest.read_bytes()
    def empty_page():
        module.export([row("a", 1, True), row("b", 2)], dest, 1)
        assert ids() == ["b"]
    return [("B1", boundary), ("B2", previous), ("B3", empty_page)]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=["A", "B"])
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    failed = 0
    with tempfile.TemporaryDirectory() as directory:
        suite = run_a if args.task == "A" else run_b
        for label, case in suite(args.source.resolve(), Path(directory)):
            try:
                case()
            except Exception as exc:
                failed += 1
                print(f"{label} FAIL {type(exc).__name__} {exc}")
            else:
                print(f"{label} PASS")
    sys.exit(bool(failed))
