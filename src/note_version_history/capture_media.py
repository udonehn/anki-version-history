"""Media capture: filesystem scans + targeted capture + exact-name restore.

Media changes are INVISIBLE to Anki's ops/undo system (OpChanges has no media
flag), so capture is driven by (a) targeted extraction from just-changed
notes via ``col.media.files_in_str`` and (b) full manifest-diff scans of
``col.media.dir()``. ``collection.media.db2`` is Anki's own sync DB and is
treated as opaque.

Change detection is (size, mtime) stat-diff with sha1 as the final arbiter;
a same-size same-mtime content swap is undetectable by design (the hash is
only computed when the stat changes).

Restore writes the exact original filename atomically (temp + os.replace) —
``media.add_file``/``write_data`` are unusable here because they rename on
name collision. Before overwriting unknown content, the current file is
snapshotted first, so restores are themselves reversible.
"""

from __future__ import annotations

import os
import sqlite3
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from anki.collection import Collection
from anki.errors import NotFoundError

from . import consts, db
from .blobstore import BlobStore
from .records import MediaEvent

DEFAULT_CHUNK_SIZE = 200
_DENYLIST = {"thumbs.db", "desktop.ini"}

_INSERT_EVENT_SQL = (
    "INSERT INTO media_events (fname, ts, origin, event, sha1, size)"
    " VALUES (?, ?, ?, ?, ?, ?)"
)

_UPSERT_MANIFEST_SQL = (
    "INSERT INTO media_manifest (fname, sha1, size, mtime) VALUES (?, ?, ?, ?)"
    " ON CONFLICT(fname) DO UPDATE SET sha1=excluded.sha1, size=excluded.size,"
    " mtime=excluded.mtime"
)


@dataclass(frozen=True)
class MediaScanReport:
    added: int = 0
    modified: int = 0
    deleted: int = 0
    interrupted: bool = False


@dataclass(frozen=True)
class MediaFileState:
    """A media file's content identity, stat'd + hashed (and its blob stored)
    with NO history-DB lock held. ``fname`` is the NFC-normalized manifest/event
    key; the on-disk file was read under its original name."""

    fname: str
    sha1: str
    size: int
    mtime_ms: int


def _nfc(name: str) -> str:
    """Normalize a filename to NFC so a disk listing (NFD on macOS) and a note's
    reference (NFC) map to the same manifest key — otherwise the same file reads
    as a phantom add+delete pair."""
    return unicodedata.normalize("NFC", name)


def full_scan(
    col: Collection,
    conn: sqlite3.Connection,
    blobs: BlobStore,
    *,
    origin: str = consts.ORIGIN_AUTO,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    now_ms: int | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> MediaScanReport:
    """Diff the whole media folder against the manifest; store changed blobs
    and append events. Each chunk is stat'd/hashed/stored OUTSIDE the write
    transaction (long file I/O holds no lock), then committed in a short one
    that re-checks the manifest per file. Deletion events fire only on a
    completed pass (an interrupted scan must not misread unvisited files as
    deleted)."""
    resolved_now = now_ms if now_ms is not None else _now_ms()
    media_dir = Path(col.media.dir())
    manifest = _load_manifest(conn)  # stat-skip hint (may be stale under concurrency)
    seen: set[str] = set()
    added = modified = 0
    interrupted = False

    entries = sorted(_iter_media_entries(media_dir), key=lambda e: e.name)
    for start in range(0, len(entries), chunk_size):
        if should_stop is not None and should_stop():
            interrupted = True
            break
        chunk = entries[start : start + chunk_size]
        states: list[MediaFileState] = []
        for entry in chunk:
            key = _nfc(entry.name)
            seen.add(key)
            state = _read_media_state(blobs, key, Path(entry.path), manifest)
            if state is not None:
                states.append(state)
                manifest[key] = (state.sha1, state.size, state.mtime_ms)  # refresh hint
        chunk_added, chunk_modified = _write_media_states(conn, states, origin, resolved_now)
        added += chunk_added
        modified += chunk_modified

    deleted = 0
    if not interrupted:
        deleted = _write_deletions(conn, media_dir, seen, origin, resolved_now)
        db.meta_set(conn, consts.META_LAST_MEDIA_SCAN_MS, str(resolved_now))
    return MediaScanReport(
        added=added, modified=modified, deleted=deleted, interrupted=interrupted
    )


def capture_files_for_notes(
    col: Collection,
    conn: sqlite3.Connection,
    blobs: BlobStore,
    nids: Iterable[int],
    *,
    origin: str = consts.ORIGIN_AUTO,
    now_ms: int | None = None,
) -> int:
    """Targeted capture: snapshot media files referenced by the given notes
    (called right after a note scan, so a pasted image is versioned with the
    edit that introduced it). Deletions are owned by full_scan."""
    fnames: set[str] = set()
    for nid in nids:
        try:
            note = col.get_note(int(nid))
        except NotFoundError:
            continue
        joined = "\x1f".join(note.fields)
        for fname in col.media.files_in_str(note.mid, joined):
            fnames.add(fname)
    if not fnames:
        return 0
    return capture_named_files(
        conn,
        blobs,
        Path(col.media.dir()),
        sorted(fnames),
        origin=origin,
        now_ms=now_ms,
    )


def capture_named_files(
    conn: sqlite3.Connection,
    blobs: BlobStore,
    media_dir: Path,
    fnames: Iterable[str],
    *,
    origin: str = consts.ORIGIN_AUTO,
    now_ms: int | None = None,
) -> int:
    """Stat-diff specific filenames against the manifest; store new/changed
    content. Files are read/hashed outside the write transaction, then committed
    in one short transaction that re-checks each manifest row. Returns the number
    of events written."""
    resolved_now = now_ms if now_ms is not None else _now_ms()
    manifest = _load_manifest(conn)
    states: list[MediaFileState] = []
    for raw in fnames:
        if not _safe_media_name(raw):
            continue
        path = media_dir / raw  # filesystem access under the note's own spelling
        if not path.is_file():
            continue  # referenced but absent; media check territory
        key = _nfc(raw)
        state = _read_media_state(blobs, key, path, manifest)
        if state is not None:
            states.append(state)
            manifest[key] = (state.sha1, state.size, state.mtime_ms)
    added, modified = _write_media_states(conn, states, origin, resolved_now)
    return added + modified


def restore_media_file(
    col: Collection,
    conn: sqlite3.Connection,
    blobs: BlobStore,
    fname: str,
    sha1: str,
    *,
    now_ms: int | None = None,
) -> None:
    """Write a stored blob back under its EXACT original name.

    The current on-disk content (if any, and if unknown to the store) is
    snapshotted first — restoring is itself reversible through history. The blob
    is streamed to the target (temp + os.replace), never loaded whole into
    memory, and a missing blob raises before any history row is written. Not
    part of Anki's undo (media never is)."""
    if not _safe_media_name(fname):
        raise ValueError(f"unsafe media filename: {fname!r}")
    if not blobs.has(sha1):
        raise FileNotFoundError(f"blob {sha1} is missing from the store")
    resolved_now = now_ms if now_ms is not None else _now_ms()
    media_dir = Path(col.media.dir())
    target = media_dir / fname
    key = _nfc(fname)

    conn.execute("BEGIN IMMEDIATE")
    try:
        existed = target.is_file()
        if existed:
            current_sha1, current_size = blobs.put_file(target)
            manifest = _load_manifest(conn)
            known = manifest.get(key)
            if known is None or known[0] != current_sha1:
                conn.execute(
                    _INSERT_EVENT_SQL,
                    (
                        key,
                        resolved_now,
                        consts.ORIGIN_RESTORE,
                        consts.EVENT_MODIFIED if known is not None else consts.EVENT_ADDED,
                        current_sha1,
                        current_size,
                    ),
                )
        size = blobs.copy_to(sha1, target)  # stream blob → exact original name
        conn.execute(
            _INSERT_EVENT_SQL,
            (
                key,
                resolved_now,
                consts.ORIGIN_RESTORE,
                consts.EVENT_MODIFIED if existed else consts.EVENT_ADDED,
                sha1,
                size,
            ),
        )
        conn.execute(
            _UPSERT_MANIFEST_SQL,
            (key, sha1, size, int(target.stat().st_mtime * 1000)),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def list_media_events(conn: sqlite3.Connection, fname: str) -> list[MediaEvent]:
    rows = conn.execute(
        "select id, fname, ts, origin, event, sha1, size from media_events"
        " where fname=? order by id desc",
        (fname,),
    ).fetchall()
    return [
        MediaEvent(
            id=row["id"],
            fname=row["fname"],
            ts=row["ts"],
            origin=row["origin"],
            event=row["event"],
            sha1=row["sha1"],
            size=row["size"],
        )
        for row in rows
    ]


def list_media_files(
    conn: sqlite3.Connection, name_filter: str = "", limit: int = 500
) -> list[tuple[str, str, int]]:
    """(fname, last_event, last_ts) for files with history, newest first."""
    like = f"%{name_filter}%" if name_filter else "%"
    rows = conn.execute(
        "select fname, event, ts from media_events where id in ("
        "  select max(id) from media_events where fname like ? group by fname)"
        " order by ts desc limit ?",
        (like, limit),
    ).fetchall()
    return [(row["fname"], row["event"], row["ts"]) for row in rows]


def media_stats(media_dir: Path | str) -> tuple[int, int]:
    """(file_count, total_bytes) of the media folder — wizard estimates."""
    count = 0
    total = 0
    for entry in _iter_media_entries(Path(media_dir)):
        count += 1
        total += entry.stat().st_size
    return count, total


# --- internals ---


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iter_media_entries(media_dir: Path):
    if not media_dir.is_dir():
        return
    with os.scandir(media_dir) as entries:
        for entry in entries:
            if not entry.is_file():
                continue
            name_lower = entry.name.lower()
            if name_lower in _DENYLIST or entry.name.startswith("."):
                continue
            yield entry


def _load_manifest(conn: sqlite3.Connection) -> dict[str, tuple[str, int, int]]:
    return {
        row["fname"]: (row["sha1"], row["size"], row["mtime"])
        for row in conn.execute("select fname, sha1, size, mtime from media_manifest")
    }


def _read_media_state(
    blobs: BlobStore,
    fname: str,
    path: Path,
    manifest_hint: dict[str, tuple[str, int, int]],
) -> MediaFileState | None:
    """Stat + (only if the stat changed) hash/store one file, with NO history-DB
    lock held. ``fname`` is the NFC key; ``path`` reads the on-disk file under
    its original name. Returns None when the stat matches the manifest hint
    (content assumed unchanged) or the file has vanished."""
    try:
        stat = path.stat()
    except OSError:
        return None
    size = stat.st_size
    mtime_ms = int(stat.st_mtime * 1000)
    known = manifest_hint.get(fname)
    if known is not None and known[1] == size and known[2] == mtime_ms:
        return None
    sha1, actual_size = blobs.put_file(path)
    return MediaFileState(fname=fname, sha1=sha1, size=actual_size, mtime_ms=mtime_ms)


def _apply_media_state(
    conn: sqlite3.Connection,
    state: MediaFileState,
    origin: str,
    now_ms: int,
) -> str | None:
    """Caller owns the transaction. Re-reads the manifest row for this fname
    INSIDE the transaction so a concurrent scan that already recorded the same
    content is deduped, not double-logged. Returns 'added'/'modified'/None
    (metadata-only drift)."""
    row = conn.execute(
        "select sha1 from media_manifest where fname=?", (state.fname,)
    ).fetchone()
    if row is None:
        event = consts.EVENT_ADDED
    elif row[0] != state.sha1:
        event = consts.EVENT_MODIFIED
    else:
        # same content, only size/mtime metadata drifted → refresh, no event
        conn.execute(
            _UPSERT_MANIFEST_SQL, (state.fname, state.sha1, state.size, state.mtime_ms)
        )
        return None
    conn.execute(
        _INSERT_EVENT_SQL, (state.fname, now_ms, origin, event, state.sha1, state.size)
    )
    conn.execute(
        _UPSERT_MANIFEST_SQL, (state.fname, state.sha1, state.size, state.mtime_ms)
    )
    return event


def _write_media_states(
    conn: sqlite3.Connection,
    states: list[MediaFileState],
    origin: str,
    now_ms: int,
) -> tuple[int, int]:
    """Commit a chunk of pre-hashed states in one short transaction. Returns
    (added, modified)."""
    added = modified = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for state in states:
            result = _apply_media_state(conn, state, origin, now_ms)
            if result == consts.EVENT_ADDED:
                added += 1
            elif result == consts.EVENT_MODIFIED:
                modified += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return added, modified


def _write_deletions(
    conn: sqlite3.Connection,
    media_dir: Path,
    seen: set[str],
    origin: str,
    now_ms: int,
) -> int:
    """Emit deletion events for manifest files not seen this pass.

    Two concurrency guards: the manifest is re-read fresh (a row a concurrent
    scan already tombstoned is gone, so no duplicate deletion event), and each
    candidate is stat-checked on disk (a file recorded by a concurrent targeted
    capture AFTER our directory listing is present, not deleted)."""
    manifest = _load_manifest(conn)
    missing = [
        fname
        for fname in manifest
        if fname not in seen and not (media_dir / fname).is_file()
    ]
    if not missing:
        return 0
    deleted = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for fname in missing:
            last_sha1, size, _mtime = manifest[fname]
            conn.execute(
                _INSERT_EVENT_SQL,
                (fname, now_ms, origin, consts.EVENT_DELETED, last_sha1, size),
            )
            conn.execute("DELETE FROM media_manifest WHERE fname=?", (fname,))
            deleted += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return deleted


def _safe_media_name(fname: str) -> bool:
    if not fname or fname in (".", ".."):
        return False
    return "/" not in fname and "\\" not in fname and ":" not in fname
