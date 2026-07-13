from __future__ import annotations

import hashlib

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

    removed = store.gc(referenced={keep})

    assert removed == 2
    assert store.has(keep)
    assert not store.has(drop1)
    assert not store.has(drop2)


def test_stats_empty(store):
    stats = store.stats()
    assert stats.count == 0
    assert stats.total_bytes == 0
