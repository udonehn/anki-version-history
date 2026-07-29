from __future__ import annotations

import sqlite3

import pytest

from note_version_history import consts, db

EXPECTED_TABLES = {
    "meta",
    "note_versions",
    "notetype_versions",
    "media_events",
    "note_index",
    "notetype_index",
    "media_manifest",
    "note_scan_boundary",
}


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in rows}


def test_open_creates_schema(tmp_db_path):
    connection = db.open_history_db(tmp_db_path)
    try:
        assert EXPECTED_TABLES <= _table_names(connection)
        assert db.meta_get(connection, consts.META_SCHEMA_VERSION) == str(db.SCHEMA_VERSION)
    finally:
        connection.close()


def test_open_creates_parent_dirs(tmp_path):
    nested = tmp_path / "a" / "b" / "history.db"
    connection = db.open_history_db(nested)
    connection.close()
    assert nested.exists()


def test_reopen_is_idempotent(tmp_db_path):
    first = db.open_history_db(tmp_db_path)
    db.meta_set(first, "sentinel", "kept")
    first.close()

    second = db.open_history_db(tmp_db_path)
    try:
        assert db.meta_get(second, "sentinel") == "kept"
        assert db.meta_get(second, consts.META_SCHEMA_VERSION) == str(db.SCHEMA_VERSION)
    finally:
        second.close()


def test_pragmas_applied(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2  # INCREMENTAL


def test_newer_schema_is_refused(tmp_db_path):
    connection = db.open_history_db(tmp_db_path)
    db.meta_set(connection, consts.META_SCHEMA_VERSION, str(db.SCHEMA_VERSION + 1))
    connection.close()

    with pytest.raises(db.HistoryDbTooNew):
        db.open_history_db(tmp_db_path)


def test_meta_roundtrip(conn):
    assert db.meta_get(conn, "missing") is None
    assert db.meta_get(conn, "missing", "fallback") == "fallback"

    db.meta_set(conn, "key", "v1")
    db.meta_set(conn, "key", "v2")  # upsert
    assert db.meta_get(conn, "key") == "v2"

    assert db.meta_get_int(conn, "int_missing", 7) == 7
    db.meta_set(conn, "int_key", "41")
    assert db.meta_get_int(conn, "int_key") == 41
    db.meta_set(conn, "int_bad", "not-a-number")
    assert db.meta_get_int(conn, "int_bad", 3) == 3

    db.meta_set_json(conn, "json_key", {"cursor": 10, "state": "pending"})
    assert db.meta_get_json(conn, "json_key") == {"cursor": 10, "state": "pending"}
    assert db.meta_get_json(conn, "json_missing", {"d": 1}) == {"d": 1}
    db.meta_set(conn, "json_bad", "{broken")
    assert db.meta_get_json(conn, "json_bad", "fallback") == "fallback"


def test_origin_check_constraint_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO note_versions"
            " (nid, guid, mid, ts, origin, op_label, fields, field_names, tags, hash)"
            " VALUES (1, 'g', 1, 0, 'bogus', '', '[]', '[]', '[]', 'h')"
        )


def test_media_event_check_constraint_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO media_events (fname, ts, origin, event, sha1, size)"
            " VALUES ('a.mp3', 0, 'auto', 'renamed', 'abc', 1)"
        )


def test_v1_to_v2_migration_preserves_rows_ids_and_index(tmp_db_path):
    connection = sqlite3.connect(str(tmp_db_path), isolation_level=None)
    connection.row_factory = sqlite3.Row
    db._create_v1(connection)  # noqa: SLF001 - construct a released v1 fixture
    db.meta_set(connection, consts.META_SCHEMA_VERSION, "1")
    connection.execute(
        "INSERT INTO note_versions"
        " (id,nid,guid,mid,ts,origin,op_label,fields,field_names,tags,hash)"
        " VALUES (42,7,'guid-7',9,10,'auto','edit','[\"x\"]','[\"Front\"]','[]','h')"
    )
    connection.execute(
        "INSERT INTO note_index(nid,guid,latest_hash,latest_version,alive)"
        " VALUES (7,'guid-7','h',42,1)"
    )
    connection.close()

    migrated = db.open_history_db(tmp_db_path)
    try:
        row = migrated.execute("select * from note_versions where id=42").fetchone()
        assert row["fields"] == '["x"]'
        assert row["user_label"] == ""
        assert row["pinned"] == 0
        index = migrated.execute("select * from note_index").fetchone()
        assert index["identity"] == "g:guid-7"
        assert index["latest_version"] == 42
    finally:
        migrated.close()


def test_interrupted_v2_transaction_can_be_reopened(tmp_db_path):
    connection = sqlite3.connect(str(tmp_db_path), isolation_level=None)
    connection.row_factory = sqlite3.Row
    db._create_v1(connection)  # noqa: SLF001
    db.meta_set(connection, consts.META_SCHEMA_VERSION, "1")
    connection.execute("BEGIN IMMEDIATE")
    db._create_v2(connection)  # noqa: SLF001
    connection.execute("ROLLBACK")
    assert "identity" not in {
        row["name"] for row in connection.execute("pragma table_info(note_index)")
    }
    connection.close()

    reopened = db.open_history_db(tmp_db_path)
    try:
        assert db.meta_get(reopened, consts.META_SCHEMA_VERSION) == "2"
        assert "identity" in {
            row["name"] for row in reopened.execute("pragma table_info(note_index)")
        }
    finally:
        reopened.close()
