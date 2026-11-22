"""Content-addressed transport for completed rollout envelopes that exceed one relay record."""

import json
import re

from handoff import MAX_FILE
from handoff_sync import atomic_write
from personal_relay import Changed, EMPTY, digest

CHUNK_SIZE = 4 * 1024 * 1024
# The native transcript uses the same limit as the existing session handoff.
# The envelope additionally carries a relative path and filename.
MAX_ENVELOPE = MAX_FILE + 64 * 1024
MANIFESTS = "session-manifests/"
CHUNKS = "session-chunks/"


def manifest_key(legacy_key):
    return MANIFESTS + legacy_key.removeprefix("sessions/")


def read_manifest(store, key, revision):
    raw = store.read(key)
    if digest(raw) != revision:
        raise Changed("分块清单刚刚更新，请稍后重试")
    try:
        spec = json.loads(raw)
        size, parts = spec["size"], spec["parts"]
        hashes = [spec["sha256"], *parts] if isinstance(parts, list) else []
        if (spec["v"] != 1 or type(size) is not int or not 0 < size <= MAX_ENVELOPE
                or not hashes or len(parts) != (size + CHUNK_SIZE - 1) // CHUNK_SIZE
                or not all(isinstance(h, str) and re.fullmatch("[0-9a-f]{64}", h) for h in hashes)):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise ValueError("会话分块清单无效") from None
    return spec


def checkpoint(stop):
    if stop.is_set():
        raise Changed("会话传输已中断，已验证的块可在下次继续使用")


def upload(store, index, key, previous, data, stop):
    session_id = key.rsplit("/", 1)[-1][-42:-6]
    parts = []
    for offset in range(0, len(data), CHUNK_SIZE):
        checkpoint(stop)
        block = data[offset:offset + CHUNK_SIZE]
        sha = digest(block)
        parts.append(sha)
        block_key = CHUNKS + sha
        current = index.get(block_key, EMPTY)
        if current != sha:
            if current != EMPTY:
                raise ValueError("服务器会话分块摘要不一致")
            try:
                store.put(block_key, EMPTY, block, session_id=session_id)
            except Changed:
                if digest(store.read(block_key)) != sha:
                    raise
            index[block_key] = sha
    checkpoint(stop)
    manifest = json.dumps({"v": 1, "size": len(data), "sha256": digest(data), "parts": parts},
                          separators=(",", ":")).encode("ascii")
    store.put(key, previous, manifest)
    index[key] = digest(manifest)


def download(store, spec, local, scratch, stop):
    result = bytearray()
    for number, sha in enumerate(spec["parts"]):
        checkpoint(stop)
        begin = number * CHUNK_SIZE
        length = min(CHUNK_SIZE, spec["size"] - begin)
        part = scratch / sha[:24]
        block = local[begin:begin + length] if local else None
        if digest(block) != sha:
            if part.is_file() and part.stat().st_size == length:
                block = part.read_bytes()
            if digest(block) != sha:
                block = store.read(CHUNKS + sha)
                if not isinstance(block, bytes) or len(block) != length or digest(block) != sha:
                    raise ValueError("会话分块缺失或校验失败，保留原历史")
                scratch.mkdir(parents=True, exist_ok=True)
                atomic_write(part, block)
        if len(block) != length:
            raise ValueError("会话分块长度无效")
        result.extend(block)
    if digest(result) != spec["sha256"]:
        raise ValueError("完整会话校验失败，保留原历史")
    return bytes(result)


def sync_large(mirror, index, legacy_key, path, stop, read_envelope, install_envelope, validate):
    key = manifest_key(legacy_key)
    revision = index.get(key, EMPTY)
    legacy_revision = index.get(legacy_key, EMPTY)
    cache_key = ("session-manifest", path)
    def stamp():
        if path.is_file():
            info = path.stat()
            return info.st_ino, info.st_size, info.st_mtime_ns
        return None

    before = stamp()
    if mirror.cache.get(cache_key) == (before, revision, legacy_revision):
        return
    local = read_envelope(path)
    remote = read_manifest(mirror.store, key, revision) if revision != EMPTY else None
    # Keep the retry cache inside normal Windows MAX_PATH even for long profile paths.
    # Cache hits are always verified against the full SHA-256 before use.
    scratch = mirror.backups.parent / "parts" / digest(key.encode())[:16]
    # Legacy clients may continue advancing the small record after promotion.
    # A fork there is visible to R38 and must never silently replace either copy.
    legacy = None
    if legacy_revision != EMPTY:
        legacy = mirror.store.read(legacy_key)
        if digest(legacy) != legacy_revision:
            raise Changed("旧格式会话刚刚更新")

    if remote is None or (local is not None and len(local) >= remote["size"]):
        if local is None:
            raise Changed("本机和服务器均缺少会话内容")
        if legacy and not local.startswith(legacy):
            raise Changed("会话与旧版历史分叉，已保留双方")
        if remote and digest(memoryview(local)[:remote["size"]]) != remote["sha256"]:
            raise Changed("会话发生分叉，两端版本均已保留")
        if not remote or len(local) > remote["size"]:
            validate(local)
            upload(mirror.store, index, key, revision, local, stop)
    else:
        incoming = download(mirror.store, remote, local, scratch, stop)
        if (local and not incoming.startswith(local)) or (legacy and not incoming.startswith(legacy)):
            raise Changed("会话发生分叉，两端版本均已保留")
        validate(incoming)
        checkpoint(stop)
        install_envelope(path, local, incoming, mirror.backups)
        before = stamp()
    if scratch.is_dir() and remote:
        for sha in set(remote["parts"]):
            (scratch / sha[:24]).unlink(missing_ok=True)
    mirror.cache[cache_key] = before, index.get(key, EMPTY), legacy_revision
