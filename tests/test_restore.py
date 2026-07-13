from __future__ import annotations

from dataclasses import replace

import pytest
from anki.collection import Collection
from anki.errors import NotFoundError

from note_version_history import baseline, capture_notes, capture_notetypes, restore
from note_version_history.capture_notes import NoteScanContext, list_note_versions
from note_version_history.capture_notetypes import list_notetype_versions


@pytest.fixture
def col(tmp_path):
    collection = Collection(str(tmp_path / "collection.anki2"))
    yield collection
    collection.close()


def add_note(col: Collection, front="f", back="b", tags=()):
    notetype = col.models.by_name("Basic")
    note = col.new_note(notetype)
    note["Front"] = front
    note["Back"] = back
    note.tags = list(tags)
    col.add_note(note, 1)
    return note


def edit(col, nid, **fields):
    note = col.get_note(nid)
    for name, value in fields.items():
        note[name] = value
    col.update_note(note)


def scan(col, conn):
    return capture_notes.scan_notes(col, conn, NoteScanContext())


def test_full_restore_reverts_fields_and_tags_and_is_undoable(col, conn):
    note = add_note(col, front="v1 front", back="v1 back", tags=("old-tag",))
    baseline.run_notes_baseline(col, conn)

    fresh = col.get_note(note.id)
    fresh["Front"] = "v2 front"
    fresh.tags = ["new-tag"]
    col.update_note(fresh)
    scan(col, conn)

    old_version = list_note_versions(conn, note.id)[-1]  # baseline row
    result = restore.apply_note_version(col, old_version, None, "Restore note version")

    live = col.get_note(note.id)
    assert live["Front"] == "v1 front"
    assert live.tags == ["old-tag"]
    assert result.applied_fields == ("Front", "Back")
    assert result.skipped_fields == ()
    # merged into ONE named undo step…
    assert col.undo_status().undo == "Restore note version"
    # …and undoing it brings the edited state back
    col.undo()
    assert col.get_note(note.id)["Front"] == "v2 front"


def test_selective_field_restore_leaves_other_fields_and_tags(col, conn):
    note = add_note(col, front="old front", back="old back", tags=("keep",))
    baseline.run_notes_baseline(col, conn)

    edit(col, note.id, Front="new front", Back="new back")
    fresh = col.get_note(note.id)
    fresh.tags = ["changed"]
    col.update_note(fresh)
    scan(col, conn)

    old_version = list_note_versions(conn, note.id)[-1]
    restore.apply_note_version(col, old_version, {"Back"}, "Restore field")

    live = col.get_note(note.id)
    assert live["Back"] == "old back"  # restored
    assert live["Front"] == "new front"  # untouched
    assert live.tags == ["changed"]  # tags untouched on selective restore


def test_restore_matches_fields_by_name_after_rename(col, conn):
    note = add_note(col, front="question text", back="answer text")
    baseline.run_notes_baseline(col, conn)

    # rename field Front → Question (schema change; content preserved)
    notetype = col.models.by_name("Basic")
    notetype["flds"][0]["name"] = "Question"
    col.models.update_dict(notetype)

    edit(col, note.id, Question="edited question")

    old_version = list_note_versions(conn, note.id)[-1]
    assert old_version.field_names == ("Front", "Back")
    result = restore.apply_note_version(col, old_version, None, "Restore")

    live = col.get_note(note.id)
    assert live["Question"] == "edited question"  # 'Front' has no match → skipped
    assert live["Back"] == "answer text"
    assert result.skipped_fields == ("Front",)
    assert result.applied_fields == ("Back",)


def test_guid_mismatch_blocks_in_place_restore(col, conn):
    note = add_note(col)
    baseline.run_notes_baseline(col, conn)
    version = list_note_versions(conn, note.id)[0]

    tampered = replace(version, guid="someone-else")
    with pytest.raises(restore.GuidMismatchError):
        restore.apply_note_version(col, tampered, None, "Restore")


def test_restore_deleted_note_as_new(col, conn):
    note = add_note(col, front="precious", back="content", tags=("keep",))
    baseline.run_notes_baseline(col, conn)
    old_version = list_note_versions(conn, note.id)[0]

    col.remove_notes([note.id])
    with pytest.raises(NotFoundError):
        restore.apply_note_version(col, old_version, None, "Restore")

    result = restore.restore_deleted_note_as_new(col, old_version, 1, "Restore as new")
    new_nid = result.new_nid
    assert new_nid != int(note.id)
    recreated = col.get_note(new_nid)
    assert recreated["Front"] == "precious"
    assert recreated.tags == ["keep"]
    assert col.undo_status().undo == "Restore as new"


def test_notetype_restore_templates_and_css_only(col, conn):
    capture_notetypes.scan_notetypes(col, conn, origin="baseline")
    mid = int(col.models.by_name("Basic")["id"])
    old_version = list_notetype_versions(conn, mid)[0]

    notetype = col.models.get(mid)
    original_sortf = notetype["sortf"]
    notetype["css"] = ".card { color: pink; }"
    notetype["tmpls"][0]["qfmt"] = "{{Front}}<!-- v2 -->"
    notetype["sortf"] = 1  # non-restorable surface: must survive the restore
    col.models.update_dict(notetype)

    result = restore.apply_notetype_version(col, old_version, "Restore note type")

    live = col.models.get(mid)
    assert "pink" not in live["css"]
    assert "<!-- v2 -->" not in live["tmpls"][0]["qfmt"]
    assert live["sortf"] == 1  # schema/prefs untouched
    assert result.applied_templates == ("Card 1",)
    assert result.missing_in_current == ()
    assert result.missing_in_stored == ()
    assert col.undo_status().undo == "Restore note type"
    assert original_sortf == 0


def test_notetype_restore_reports_template_mismatches(col, conn):
    capture_notetypes.scan_notetypes(col, conn, origin="baseline")
    mid = int(col.models.by_name("Basic")["id"])
    old_version = list_notetype_versions(conn, mid)[0]

    notetype = col.models.get(mid)
    extra = col.models.new_template("Card 2")
    extra["qfmt"] = "{{Front}} (second)"  # card templates need a field on the front
    extra["afmt"] = "{{Back}}"
    col.models.add_template(notetype, extra)
    col.models.update_dict(notetype)

    result = restore.apply_notetype_version(col, old_version, "Restore note type")

    live = col.models.get(mid)
    assert len(live["tmpls"]) == 2  # never adds/removes templates
    assert result.applied_templates == ("Card 1",)
    assert result.missing_in_stored == ("Card 2",)


def test_notetype_restore_missing_notetype_raises(col, conn):
    capture_notetypes.scan_notetypes(col, conn, origin="baseline")
    mid = int(col.models.by_name("Basic")["id"])
    version = list_notetype_versions(conn, mid)[0]
    col.models.remove(mid)

    with pytest.raises(restore.RestoreError):
        restore.apply_notetype_version(col, version, "Restore")
