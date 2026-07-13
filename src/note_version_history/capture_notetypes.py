"""Note type capture: dozens of dicts, so a full compare each scan is cheap.

The stored hash covers only the restorable surface (name + template
qfmt/afmt + CSS) so sort-field/LaTeX tweaks don't spam the timeline; the full
dict JSON is stored per version for context and restore.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from anki.collection import Collection

from . import consts, hashing
from .records import NotetypeVersion

DELETED_HASH = "__deleted__"

_INSERT_VERSION_SQL = (
    "INSERT INTO notetype_versions"
    " (mid, ts, origin, op_label, name, config, hash, deleted)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

_UPSERT_INDEX_SQL = (
    "INSERT INTO notetype_index (mid, latest_hash, latest_version, alive)"
    " VALUES (?, ?, ?, ?)"
    " ON CONFLICT(mid) DO UPDATE SET latest_hash=excluded.latest_hash,"
    " latest_version=excluded.latest_version, alive=excluded.alive"
)


@dataclass(frozen=True)
class NotetypeScanReport:
    captured: int = 0
    deleted: int = 0


def scan_notetypes(
    col: Collection,
    conn: sqlite3.Connection,
    *,
    origin: str = consts.ORIGIN_AUTO,
    op_label: str = "",
    now_ms: int | None = None,
) -> NotetypeScanReport:
    """Compare every notetype's restorable surface against the index; append
    version rows for changes, deletion markers for vanished notetypes."""
    resolved_now = now_ms if now_ms is not None else int(time.time() * 1000)
    current: dict[int, dict] = {}
    for entry in col.models.all_names_and_ids():
        notetype = col.models.get(entry.id)
        if notetype is not None:
            current[int(entry.id)] = notetype

    index = {
        row["mid"]: (row["latest_hash"], row["alive"])
        for row in conn.execute("select mid, latest_hash, alive from notetype_index")
    }

    captured = deleted = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for mid, notetype in current.items():
            if _capture_if_changed(conn, mid, notetype, origin, op_label, resolved_now, index):
                captured += 1
        for mid, (_hash, alive) in index.items():
            if alive == 1 and mid not in current:
                _insert_deletion_marker(conn, mid, origin, op_label, resolved_now)
                deleted += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return NotetypeScanReport(captured=captured, deleted=deleted)


def snapshot_notetype(
    col: Collection,
    conn: sqlite3.Connection,
    mid: int,
    *,
    origin: str = consts.ORIGIN_MANUAL,
    op_label: str = "",
    now_ms: int | None = None,
) -> bool:
    """Manual snapshot of one notetype: always inserts (dedupe bypassed)."""
    notetype = col.models.get(mid)
    if notetype is None:
        return False
    resolved_now = now_ms if now_ms is not None else int(time.time() * 1000)
    conn.execute("BEGIN IMMEDIATE")
    try:
        _insert_current(conn, int(mid), notetype, origin, op_label, resolved_now)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return True


def list_notetype_versions(conn: sqlite3.Connection, mid: int) -> list[NotetypeVersion]:
    """All stored versions of a notetype, newest first."""
    rows = conn.execute(
        "select id, mid, ts, origin, op_label, name, config, hash, deleted"
        " from notetype_versions where mid=? order by id desc",
        (mid,),
    ).fetchall()
    return [
        NotetypeVersion(
            id=row["id"],
            mid=row["mid"],
            ts=row["ts"],
            origin=row["origin"],
            op_label=row["op_label"],
            name=row["name"],
            config_json=row["config"],
            hash=row["hash"],
            deleted=bool(row["deleted"]),
        )
        for row in rows
    ]


# --- internals (caller owns the transaction) ---


def _capture_if_changed(
    conn: sqlite3.Connection,
    mid: int,
    notetype: dict,
    origin: str,
    op_label: str,
    now_ms: int,
    index: dict[int, tuple[str, int]],
) -> bool:
    surface_hash = hashing.notetype_hash_from_dict(notetype)
    indexed = index.get(mid)
    if indexed is not None and indexed[0] == surface_hash and indexed[1] == 1:
        return False
    _insert_current(conn, mid, notetype, origin, op_label, now_ms)
    return True


def _insert_current(
    conn: sqlite3.Connection,
    mid: int,
    notetype: dict,
    origin: str,
    op_label: str,
    now_ms: int,
) -> int:
    surface_hash = hashing.notetype_hash_from_dict(notetype)
    cursor = conn.execute(
        _INSERT_VERSION_SQL,
        (
            mid,
            now_ms,
            origin,
            op_label,
            notetype.get("name", ""),
            json.dumps(notetype, ensure_ascii=False),
            surface_hash,
            0,
        ),
    )
    version_id = int(cursor.lastrowid)
    conn.execute(_UPSERT_INDEX_SQL, (mid, surface_hash, version_id, 1))
    return version_id


def _insert_deletion_marker(
    conn: sqlite3.Connection, mid: int, origin: str, op_label: str, now_ms: int
) -> None:
    last_name = conn.execute(
        "select name from notetype_versions where mid=? order by id desc limit 1", (mid,)
    ).fetchone()
    cursor = conn.execute(
        _INSERT_VERSION_SQL,
        (
            mid,
            now_ms,
            origin,
            op_label or consts.LABEL_DELETE_NOTETYPE,
            last_name[0] if last_name is not None else "",
            "",
            DELETED_HASH,
            1,
        ),
    )
    conn.execute(_UPSERT_INDEX_SQL, (mid, DELETED_HASH, int(cursor.lastrowid), 0))
