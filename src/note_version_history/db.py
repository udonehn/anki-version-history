"""The add-on's own SQLite history database (never touches collection.anki2).

Connections are opened in autocommit mode (``isolation_level=None``); callers
that need atomicity (chunked scans, migrations) issue explicit BEGIN/COMMIT.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from . import consts

SCHEMA_VERSION = 1


class HistoryDbError(Exception):
    """Base error for history-database problems."""


class HistoryDbTooNew(HistoryDbError):
    """The DB was created by a newer add-on version (downgrade protection)."""


_DDL_V1 = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS note_versions (
  id          INTEGER PRIMARY KEY,
  nid         INTEGER NOT NULL,
  guid        TEXT    NOT NULL DEFAULT '',
  mid         INTEGER NOT NULL,
  ts          INTEGER NOT NULL,
  origin      TEXT    NOT NULL CHECK (origin IN ('baseline','auto','manual','restore')),
  op_label    TEXT    NOT NULL DEFAULT '',
  fields      TEXT    NOT NULL,
  field_names TEXT    NOT NULL,
  tags        TEXT    NOT NULL,
  hash        TEXT    NOT NULL,
  deleted     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_note_versions_nid ON note_versions (nid, id);
CREATE INDEX IF NOT EXISTS ix_note_versions_prune ON note_versions (origin, ts);

CREATE TABLE IF NOT EXISTS notetype_versions (
  id       INTEGER PRIMARY KEY,
  mid      INTEGER NOT NULL,
  ts       INTEGER NOT NULL,
  origin   TEXT    NOT NULL CHECK (origin IN ('baseline','auto','manual','restore')),
  op_label TEXT    NOT NULL DEFAULT '',
  name     TEXT    NOT NULL,
  config   TEXT    NOT NULL,
  hash     TEXT    NOT NULL,
  deleted  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_notetype_versions_mid ON notetype_versions (mid, id);

CREATE TABLE IF NOT EXISTS media_events (
  id     INTEGER PRIMARY KEY,
  fname  TEXT    NOT NULL,
  ts     INTEGER NOT NULL,
  origin TEXT    NOT NULL CHECK (origin IN ('baseline','auto','manual','restore')),
  event  TEXT    NOT NULL CHECK (event IN ('added','modified','deleted')),
  sha1   TEXT    NOT NULL,
  size   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_media_events_fname ON media_events (fname, id);

-- Mutable caches (rebuildable from history + collection; NOT history)
CREATE TABLE IF NOT EXISTS note_index (
  nid            INTEGER PRIMARY KEY,
  guid           TEXT    NOT NULL DEFAULT '',
  latest_hash    TEXT    NOT NULL,
  latest_version INTEGER NOT NULL,
  alive          INTEGER NOT NULL DEFAULT 1
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS notetype_index (
  mid            INTEGER PRIMARY KEY,
  latest_hash    TEXT    NOT NULL,
  latest_version INTEGER NOT NULL,
  alive          INTEGER NOT NULL DEFAULT 1
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS media_manifest (
  fname TEXT    PRIMARY KEY,
  sha1  TEXT    NOT NULL,
  size  INTEGER NOT NULL,
  mtime INTEGER NOT NULL
) WITHOUT ROWID;
"""


def _create_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL_V1)


# Migration infrastructure is kept for post-release schema changes; pre-release
# there is a single version and local data is simply recreated when the shape
# changes (no back-compat needed yet).
_MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _create_v1),
]


def open_history_db(path: Path | str) -> sqlite3.Connection:
    """Open (creating/migrating if needed) a history DB. Raises
    :class:`HistoryDbTooNew` if the file was written by a newer add-on."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not db_path.exists()
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        if is_new:
            # Must be set before any table exists to take effect.
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _migrate(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _schema_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if has_meta is None:
        return 0
    value = meta_get(conn, consts.META_SCHEMA_VERSION)
    return int(value) if value else 0


def _migrate(conn: sqlite3.Connection) -> None:
    current = _schema_version(conn)
    if current > SCHEMA_VERSION:
        raise HistoryDbTooNew(
            f"history DB schema {current} is newer than supported {SCHEMA_VERSION}"
        )
    for target, apply_migration in _MIGRATIONS:
        if current >= target:
            continue
        # executescript commits implicitly; DDL uses IF NOT EXISTS so a crash
        # between the script and the version bump is safely re-runnable.
        apply_migration(conn)
        meta_set(conn, consts.META_SCHEMA_VERSION, str(target))
        current = target


# --- meta helpers (autocommit connection: writes persist immediately) ---


def meta_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row is not None else default


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def meta_get_int(conn: sqlite3.Connection, key: str, default: int = 0) -> int:
    value = meta_get(conn, key)
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def meta_get_json(conn: sqlite3.Connection, key: str, default: object = None) -> object:
    value = meta_get(conn, key)
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def meta_set_json(conn: sqlite3.Connection, key: str, obj: object) -> None:
    meta_set(conn, key, json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
