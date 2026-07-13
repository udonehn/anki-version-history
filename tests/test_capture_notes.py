from __future__ import annotations

import pytest
from anki.collection import Collection

from note_version_history import baseline, capture_notes, consts, db, hashing
from note_version_history.capture_notes import (
    BeforeState,
    NoteScanContext,
    list_note_versions,
)


@pytest.fixture
def col(tmp_path):
    collection = Collection(str(tmp_path / "collection.anki2"))
    yield collection
    collection.close()


def add_note(col: Collection, front: str = "f", back: str = "b", tags: tuple = ()):
    notetype = col.models.by_name("Basic")
    note = col.new_note(notetype)
    note["Front"] = front
    note["Back"] = back
    note.tags = list(tags)
    col.add_note(note, 1)
    return note


def scan(col, conn, **ctx_kwargs):
    return capture_notes.scan_notes(col, conn, NoteScanContext(**ctx_kwargs))


def test_baseline_then_edit_captures_auto_version(col, conn):
    note = add_note(col, front="original")
    baseline.run_notes_baseline(col, conn)

    fresh = col.get_note(note.id)
    fresh["Front"] = "edited"
    col.update_note(fresh)

    report = scan(col, conn, op_label="Update note")
    assert report.captured == 1
    assert not report.interrupted

    versions = list_note_versions(conn, note.id)
    assert len(versions) == 2  # baseline + auto
    assert versions[0].origin == consts.ORIGIN_AUTO
    assert versions[0].op_label == "Update note"
    assert versions[0].fields[0] == "edited"
    assert versions[0].field_names[0] == "Front"
    assert versions[1].origin == consts.ORIGIN_BASELINE


def test_unchanged_content_is_not_recaptured(col, conn):
    note = add_note(col)
    baseline.run_notes_baseline(col, conn)

    # touch without content change (bumps mod, same hash)
    fresh = col.get_note(note.id)
    col.update_note(fresh)

    assert scan(col, conn).captured == 0
    assert len(list_note_versions(conn, note.id)) == 1


def test_tag_reorder_is_not_a_new_version(col, conn):
    note = add_note(col, tags=("beta", "alpha"))
    baseline.run_notes_baseline(col, conn)

    fresh = col.get_note(note.id)
    fresh.tags = list(reversed(fresh.tags))
    col.update_note(fresh)

    assert scan(col, conn).captured == 0


def test_note_added_after_baseline_is_captured(col, conn):
    add_note(col)
    baseline.run_notes_baseline(col, conn)

    new_note = add_note(col, front="brand new")
    report = scan(col, conn)

    assert report.captured == 1
    versions = list_note_versions(conn, new_note.id)
    assert len(versions) == 1
    assert versions[0].origin == consts.ORIGIN_AUTO


def test_bulk_edit_chunked_scan_with_crash_resume(col, conn):
    notes = [add_note(col, front=f"note {i}") for i in range(30)]
    baseline.run_notes_baseline(col, conn)

    for note in notes:
        fresh = col.get_note(note.id)
        fresh["Front"] = fresh["Front"] + " v2"
        col.update_note(fresh)

    # stop after the first chunk commit (should_stop checked per chunk)
    calls = {"n": 0}

    def stop_after_first_chunk() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    first = scan(col, conn, chunk_size=10, should_stop=stop_after_first_chunk)
    assert first.interrupted
    assert first.captured == 10

    second = scan(col, conn, chunk_size=10)
    assert not second.interrupted
    assert second.captured == 20  # remaining notes; committed chunk not re-captured

    for note in notes:
        versions = list_note_versions(conn, note.id)
        assert len(versions) == 2, "exactly baseline + one auto version, no dupes"


def test_undo_reverted_content_is_recorded(col, conn):
    note = add_note(col, front="original")
    baseline.run_notes_baseline(col, conn)

    fresh = col.get_note(note.id)
    fresh["Front"] = "edited"
    col.update_note(fresh)
    assert scan(col, conn).captured == 1

    col.undo()  # revert the edit — backend may rewind notes.mod (C1)

    plain = scan(col, conn)
    recheck = scan(
        col, conn, saw_undo=True, session_touched_nids=frozenset({int(note.id)}),
        op_label="Undo: Update Note",
    )
    # Exactly one of the two scans captures the reverted state, regardless of
    # whether the backend rewinds mod (C1 assumption) or advances it.
    assert plain.captured + recheck.captured == 1

    versions = list_note_versions(conn, note.id)
    assert len(versions) == 3
    assert versions[0].fields[0] == "original"


def test_deletion_marker_and_resurrection(col, conn):
    note = add_note(col, front="to be deleted")
    baseline.run_notes_baseline(col, conn)
    nid = int(note.id)

    col.remove_notes([note.id])
    report = scan(col, conn)
    assert report.deleted == 1

    versions = list_note_versions(conn, nid)
    assert versions[0].deleted is True
    alive = conn.execute("select alive from note_index where nid=?", (nid,)).fetchone()[0]
    assert alive == 0

    col.undo()  # bring the note back with its ORIGINAL mod
    revive = scan(col, conn, saw_undo=True, session_touched_nids=frozenset({nid}))
    assert revive.captured + revive.resurrected == 1

    versions = list_note_versions(conn, nid)
    assert versions[0].deleted is False
    assert versions[0].fields[0] == "to be deleted"
    alive = conn.execute("select alive from note_index where nid=?", (nid,)).fetchone()[0]
    assert alive == 1


def test_excluded_notetype_is_not_captured(col, conn):
    note = add_note(col)
    baseline.run_notes_baseline(col, conn)
    basic_mid = int(col.models.by_name("Basic")["id"])

    fresh = col.get_note(note.id)
    fresh["Front"] = "changed"
    col.update_note(fresh)

    report = scan(col, conn, exclude_mids=frozenset({basic_mid}))
    assert report.captured == 0
    assert len(list_note_versions(conn, note.id)) == 1  # baseline only


def test_manual_snapshot_bypasses_dedupe(col, conn):
    note = add_note(col)
    baseline.run_notes_baseline(col, conn)

    first = capture_notes.snapshot_notes(col, conn, [note.id], op_label="Manual")
    second = capture_notes.snapshot_notes(col, conn, [note.id], op_label="Manual")
    assert first == second == 1

    versions = list_note_versions(conn, note.id)
    assert len(versions) == 3  # baseline + two identical manual snapshots
    assert versions[0].origin == consts.ORIGIN_MANUAL
    assert versions[0].hash == versions[1].hash


def test_lazy_baseline_from_before_state(col, conn):
    # no install baseline was taken; only the editor-load cache exists
    note = add_note(col, front="original", back="b")
    before = BeforeState(
        ts=1000,
        guid=note.guid,
        mid=int(note.mid),
        fields=("original", "b"),
        field_names=("Front", "Back"),
        tags=(),
        hash=hashing.note_hash(int(note.mid), ["original", "b"], []),
    )

    fresh = col.get_note(note.id)
    fresh["Front"] = "edited"
    col.update_note(fresh)

    report = scan(col, conn, before_states={int(note.id): before})
    assert report.captured == 1  # the after-state (baseline is an extra row)

    versions = list_note_versions(conn, note.id)
    assert len(versions) == 2
    assert versions[1].origin == consts.ORIGIN_BASELINE  # older = pre-edit
    assert versions[1].fields[0] == "original"
    assert versions[1].ts == 1000  # keeps the editor-load timestamp
    assert versions[0].origin == consts.ORIGIN_AUTO  # newer = after
    assert versions[0].fields[0] == "edited"


def test_scan_ignores_notes_below_marker(col, conn):
    # Lazy install sets the marker to the collection's current max mod so the
    # pre-existing collection is NOT captured wholesale — only later edits are.
    for index in range(3):
        add_note(col, front=f"pre {index}")
    max_mod = int(col.db.scalar("select coalesce(max(mod), 0) from notes"))
    db.meta_set(conn, consts.META_NOTE_SCAN_MARKER, str(max_mod + 1))
    db.meta_set(conn, consts.META_LAST_NOTE_COUNT, "3")

    scan(col, conn)
    assert conn.execute("select count(*) from note_versions").fetchone()[0] == 0


def test_no_before_state_means_no_baseline(col, conn):
    # bulk/sync/never-opened notes: first change has no recoverable "before"
    note = add_note(col, front="original")
    fresh = col.get_note(note.id)
    fresh["Front"] = "edited"
    col.update_note(fresh)

    assert scan(col, conn).captured == 1
    versions = list_note_versions(conn, note.id)
    assert len(versions) == 1
    assert versions[0].origin == consts.ORIGIN_AUTO


def test_before_state_ignored_once_note_has_history(col, conn):
    note = add_note(col, front="original")
    baseline.run_notes_baseline(col, conn)  # note already has a baseline
    before = BeforeState(
        ts=1, guid=note.guid, mid=int(note.mid), fields=("stale",),
        field_names=("Front",), tags=(), hash="stalehash",
    )

    fresh = col.get_note(note.id)
    fresh["Front"] = "edited"
    col.update_note(fresh)

    scan(col, conn, before_states={int(note.id): before})
    versions = list_note_versions(conn, note.id)
    # baseline(run_notes_baseline) + auto(edit); the stale before-state is NOT
    # injected because the note already had history
    assert [v.origin for v in versions] == [consts.ORIGIN_AUTO, consts.ORIGIN_BASELINE]
    assert all(v.fields[0] != "stale" for v in versions)


def test_marker_is_high_water_and_persisted(col, conn):
    add_note(col)
    baseline.run_notes_baseline(col, conn)
    marker_after_baseline = db.meta_get_int(conn, consts.META_NOTE_SCAN_MARKER, 0)
    assert marker_after_baseline > 0

    scan(col, conn)
    marker_after_scan = db.meta_get_int(conn, consts.META_NOTE_SCAN_MARKER, 0)
    assert marker_after_scan >= marker_after_baseline
