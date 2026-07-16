from __future__ import annotations

import hashlib
import os
import time

import pytest

from note_version_history.blobstore import BlobStore


@pytest.fixture
def store(tmp_path):
    return BlobStore(tmp_path / "blobs")


def test_put_bytes_roundtrip(store):
    sha1 = store.put_bytes(b"hello")
    assert sha1 == hashlib.sha1(b"hello").hexdigest()
    assert store.has(sha1)
    assert store.read_bytes(sha1) == b"hello"
    # sharded layout: <root>/<sha1[:2]>/<sha1>
    assert store.path_for(sha1).parent.name == sha1[:2]


def test_put_file_streams_and_dedupes(store, tmp_path):
    payload = b"media-bytes" * 10_000
    file_a = tmp_path / "a.mp3"
    file_b = tmp_path / "b.mp3"
    file_a.write_bytes(payload)
    file_b.write_bytes(payload)  # identical content, different name

    sha_a, size_a = store.put_file(file_a)
    sha_b, size_b = store.put_file(file_b)

    assert sha_a == sha_b == hashlib.sha1(payload).hexdigest()
    assert size_a == size_b == len(payload)
    stats = store.stats()
    assert stats.count == 1  # stored exactly once
    assert stats.total_bytes == len(payload)


def test_read_missing_blob_raises(store):
    with pytest.raises(FileNotFoundError):
        store.read_bytes("0" * 40)


def test_gc_removes_only_unreferenced(store):
    keep = store.put_bytes(b"keep-me")
    drop1 = store.put_bytes(b"drop-1")
    drop2 = store.put_bytes(b"drop-2")

    # min_age_ms=0 disables the freshness guard so this pins pure ref-diffing
    removed = store.gc(referenced={keep}, min_age_ms=0)

    assert removed == 2
    assert store.has(keep)
    assert not store.has(drop1)
    assert not store.has(drop2)


def _backdate(path, seconds: float = 7_200) -> None:
    stale = time.time() - seconds
    os.utime(path, (stale, stale))


def test_gc_age_guard_spares_fresh_orphans_and_sweeps_stale_tmps(store):
    fresh_orphan = store.put_bytes(b"fresh-orphan")  # in-flight scan's blob
    old_orphan = store.put_bytes(b"old-orphan")
    _backdate(store.path_for(old_orphan))
    root = store.path_for(old_orphan).parent.parent
    shard_dir = store.path_for(old_orphan).parent
    fresh_tmp = root / ".tmp-fresh"
    fresh_tmp.write_bytes(b"x")
    old_root_tmp = root / ".tmp-old-root"
    old_root_tmp.write_bytes(b"x")
    _backdate(old_root_tmp)
    old_shard_tmp = shard_dir / ".tmp-old-shard"
    old_shard_tmp.write_bytes(b"x")
    _backdate(old_shard_tmp)

    removed = store.gc(referenced=set())  # default one-hour age guard

    assert removed == 3  # old orphan + both stale tmps
    assert store.has(fresh_orphan)  # young blob spared (event may commit soon)
    assert not store.has(old_orphan)
    assert fresh_tmp.exists()
    assert not old_root_tmp.exists()
    assert not old_shard_tmp.exists()


def test_copy_to_streams_blob(store, tmp_path):
    sha1 = store.put_bytes(b"stream-me")
    dest = tmp_path / "restored.bin"

    size = store.copy_to(sha1, dest)

    assert size == len(b"stream-me")
    assert dest.read_bytes() == b"stream-me"
    with pytest.raises(FileNotFoundError):
        store.copy_to("0" * 40, tmp_path / "never-written.bin")
    assert not (tmp_path / "never-written.bin").exists()


def test_stats_empty(store):
    stats = store.stats()
    assert stats.count == 0
    assert stats.total_bytes == 0
