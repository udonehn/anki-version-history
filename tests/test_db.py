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
