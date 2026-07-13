"""First-run baseline: capture the entire collection (notes + notetypes) so
every later edit has a "previous version" to restore.

Resumable: the cursor (last processed nid) is committed with each chunk, so
an interrupted baseline continues where it left off. Media baseline is a
separate flow (media milestone) tracked in the same state dict.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from anki.collection import Collection

from . import capture_media, capture_notetypes, consts, db
from .blobstore import BlobStore
from .capture_notes import NoteScanContext, capture_note

DEFAULT_CHUNK_SIZE = 1000

STATE_PENDING = "pending"
STATE_DONE = "done"
STATE_SKIPPED = "skipped"

_DEFAULT_STATE: dict[str, object] = {
    "notes": STATE_PENDING,
    "notes_cursor": 0,
    "media": STATE_PENDING,
    "media_cursor": "",
}


def get_state(conn: sqlite3.Connection) -> dict:
    stored = db.meta_get_json(conn, consts.META_BASELINE_STATE, {})
    state = dict(_DEFAULT_STATE)
    if isinstance(stored, dict):
        state.update(stored)
    return state


def update_state(conn: sqlite3.Connection, **changes: object) -> dict:
    state = get_state(conn)
    state.update(changes)
    db.meta_set_json(conn, consts.META_BASELINE_STATE, state)
    return state


def notes_baseline_done(conn: sqlite3.Connection) -> bool:
    return get_state(conn)["notes"] == STATE_DONE


def estimate(col: Collection) -> dict:
    """Cheap read-only queries for the first-run wizard's informed-consent
    numbers (exact note count / text bytes / notetype count)."""
    row = col.db.first("select count(*), coalesce(sum(length(flds)), 0) from notes")
    return {
        "note_count": int(row[0]),
        "field_bytes": int(row[1]),
        "notetype_count": len(list(col.models.all_names_and_ids())),
    }


def run_notes_baseline(
    col: Collection,
    conn: sqlite3.Connection,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    now_ms: int | None = None,
    progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Baseline all notetypes then all notes (origin='baseline').

    Returns the number of note rows inserted this run. If ``should_stop``
    interrupts, state stays pending with the cursor committed — the next call
    resumes; already-indexed notes are skipped, so resumption never dupes.
    """
    if notes_baseline_done(conn):
        return 0
    resolved_now = now_ms if now_ms is not None else int(time.time() * 1000)

    # empty op_label → the timeline derives the label from origin='baseline'
    capture_notetypes.scan_notetypes(
        col, conn, origin=consts.ORIGIN_BASELINE, op_label="", now_ms=resolved_now
    )

    total = int(col.db.scalar("select count(*) from notes"))
    cursor = int(get_state(conn).get("notes_cursor") or 0)
    done = int(col.db.scalar("select count(*) from notes where id <= ?", cursor))
    ctx = NoteScanContext(
        origin=consts.ORIGIN_BASELINE, op_label="", now_ms=resolved_now
    )

    captured = 0
    while True:
        if should_stop is not None and should_stop():
            return captured  # state stays pending; cursor already committed
        rows = col.db.all(
            "select id from notes where id > ? order by id limit ?", cursor, chunk_size
        )
        if not rows:
            break
        cursor, chunk_captured = _process_chunk(col, conn, ctx, rows, resolved_now)
        captured += chunk_captured
        done += len(rows)
        if progress is not None:
            progress(done, total)

    _finalize(col, conn, total)
    return captured


# --- internals ---


def _process_chunk(
    col: Collection,
    conn: sqlite3.Connection,
    ctx: NoteScanContext,
    rows: list,
    now_ms: int,
) -> tuple[int, int]:
    """One transaction: baseline a chunk of nids and commit the resume cursor
    with it. Returns (new_cursor, captured)."""
    captured = 0
    cursor = int(rows[-1][0])
    conn.execute("BEGIN IMMEDIATE")
    try:
        for (nid,) in rows:
            nid = int(nid)
            already_indexed = conn.execute(
                "select 1 from note_index where nid=?", (nid,)
            ).fetchone()
            if already_indexed is None:
                if capture_note(col, conn, nid, ctx, now_ms, force=False):
                    captured += 1
        state = get_state(conn)
        state["notes_cursor"] = cursor
        db.meta_set_json(conn, consts.META_BASELINE_STATE, state)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return cursor, captured


def _finalize(col: Collection, conn: sqlite3.Connection, total: int) -> None:
    """Mark done and initialize the scan marker/count so auto-capture takes
    over exactly where the baseline left off."""
    max_mod = int(col.db.scalar("select coalesce(max(mod), 0) from notes"))
    current_marker = db.meta_get_int(conn, consts.META_NOTE_SCAN_MARKER, 0)
    db.meta_set(conn, consts.META_NOTE_SCAN_MARKER, str(max(max_mod, current_marker)))
    db.meta_set(conn, consts.META_LAST_NOTE_COUNT, str(total))
    update_state(conn, notes=STATE_DONE)


# --- media baseline ---


def estimate_media(col: Collection) -> dict:
    count, total_bytes = capture_media.media_stats(col.media.dir())
    return {"file_count": count, "total_bytes": total_bytes}


def skip_media_baseline(conn: sqlite3.Connection) -> None:
    update_state(conn, media=STATE_SKIPPED)


def media_baseline_state(conn: sqlite3.Connection) -> str:
    return str(get_state(conn)["media"])


def run_media_baseline(
    col: Collection,
    conn: sqlite3.Connection,
    blobs: BlobStore,
    *,
    chunk_size: int = 200,
    now_ms: int | None = None,
    progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Copy every media file into the content-addressed store (origin
    'baseline' events). Resumable via a filename cursor; files already in the
    manifest are skipped, so resumption never dupes. Returns files captured
    this run."""
    if media_baseline_state(conn) in (STATE_DONE, STATE_SKIPPED):
        return 0
    resolved_now = now_ms if now_ms is not None else int(time.time() * 1000)
    media_dir = Path(col.media.dir())
    names = sorted(
        entry.name for entry in capture_media._iter_media_entries(media_dir)  # noqa: SLF001
    )
    total = len(names)
    cursor = str(get_state(conn).get("media_cursor") or "")
    done = sum(1 for name in names if name <= cursor) if cursor else 0
    remaining = [name for name in names if name > cursor]

    captured = 0
    manifest = capture_media._load_manifest(conn)  # noqa: SLF001
    for start in range(0, len(remaining), chunk_size):
        if should_stop is not None and should_stop():
            return captured  # state stays pending; cursor already committed
        chunk = remaining[start : start + chunk_size]
        conn.execute("BEGIN IMMEDIATE")
        try:
            for fname in chunk:
                path = media_dir / fname
                if fname in manifest or not path.is_file():
                    continue
                stat = path.stat()
                result = capture_media._capture_stat(  # noqa: SLF001
                    conn,
                    blobs,
                    fname,
                    path,
                    stat.st_size,
                    int(stat.st_mtime * 1000),
                    manifest,
                    consts.ORIGIN_BASELINE,
                    "",  # media events carry no op_label; kept empty
                    resolved_now,
                )
                if result is not None:
                    captured += 1
            state = get_state(conn)
            state["media_cursor"] = chunk[-1]
            db.meta_set_json(conn, consts.META_BASELINE_STATE, state)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        done += len(chunk)
        if progress is not None:
            progress(done, total)

    update_state(conn, media=STATE_DONE)
    db.meta_set(conn, consts.META_LAST_MEDIA_SCAN_MS, str(resolved_now))
    return captured
