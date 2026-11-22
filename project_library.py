"""Discover local projects and connect their saved Codex conversations to the desktop."""

import configparser
import json
import stat
import uuid
from pathlib import Path

from handoff_sync import atomic_write
from code_sync import github_id
from history_labels import read_label
from personal_memory import NOTE_TYPES, safe_path
from plain_projects import EXCLUDED, INDEX as FOLDER_INDEX

SETTINGS = "project-library.json"
ENTRY_PREFIX = "project:"
MARKERS = {
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "CMakeLists.txt",
    "pom.xml",
    "README.md",
    "AGENTS.md",
    "requirements.txt",
}


def configured_root(data):
    try:
        folder = json.loads((data / SETTINGS).read_bytes())["root"]
        if folder:
            path = Path(folder)
            if path.is_absolute():
                return path
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def set_project_root(data, folder):
    payload = json.dumps({"root": str(folder.resolve()) if folder else None}, ensure_ascii=False).encode()
    atomic_write(data / SETTINGS, payload)


def folders(parent):
    try:
        return sorted(
            child
            for child in parent.iterdir()
            if not child.name.startswith(".")
            and child.name.casefold() not in EXCLUDED
            and child.is_dir()
            and not child.is_symlink()
            and not getattr(child.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
    except OSError:
        return []


def inspect_project(path, overrides):
    try:
        filenames = {p.name for p in path.iterdir()}
    except OSError:
        return None
    if not (
        filenames.intersection(MARKERS)
        or any(name.endswith((".sln", ".slnx", ".code-workspace")) for name in filenames)
        or (path / ".codex" / "memories").is_dir()
        or (path / FOLDER_INDEX).is_file()
    ):
        return None
    branch = repository = ""
    git = path / '.git'
    try:
        if git.is_file():
            with git.open(encoding='utf-8') as stream:
                line = stream.readline(4096).strip()
            if line.startswith('gitdir: '):
                git = path / line.removeprefix('gitdir: ')
        with (git / 'HEAD').open(encoding='utf-8') as stream:
            head = stream.readline(1024).strip()
        branch = head.removeprefix('ref: refs/heads/') if head.startswith('ref: refs/heads/') else ''
    except (OSError, UnicodeError):
        pass
    try:
        config_dir = git
        common = git / 'commondir'
        if common.is_file():
            with common.open(encoding='utf-8') as stream:
                config_dir = git / stream.readline(4096).strip()
        config = configparser.ConfigParser(interpolation=None, strict=False)
        with (config_dir / 'config').open(encoding='utf-8') as stream:
            config.read_string(stream.read(65536))
        repository = github_id(config.get('remote "origin"', 'url', fallback='')) or ''
    except (OSError, UnicodeError, configparser.Error):
        pass
    memory = ""
    notes = []
    try:
        directory = safe_path(path, overrides.get(path, ".codex/memories"))
        if directory.is_dir():
            memory = str(directory)
            notes = sorted(
                file.name
                for file in directory.iterdir()
                if not file.name.startswith(".")
                and file.suffix.lower() in NOTE_TYPES
                and file.is_file()
                and not file.is_symlink()
            )[:10]
    except (OSError, ValueError, TypeError):
        pass
    return {
        "id": ENTRY_PREFIX + str(path),
        "cwd": str(path),
        "title": path.name,
        "memory": memory,
        "notes": notes,
        "branch": branch,
        "githubProject": repository,
    }


def discover(parent, overrides):
    from plain_git import managed

    def scan(path):
        project = inspect_project(path, overrides)
        git = path / ".git"
        if project and path != parent and (not git.exists() or has_git_history(git) or managed(path)):
            return {path: project}
        children = {}
        for child in folders(path):
            children.update(scan(child))
        return children or ({path: project} if project else {})

    return scan(parent)


def has_git_history(git):
    # Keep embedded checkouts and test fixtures inside an established project out of the library.
    if git.is_file():
        return True
    if any((git / name).exists() for name in ("packed-refs", "reftable")):
        return True
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if head and not head.startswith("ref:"):
            return True
        return any((git / "refs" / "heads").iterdir())
    except (OSError, UnicodeError):
        return False


class ProjectLibrary:
    def __init__(self, data, home):
        self.data = data
        self.home = home
        self.headers = {}

    def snapshot(self):
        root = configured_root(self.data)
        overrides = {}
        try:
            entries = json.loads((self.data / "personal-sync.json").read_bytes()).get("projects", [])
            overrides = {
                Path(row["path"]): row["memoryPath"]
                for row in entries
                if row.get("path") and row.get("memoryPath")
            }
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        projects = discover(root, overrides) if root else {}
        mapped = {}
        for directory in projects:
            try:
                marker = safe_path(directory, FOLDER_INDEX)
                if marker.is_file():
                    for tid, entry in json.loads(marker.read_bytes()).items():
                        location = directory if entry["cwd"] == "." else safe_path(directory, entry["cwd"])
                        mapped[tid] = location, directory
            except (OSError, ValueError, AttributeError, TypeError, KeyError):
                continue
        rows = []
        cache = {}
        for file in (self.home / "sessions").rglob("rollout-*.jsonl"):
            try:
                info = file.stat()
                stamp = info.st_mtime_ns, info.st_size
            except OSError:
                continue
            old = self.headers.get(file)
            label = old[1] if old and old[0] == stamp else read_label(file)
            cache[file] = stamp, label
            if not label:
                continue
            thread, fields = label
            try:
                if str(uuid.UUID(thread)) != thread:
                    continue
            except ValueError:
                continue
            directory = fields["cwd"]
            path = Path(directory)
            parent = next((part for part in (path, *path.parents) if part in projects), None) if directory else None
            row = {"id": thread, **fields}
            if thread in mapped:
                location, project = mapped[thread]
                row.update(cwd=str(location), projectRoot=str(project))
            elif parent:
                row.update(cwd=str(parent), projectRoot=str(parent))
            rows.append(row)
        self.headers = cache
        return [*projects.values(), *rows]

    def run(self, stop, publish):
        last = None
        while not stop.is_set():
            try:
                items = self.snapshot()
                if items != last:
                    publish(indexedProjects=items)
                    last = items
            except (OSError, ValueError):
                pass
            stop.wait(5)
