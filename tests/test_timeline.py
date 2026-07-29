from __future__ import annotations

import pytest

from note_version_history import capture_notes, capture_notetypes, timeline


def _insert_note_version(
    conn,
    version_id: int,
    *,
    label: str = "",
    op_label: str = "",
    fields: str = '["body"]',
    tags: str = '["tag"]',
    pinned: int = 0,
    origin: str = "auto",
    deleted: int = 0,
):
    conn.execute(
        "INSERT INTO note_versions"
        " (id,nid,guid,mid,ts,origin,op_label,fields,field_names,tags,hash,"
        " deleted,user_label,pinned)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            version_id,
            1,
            "timeline-guid",
            2,
            version_id,
            origin,
            op_label,
            fields,
            '["Front"]',
            tags,
            f"h{version_id}",
            deleted,
            label,
            pinned,
        ),
    )


def test_note_timeline_100_101_paging_and_cross_page_lookup(conn):
    for version_id in range(1, 102):
        _insert_note_version(conn, version_id)
    first = capture_notes.page_note_versions(conn, 1, guid="timeline-guid")
    second = capture_notes.page_note_versions(
        conn, 1, guid="timeline-guid", offset=100
    )
    assert len(first.items) == 100
    assert first.total == 101 and first.has_next
    assert len(second.items) == 1
    assert second.start == 101 and second.end == 101 and second.has_previous
    assert capture_notes.get_note_version(conn, first.items[0].id).id == 101
    assert capture_notes.get_previous_note_version(
        conn, first.items[-1].id
    ).id == 1
    assert capture_notes.get_adjacent_note_version(
        conn, 1, older=False
    ).id == 2


def test_note_timeline_search_escaping_content_toggle_and_filters(conn):
    _insert_note_version(
        conn,
        1,
        label=r"literal %_\ name",
        fields='["hidden needle"]',
        tags='["alpha"]',
        pinned=1,
    )
    _insert_note_version(conn, 2, op_label="@sync", tags='["beta"]')
    escaped = capture_notes.page_note_versions(
        conn,
        1,
        timeline.TimelineFilter(search=r"%_\ "),
        guid="timeline-guid",
    )
    assert [item.id for item in escaped.items] == [1]
    assert (
        capture_notes.page_note_versions(
            conn,
            1,
            timeline.TimelineFilter(search="needle"),
            guid="timeline-guid",
        ).total
        == 0
    )
    assert (
        capture_notes.page_note_versions(
            conn,
            1,
            timeline.TimelineFilter(search="needle", include_content=True),
            guid="timeline-guid",
        ).total
        == 1
    )
    sync = capture_notes.page_note_versions(
        conn,
        1,
        timeline.TimelineFilter(category="sync"),
        guid="timeline-guid",
    )
    assert [item.id for item in sync.items] == [2]
    pinned = capture_notes.page_note_versions(
        conn,
        1,
        timeline.TimelineFilter(category="all", pinned_only=True),
        guid="timeline-guid",
    )
    assert [item.id for item in pinned.items] == [1]


def test_annotation_updates_only_mutable_metadata(conn):
    _insert_note_version(conn, 1, fields='["immutable"]')
    assert capture_notes.update_note_annotation(
        conn, 1, user_label="  important  ", pinned=True
    )
    row = conn.execute("select * from note_versions where id=1").fetchone()
    assert row["user_label"] == "important"
    assert row["pinned"] == 1
    assert row["fields"] == '["immutable"]'


def test_deleted_restore_source_is_nearest_older_content(conn):
    _insert_note_version(conn, 1, fields='["old"]')
    _insert_note_version(conn, 2, deleted=1, fields="[]")
    _insert_note_version(conn, 3, fields='["new"]')
    _insert_note_version(conn, 4, deleted=1, fields="[]")
    assert capture_notes.get_deleted_restore_source(conn, 2).id == 1
    assert capture_notes.get_deleted_restore_source(conn, 4).id == 3


def test_notetype_timeline_content_search_and_annotation(conn):
    conn.execute(
        "INSERT INTO notetype_versions"
        " (id,mid,ts,origin,op_label,name,config,hash,deleted,user_label,pinned)"
        " VALUES (1,3,1,'manual','','Basic','{\"css\":\"needle\"}','h',0,'named',0)"
    )
    assert (
        capture_notetypes.page_notetype_versions(
            conn, 3, timeline.TimelineFilter(search="needle")
        ).total
        == 0
    )
    assert (
        capture_notetypes.page_notetype_versions(
            conn,
            3,
            timeline.TimelineFilter(search="needle", include_content=True),
        ).total
        == 1
    )
    assert capture_notetypes.update_notetype_annotation(
        conn, 1, user_label="star", pinned=True
    )
    assert capture_notetypes.get_notetype_version(conn, 1).pinned
    assert (
        capture_notetypes.get_adjacent_notetype_version(conn, 1, older=False)
        is None
    )


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("all", ""),
        ("pinned", "pinned=1"),
        ("deleted", "deleted=1"),
        ("snapshot", "origin='manual'"),
        ("restore", "origin='restore'"),
        ("baseline", "origin='baseline'"),
        ("automatic", "origin='auto'"),
        ("sync", "op_label='@sync'"),
    ],
)
def test_all_timeline_category_clauses(category, expected):
    sql, params = timeline.category_clause(category)
    assert expected in sql
    assert params == []


def test_invalid_timeline_category_and_table_are_rejected(conn):
    with pytest.raises(ValueError):
        timeline.category_clause("unknown")
    with pytest.raises(ValueError):
        timeline.update_annotation(
            conn, "media_events", 1, user_label="", pinned=False
        )
