from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from anki.collection import Collection

from note_version_history import baseline, consts
from note_version_history.blobstore import BlobStore
from note_version_history.capture_media import (
    capture_files_for_notes,
    full_scan,
    list_media_events,
    list_media_files,
    media_stats,
    restore_media_file,
)


@pytest.fixture
def col(tmp_path):
    collection = Collection(str(tmp_path / "collection.anki2"))
    yield collection
    collection.close()


@pytest.fixture
def blobs(tmp_path):
    return BlobStore(tmp_path / "blobs")


def media_dir(col: Collection) -> Path:
    path = Path(col.media.dir())
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_media(col: Collection, name: str, data: bytes, mtime: float | None = None) -> Path:
    path = media_dir(col) / name
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_full_scan_adds_files_and_dedupes_blobs(col, conn, blobs):
    write_media(col, "a.mp3", b"same-bytes")
    write_media(col, "b.mp3", b"same-bytes")  # identical content
    write_media(col, "c.png", b"image")

    report = full_scan(col, conn, blobs, origin=consts.ORIGIN_BASELINE)

    assert (report.added, report.modified, report.deleted) == (3, 0, 0)
    assert blobs.stats().count == 2  # identical files share one blob
    assert {f for f, _e, _t in list_media_files(conn)} == {"a.mp3", "b.mp3", "c.png"}


def test_unchanged_rescan_is_silent(col, conn, blobs):
    write_media(col, "a.mp3", b"data")
    full_scan(col, conn, blobs)

    report = full_scan(col, conn, blobs)
    assert (report.added, report.modified, report.deleted) == (0, 0, 0)


def test_modified_file_gets_event_and_new_blob(col, conn, blobs):
    base_time = time.time() - 100
    write_media(col, "a.mp3", b"v1", mtime=base_time)
    full_scan(col, conn, blobs)

    write_media(col, "a.mp3", b"v2-different", mtime=base_time + 50)
    report = full_scan(col, conn, blobs)

    assert report.modified == 1
    events = list_media_events(conn, "a.mp3")
    assert [e.event for e in events] == [consts.EVENT_MODIFIED, consts.EVENT_ADDED]
    assert blobs.stats().count == 2  # both versions kept


def test_metadata_only_change_updates_manifest_without_event(col, conn, blobs):
    base_time = time.time() - 100
    write_media(col, "a.mp3", b"stable", mtime=base_time)
    full_scan(col, conn, blobs)

    os.utime(media_dir(col) / "a.mp3", (base_time + 60, base_time + 60))  # touch only
    report = full_scan(col, conn, blobs)

    assert (report.added, report.modified, report.deleted) == (0, 0, 0)
    assert len(list_media_events(conn, "a.mp3")) == 1


def test_deleted_file_event_preserves_last_content(col, conn, blobs):
    write_media(col, "gone.png", b"precious-pixels")
    full_scan(col, conn, blobs)

    (media_dir(col) / "gone.png").unlink()
    report = full_scan(col, conn, blobs)

    assert report.deleted == 1
    latest = list_media_events(conn, "gone.png")[0]
    assert latest.event == consts.EVENT_DELETED
    assert blobs.read_bytes(latest.sha1) == b"precious-pixels"  # restorable
    manifest_row = conn.execute(
        "select 1 from media_manifest where fname='gone.png'"
    ).fetchone()
    assert manifest_row is None


def test_interrupted_scan_never_emits_false_deletions(col, conn, blobs):
    for index in range(6):
        write_media(col, f"f{index}.png", f"data{index}".encode())
    calls = {"n": 0}

    def stop_after_first_chunk() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    report = full_scan(col, conn, blobs, chunk_size=2, should_stop=stop_after_first_chunk)
    assert report.interrupted
    assert report.deleted == 0  # unvisited files must not be misread as deleted

    resumed = full_scan(col, conn, blobs, chunk_size=2)
    assert not resumed.interrupted
    assert resumed.added == 4  # remaining files; first chunk not re-added


def test_targeted_capture_for_changed_notes(col, conn, blobs):
    write_media(col, "ref.png", b"referenced")
    write_media(col, "unref.png", b"unreferenced")

    notetype = col.models.by_name("Basic")
    note = col.new_note(notetype)
    note["Front"] = 'look <img src="ref.png">'
    note["Back"] = "b"
    col.add_note(note, 1)

    written = capture_files_for_notes(col, conn, blobs, [int(note.id)])

    assert written == 1
    assert [e.fname for e in list_media_events(conn, "ref.png")] == ["ref.png"]
    assert list_media_events(conn, "unref.png") == []  # untouched by targeted pass


def test_restore_media_file_round_trip(col, conn, blobs):
    base_time = time.time() - 100
    write_media(col, "a.mp3", b"v1", mtime=base_time)
    full_scan(col, conn, blobs)
    v1_sha = list_media_events(conn, "a.mp3")[0].sha1

    write_media(col, "a.mp3", b"v2-content", mtime=base_time + 50)
    full_scan(col, conn, blobs)

    restore_media_file(col, conn, blobs, "a.mp3", v1_sha)

    assert (media_dir(col) / "a.mp3").read_bytes() == b"v1"
    events = list_media_events(conn, "a.mp3")
    assert events[0].origin == consts.ORIGIN_RESTORE
    # v2 is still restorable after restoring v1 (pre-restore state kept)
    v2_sha = events[1].sha1 if events[1].origin != consts.ORIGIN_RESTORE else events[2].sha1
    assert blobs.has(v2_sha)


def test_restore_recreates_deleted_file(col, conn, blobs):
    write_media(col, "gone.png", b"bytes")
    full_scan(col, conn, blobs)
    sha1 = list_media_events(conn, "gone.png")[0].sha1

    (media_dir(col) / "gone.png").unlink()
    full_scan(col, conn, blobs)

    restore_media_file(col, conn, blobs, "gone.png", sha1)
    assert (media_dir(col) / "gone.png").read_bytes() == b"bytes"
    # next scan sees it as already known — no spurious events
    report = full_scan(col, conn, blobs)
    assert (report.added, report.modified, report.deleted) == (0, 0, 0)


def test_restore_rejects_unsafe_names(col, conn, blobs):
    sha1 = blobs.put_bytes(b"x")
    for bad in ("../evil.png", "a/b.png", "a\\b.png", "c:d.png", ""):
        with pytest.raises(ValueError):
            restore_media_file(col, conn, blobs, bad, sha1)


def test_media_stats_and_denylist(col, conn, blobs):
    write_media(col, "a.png", b"12345")
    write_media(col, "Thumbs.db", b"junk")
    write_media(col, ".hidden", b"junk")

    count, total = media_stats(media_dir(col))
    assert count == 1
    assert total == 5


def test_media_baseline_resumes_without_dupes(col, conn, blobs):
    for index in range(10):
        write_media(col, f"m{index}.png", f"payload{index}".encode())

    calls = {"n": 0}

    def stop_after_first_chunk() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    first = baseline.run_media_baseline(
        col, conn, blobs, chunk_size=4, should_stop=stop_after_first_chunk
    )
    assert first == 4
    assert baseline.media_baseline_state(conn) == baseline.STATE_PENDING

    rest = baseline.run_media_baseline(col, conn, blobs, chunk_size=4)
    assert rest == 6
    assert baseline.media_baseline_state(conn) == baseline.STATE_DONE

    total_events = conn.execute("select count(*) from media_events").fetchone()[0]
    assert total_events == 10  # no duplicates

    assert baseline.run_media_baseline(col, conn, blobs) == 0  # idempotent


def test_skip_media_baseline(col, conn, blobs):
    baseline.skip_media_baseline(conn)
    assert baseline.media_baseline_state(conn) == baseline.STATE_SKIPPED
    assert baseline.run_media_baseline(col, conn, blobs) == 0


def test_estimate_media(col, conn):
    write_media(col, "a.png", b"12345678")
    numbers = baseline.estimate_media(col)
    assert numbers["file_count"] == 1
    assert numbers["total_bytes"] == 8
