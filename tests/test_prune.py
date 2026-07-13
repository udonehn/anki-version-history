from __future__ import annotations

import json

import pytest
from anki.collection import Collection

from note_version_history import baseline, capture_notes, consts, db, prune
from note_version_history.appconfig import RetentionConfig
from note_version_history.blobstore import BlobStore
from note_version_history.capture_notes import list_note_versions

DAY_MS = 24 * 60 * 60 * 1000
NOW_MS = 1_800_000_000_000  # fixed fake clock for deterministic age tests


@pytest.fixture
def col(tmp_path):
    collection = Collection(str(tmp_path / "collection.anki2"))
    yield collection
    collection.close()


def insert_note_version(conn, nid, *, origin="auto", ts=NOW_MS, deleted=0, hash_="h"):
    cursor = conn.execute(
        "INSERT INTO note_versions"
        " (nid, guid, mid, ts, origin, op_label, fields, field_names, tags, hash, deleted)"
        " VALUES (?, 'g', 1, ?, ?, '', ?, ?, '[]', ?, ?)",
        (nid, ts, origin, json.dumps(["f"]), json.dumps(["Front"]), hash_, deleted),
    )
    return int(cursor.lastrowid)


def count_versions(conn, nid=None):
    if nid is None:
        return conn.execute("select count(*) from note_versions").fetchone()[0]
    return conn.execute(
        "select count(*) from note_versions where nid=?", (nid,)
    ).fetchone()[0]


def test_prune_keeps_newest_auto_and_caps_count(conn):
    # insertion order == capture order == id order; ts rises with it
    for index in range(10):
        insert_note_version(conn, 1, ts=NOW_MS - 9 + index)
    retention = RetentionConfig(max_auto_versions_per_note=3, max_age_days=0)

    pruned = prune.prune_note_versions(conn, retention, now_ms=NOW_MS)

    assert pruned == 7
    assert count_versions(conn, 1) == 3
    newest = conn.execute(
        "select max(ts) from note_versions where nid=1"
    ).fetchone()[0]
    assert newest == NOW_MS  # newest survived


def test_prune_age_cutoff_always_keeps_latest(conn):
    ancient = NOW_MS - 400 * DAY_MS
    for index in range(3):
        insert_note_version(conn, 1, ts=ancient + index)
    retention = RetentionConfig(max_auto_versions_per_note=100, max_age_days=180)

    pruned = prune.prune_note_versions(conn, retention, now_ms=NOW_MS)

    assert pruned == 2  # everything old except the note's newest auto row
    assert count_versions(conn, 1) == 1


def test_prune_never_touches_protected_origins(conn):
    ancient = NOW_MS - 400 * DAY_MS
    insert_note_version(conn, 1, origin="baseline", ts=ancient)
    insert_note_version(conn, 1, origin="manual", ts=ancient)
    insert_note_version(conn, 1, origin="restore", ts=ancient)
    insert_note_version(conn, 1, origin="auto", ts=ancient, deleted=1)  # marker
    insert_note_version(conn, 1, origin="auto", ts=ancient)  # newest auto

    retention = RetentionConfig(max_auto_versions_per_note=1, max_age_days=1)
    pruned = prune.prune_note_versions(conn, retention, now_ms=NOW_MS)

    assert pruned == 0
    assert count_versions(conn, 1) == 5


def test_media_prune_disabled_by_default(conn):
    conn.execute(
        "INSERT INTO media_events (fname, ts, origin, event, sha1, size)"
        " VALUES ('a.mp3', 1, 'auto', 'added', 'x', 1)"
    )
    assert prune.prune_media_events(conn, RetentionConfig(), now_ms=NOW_MS) == 0


def test_media_prune_keeps_last_event_per_file(conn):
    old = NOW_MS - 400 * DAY_MS
    for ts, event in ((old, "added"), (old + 1, "modified"), (old + 2, "modified")):
        conn.execute(
            "INSERT INTO media_events (fname, ts, origin, event, sha1, size)"
            " VALUES ('a.mp3', ?, 'auto', ?, 'x', 1)",
            (ts, event),
        )
    retention = RetentionConfig(media_max_age_days=180)

    pruned = prune.prune_media_events(conn, retention, now_ms=NOW_MS)

    assert pruned == 2
    remaining = conn.execute(
        "select count(*) from media_events where fname='a.mp3'"
    ).fetchone()[0]
    assert remaining == 1


def test_gc_blobs_spares_manifest_and_event_references(conn, tmp_path):
    blobs = BlobStore(tmp_path / "blobs")
    kept_event = blobs.put_bytes(b"event-referenced")
    kept_manifest = blobs.put_bytes(b"manifest-referenced")
    orphan = blobs.put_bytes(b"orphan")
    conn.execute(
        "INSERT INTO media_events (fname, ts, origin, event, sha1, size)"
        " VALUES ('a', 1, 'auto', 'added', ?, 1)",
        (kept_event,),
    )
    conn.execute(
        "INSERT INTO media_manifest (fname, sha1, size, mtime) VALUES ('b', ?, 1, 1)",
        (kept_manifest,),
    )

    removed = prune.gc_blobs(conn, blobs)

    assert removed == 1
    assert blobs.has(kept_event) and blobs.has(kept_manifest)
    assert not blobs.has(orphan)


def test_run_maintenance_stamps_marker_and_reports(conn, tmp_path):
    blobs = BlobStore(tmp_path / "blobs")
    for index in range(5):
        insert_note_version(conn, 1, ts=NOW_MS - index)
    retention = RetentionConfig(max_auto_versions_per_note=2)

    assert prune.maintenance_due(conn, now_ms=NOW_MS)
    report = prune.run_maintenance(conn, blobs, retention, now_ms=NOW_MS)

    assert report.notes_pruned == 3
    assert not prune.maintenance_due(conn, now_ms=NOW_MS)
    assert prune.maintenance_due(conn, now_ms=NOW_MS + prune.MAINTENANCE_INTERVAL_MS)


def test_full_vacuum_and_incremental_vacuum_run(conn):
    prune.incremental_vacuum(conn)
    prune.full_vacuum(conn)  # must not raise


def test_full_rescan_heals_corrupted_index(col, conn):
    notetype = col.models.by_name("Basic")
    note = col.new_note(notetype)
    note["Front"] = "truth"
    note["Back"] = "b"
    col.add_note(note, 1)
    baseline.run_notes_baseline(col, conn)
    assert count_versions(conn, int(note.id)) == 1

    # corrupt the cache: index claims different content than reality
    conn.execute(
        "UPDATE note_index SET latest_hash='corrupted' WHERE nid=?", (int(note.id),)
    )

    report = capture_notes.full_rescan(col, conn)

    assert report.captured == 1  # healed by re-capturing the real state
    versions = list_note_versions(conn, int(note.id))
    assert versions[0].fields[0] == "truth"
    # marker reset to the collection's real max mod (downward-capable)
    max_mod = int(col.db.scalar("select coalesce(max(mod),0) from notes"))
    assert db.meta_get_int(conn, consts.META_NOTE_SCAN_MARKER, -1) == max_mod


def test_full_rescan_detects_deletions_and_resets_count(col, conn):
    notetype = col.models.by_name("Basic")
    note = col.new_note(notetype)
    note["Front"] = "f"
    note["Back"] = "b"
    col.add_note(note, 1)
    baseline.run_notes_baseline(col, conn)

    col.remove_notes([note.id])
    report = capture_notes.full_rescan(col, conn)

    assert report.deleted == 1
    assert db.meta_get_int(conn, consts.META_LAST_NOTE_COUNT, -1) == 0
