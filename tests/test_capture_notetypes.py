from __future__ import annotations

import pytest
from anki.collection import Collection

from note_version_history import consts
from note_version_history.capture_notetypes import (
    list_notetype_versions,
    scan_notetypes,
    snapshot_notetype,
)


@pytest.fixture
def col(tmp_path):
    collection = Collection(str(tmp_path / "collection.anki2"))
    yield collection
    collection.close()


def basic_mid(col: Collection) -> int:
    return int(col.models.by_name("Basic")["id"])


def test_initial_scan_captures_every_notetype_once(col, conn):
    first = scan_notetypes(col, conn, origin=consts.ORIGIN_BASELINE)
    assert first.captured >= 1  # fresh collections ship several stock notetypes

    second = scan_notetypes(col, conn)
    assert second.captured == 0
    assert second.deleted == 0


def test_css_change_is_captured(col, conn):
    scan_notetypes(col, conn, origin=consts.ORIGIN_BASELINE)

    notetype = col.models.by_name("Basic")
    notetype["css"] += "\n.card { color: red; }"
    col.models.update_dict(notetype)

    report = scan_notetypes(col, conn, op_label="Update note type")
    assert report.captured == 1

    versions = list_notetype_versions(conn, basic_mid(col))
    assert len(versions) == 2
    assert versions[0].origin == consts.ORIGIN_AUTO
    assert ".card { color: red; }" in versions[0].config_json


def test_template_change_is_captured(col, conn):
    scan_notetypes(col, conn, origin=consts.ORIGIN_BASELINE)

    notetype = col.models.by_name("Basic")
    notetype["tmpls"][0]["afmt"] = "{{FrontSide}}<hr id=answer>{{Back}}<!-- v2 -->"
    col.models.update_dict(notetype)

    assert scan_notetypes(col, conn).captured == 1


def test_non_template_change_is_ignored(col, conn):
    scan_notetypes(col, conn, origin=consts.ORIGIN_BASELINE)

    notetype = col.models.by_name("Basic")
    notetype["sortf"] = 1  # sort-field tweak: not part of the restorable surface
    col.models.update_dict(notetype)

    assert scan_notetypes(col, conn).captured == 0


def test_rename_is_captured(col, conn):
    scan_notetypes(col, conn, origin=consts.ORIGIN_BASELINE)
    mid = basic_mid(col)

    notetype = col.models.get(mid)
    notetype["name"] = "Basic (renamed)"
    col.models.update_dict(notetype)

    assert scan_notetypes(col, conn).captured == 1
    assert list_notetype_versions(conn, mid)[0].name == "Basic (renamed)"


def test_removed_notetype_gets_deletion_marker(col, conn):
    scan_notetypes(col, conn, origin=consts.ORIGIN_BASELINE)
    mid = basic_mid(col)

    col.models.remove(mid)

    report = scan_notetypes(col, conn)
    assert report.deleted == 1

    versions = list_notetype_versions(conn, mid)
    assert versions[0].deleted is True
    assert versions[0].name == "Basic"  # name preserved from last version
    alive = conn.execute(
        "select alive from notetype_index where mid=?", (mid,)
    ).fetchone()[0]
    assert alive == 0
    # previous version still holds the full config for viewing/restore
    assert versions[1].config_json != ""


def test_manual_snapshot_bypasses_dedupe(col, conn):
    scan_notetypes(col, conn, origin=consts.ORIGIN_BASELINE)
    mid = basic_mid(col)

    assert snapshot_notetype(col, conn, mid, op_label="Manual") is True
    assert snapshot_notetype(col, conn, mid, op_label="Manual") is True

    versions = list_notetype_versions(conn, mid)
    assert len(versions) == 3  # baseline + two identical manual snapshots
    assert versions[0].origin == consts.ORIGIN_MANUAL


def test_snapshot_missing_notetype_returns_false(col, conn):
    assert snapshot_notetype(col, conn, 999_999_999) is False
