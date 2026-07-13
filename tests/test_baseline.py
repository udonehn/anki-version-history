from __future__ import annotations

import pytest
from anki.collection import Collection

from note_version_history import baseline, consts, db


@pytest.fixture
def col(tmp_path):
    collection = Collection(str(tmp_path / "collection.anki2"))
    yield collection
    collection.close()


def add_notes(col: Collection, count: int) -> list[int]:
    nids = []
    notetype = col.models.by_name("Basic")
    for i in range(count):
        note = col.new_note(notetype)
        note["Front"] = f"front {i}"
        note["Back"] = f"back {i}"
        col.add_note(note, 1)
        nids.append(int(note.id))
    return nids


def test_estimate_reports_exact_numbers(col, conn):
    add_notes(col, 3)
    numbers = baseline.estimate(col)
    assert numbers["note_count"] == 3
    assert numbers["field_bytes"] > 0
    assert numbers["notetype_count"] >= 1


def test_full_baseline_captures_everything(col, conn):
    nids = add_notes(col, 5)

    captured = baseline.run_notes_baseline(col, conn)

    assert captured == 5
    assert baseline.notes_baseline_done(conn)
    row_count = conn.execute(
        "select count(*) from note_versions where origin='baseline'"
    ).fetchone()[0]
    assert row_count == 5
    notetype_rows = conn.execute("select count(*) from notetype_versions").fetchone()[0]
    assert notetype_rows >= 1
    # marker/count initialized so auto-capture takes over cleanly
    assert db.meta_get_int(conn, consts.META_NOTE_SCAN_MARKER, 0) > 0
    assert db.meta_get_int(conn, consts.META_LAST_NOTE_COUNT, -1) == 5
    assert set(nids) == {
        row[0] for row in conn.execute("select nid from note_index").fetchall()
    }


def test_baseline_resumes_after_interruption_without_dupes(col, conn):
    add_notes(col, 25)

    calls = {"n": 0}

    def stop_after_first_chunk() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    partial = baseline.run_notes_baseline(
        col, conn, chunk_size=10, should_stop=stop_after_first_chunk
    )
    assert partial == 10
    assert not baseline.notes_baseline_done(conn)
    assert int(baseline.get_state(conn)["notes_cursor"]) > 0

    rest = baseline.run_notes_baseline(col, conn, chunk_size=10)
    assert rest == 15
    assert baseline.notes_baseline_done(conn)

    dupes = conn.execute(
        "select count(*) from (select nid from note_versions group by nid having count(*) > 1)"
    ).fetchone()[0]
    assert dupes == 0


def test_baseline_is_idempotent_when_done(col, conn):
    add_notes(col, 2)
    assert baseline.run_notes_baseline(col, conn) == 2
    assert baseline.run_notes_baseline(col, conn) == 0


def test_progress_callback_reports_totals(col, conn):
    add_notes(col, 12)
    seen: list[tuple[int, int]] = []

    baseline.run_notes_baseline(
        col, conn, chunk_size=5, progress=lambda done, total: seen.append((done, total))
    )

    assert seen[-1] == (12, 12)
    assert all(total == 12 for _done, total in seen)
