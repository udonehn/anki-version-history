"""Retention: prune old automatic versions, GC unreferenced blobs, reclaim
space incrementally.

Invariants (headless-proven):
- only ``origin='auto' AND deleted=0`` note rows are prunable;
- the newest automatic row per note ALWAYS survives (so a deleted note's
  final content stays restorable);
- manual, baseline, restore and deletion-marker rows are permanent;
- media events are pruned by age only, always keeping each file's last event;
- blobs referenced by any remaining event or the manifest are never removed.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from . import consts, db
from .appconfig import RetentionConfig
from .blobstore import BlobStore

MAINTENANCE_INTERVAL_MS = 24 * 60 * 60 * 1000
_VACUUM_PAGES = 2000

_PRUNE_NOTES_SQL = """
DELETE FROM note_versions WHERE id IN (
  SELECT id FROM (
    SELECT id, ts,
           row_number() OVER (PARTITION BY nid ORDER BY id DESC) AS rn
    FROM note_versions
    WHERE origin = 'auto' AND deleted = 0
  )
  WHERE rn > 1 AND (rn > :max_per OR (:cutoff > 0 AND ts < :cutoff))
)
"""

_PRUNE_MEDIA_SQL = """
DELETE FROM media_events
WHERE ts < :cutoff
  AND id NOT IN (SELECT max(id) FROM media_events GROUP BY fname)
"""


@dataclass(frozen=True)
class MaintenanceReport:
    notes_pruned: int = 0
    media_events_pruned: int = 0
    blobs_removed: int = 0


def maintenance_due(conn: sqlite3.Connection, now_ms: int | None = None) -> bool:
    resolved_now = now_ms if now_ms is not None else _now_ms()
    last = db.meta_get_int(conn, consts.META_LAST_PRUNE_MS, 0)
    return resolved_now - last >= MAINTENANCE_INTERVAL_MS


def run_maintenance(
    conn: sqlite3.Connection,
    blobs: BlobStore,
    retention: RetentionConfig,
    *,
    now_ms: int | None = None,
) -> MaintenanceReport:
    """Prune + GC + incremental vacuum; stamps the last-run marker."""
    resolved_now = now_ms if now_ms is not None else _now_ms()
    notes_pruned = prune_note_versions(conn, retention, now_ms=resolved_now)
    media_pruned = prune_media_events(conn, retention, now_ms=resolved_now)
    blobs_removed = gc_blobs(conn, blobs) if media_pruned else 0
    incremental_vacuum(conn)
    db.meta_set(conn, consts.META_LAST_PRUNE_MS, str(resolved_now))
    return MaintenanceReport(
        notes_pruned=notes_pruned,
        media_events_pruned=media_pruned,
        blobs_removed=blobs_removed,
    )


def prune_note_versions(
    conn: sqlite3.Connection,
    retention: RetentionConfig,
    *,
    now_ms: int | None = None,
) -> int:
    resolved_now = now_ms if now_ms is not None else _now_ms()
    cutoff = (
        resolved_now - retention.max_age_days * 24 * 60 * 60 * 1000
        if retention.max_age_days > 0
        else 0
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = conn.execute(
            _PRUNE_NOTES_SQL,
            {"max_per": retention.max_auto_versions_per_note, "cutoff": cutoff},
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return cursor.rowcount if cursor.rowcount > 0 else 0


def prune_media_events(
    conn: sqlite3.Connection,
    retention: RetentionConfig,
    *,
    now_ms: int | None = None,
) -> int:
    if retention.media_max_age_days <= 0:
        return 0
    resolved_now = now_ms if now_ms is not None else _now_ms()
    cutoff = resolved_now - retention.media_max_age_days * 24 * 60 * 60 * 1000
    conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = conn.execute(_PRUNE_MEDIA_SQL, {"cutoff": cutoff})
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return cursor.rowcount if cursor.rowcount > 0 else 0


def gc_blobs(conn: sqlite3.Connection, blobs: BlobStore) -> int:
    """Remove blobs referenced by no media event and absent from the manifest."""
    referenced = {
        row[0] for row in conn.execute("select distinct sha1 from media_events")
    }
    referenced.update(
        row[0] for row in conn.execute("select distinct sha1 from media_manifest")
    )
    return blobs.gc(referenced)


def incremental_vacuum(conn: sqlite3.Connection, pages: int = _VACUUM_PAGES) -> None:
    conn.execute(f"PRAGMA incremental_vacuum({int(pages)})")


def full_vacuum(conn: sqlite3.Connection) -> None:
    """Manual 'Compact now' only — rewrites the whole DB file."""
    conn.execute("VACUUM")


def _now_ms() -> int:
    return int(time.time() * 1000)
