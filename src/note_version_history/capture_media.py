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
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from anki.collection import Collection
from anki.errors import NotFoundError

from . import consts
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


def full_scan(
    col: Collection,
    conn: sqlite3.Connection,
    blobs: BlobStore,
    *,
    origin: str = consts.ORIGIN_AUTO,
    op_label: str = "",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    now_ms: int | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> MediaScanReport:
    """Diff the whole media folder against the manifest; store changed blobs
    and append events. Deletion events fire only on a completed pass (an
    interrupted scan must not misread unvisited files as deleted)."""
    resolved_now = now_ms if now_ms is not None else _now_ms()
    media_dir = Path(col.media.dir())
    manifest = _load_manifest(conn)
    seen: set[str] = set()
    added = modified = 0
    interrupted = False

    entries = sorted(_iter_media_entries(media_dir), key=lambda e: e.name)
    for start in range(0, len(entries), chunk_size):
        if should_stop is not None and should_stop():
            interrupted = True
            break
        chunk = entries[start : start + chunk_size]
        conn.execute("BEGIN IMMEDIATE")
        try:
            for entry in chunk:
                seen.add(entry.name)
                counted = _capture_entry(
                    conn, blobs, entry, manifest, origin, op_label, resolved_now
                )
                if counted == "added":
                    added += 1
                elif counted == "modified":
                    modified += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    deleted = 0
    if not interrupted:
        missing = [fname for fname in manifest if fname not in seen]
        if missing:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for fname in missing:
                    last_sha1, size, _mtime = manifest[fname]
                    conn.execute(
                        _INSERT_EVENT_SQL,
                        (fname, resolved_now, origin, consts.EVENT_DELETED, last_sha1, size),
                    )
                    conn.execute("DELETE FROM media_manifest WHERE fname=?", (fname,))
                    deleted += 1
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
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
    op_label: str = "",
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
        op_label=op_label,
        now_ms=now_ms,
    )


def capture_named_files(
    conn: sqlite3.Connection,
    blobs: BlobStore,
    media_dir: Path,
    fnames: Iterable[str],
    *,
    origin: str = consts.ORIGIN_AUTO,
    op_label: str = "",
    now_ms: int | None = None,
) -> int:
    """Stat-diff specific filenames against the manifest; store new/changed
    content. Returns the number of events written."""
    resolved_now = now_ms if now_ms is not None else _now_ms()
    manifest = _load_manifest(conn)
    written = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for fname in fnames:
            if not _safe_media_name(fname):
                continue
            path = media_dir / fname
            if not path.is_file():
                continue  # referenced but absent; media check territory
            entry_stat = path.stat()
            counted = _capture_stat(
                conn,
                blobs,
                fname,
                path,
                entry_stat.st_size,
                int(entry_stat.st_mtime * 1000),
                manifest,
                origin,
                op_label,
                resolved_now,
            )
            if counted in ("added", "modified"):
                written += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return written


def restore_media_file(
    col: Collection,
    conn: sqlite3.Connection,
    blobs: BlobStore,
    fname: str,
    sha1: str,
    *,
    op_label: str = "",
    pre_backup_label: str = "",
    now_ms: int | None = None,
) -> None:
    """Write a stored blob back under its EXACT original name (C4).

    The current on-disk content (if any, and if unknown to the store) is
    snapshotted first — restoring is itself reversible through history.
    Not part of Anki's undo (media never is)."""
    if not _safe_media_name(fname):
        raise ValueError(f"unsafe media filename: {fname!r}")
    resolved_now = now_ms if now_ms is not None else _now_ms()
    media_dir = Path(col.media.dir())
    target = media_dir / fname
    data = blobs.read_bytes(sha1)  # FileNotFoundError if the blob is gone

    conn.execute("BEGIN IMMEDIATE")
    try:
        existed = target.is_file()
        if existed:
            current_sha1, current_size = blobs.put_file(target)
            manifest = _load_manifest(conn)
            known = manifest.get(fname)
            if known is None or known[0] != current_sha1:
                conn.execute(
                    _INSERT_EVENT_SQL,
                    (
                        fname,
                        resolved_now,
                        consts.ORIGIN_RESTORE,
                        consts.EVENT_MODIFIED if known is not None else consts.EVENT_ADDED,
                        current_sha1,
                        current_size,
                    ),
                )
        tmp = media_dir / f".nvh-tmp-{uuid.uuid4().hex}"
        try:
            tmp.write_bytes(data)
            os.replace(tmp, target)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        conn.execute(
            _INSERT_EVENT_SQL,
            (
                fname,
                resolved_now,
                consts.ORIGIN_RESTORE,
                consts.EVENT_MODIFIED if existed else consts.EVENT_ADDED,
                sha1,
                len(data),
            ),
        )
        conn.execute(
            _UPSERT_MANIFEST_SQL,
            (fname, sha1, len(data), int(target.stat().st_mtime * 1000)),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    # pre_backup_label is folded into op_label reporting by the UI; kept as a
    # parameter so callers can localize without this module importing i18n.
    del op_label, pre_backup_label


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


def _capture_entry(
    conn, blobs, entry, manifest, origin, op_label, now_ms
) -> str | None:
    stat = entry.stat()
    return _capture_stat(
        conn,
        blobs,
        entry.name,
        Path(entry.path),
        stat.st_size,
        int(stat.st_mtime * 1000),
        manifest,
        origin,
        op_label,
        now_ms,
    )


def _capture_stat(
    conn: sqlite3.Connection,
    blobs: BlobStore,
    fname: str,
    path: Path,
    size: int,
    mtime_ms: int,
    manifest: dict[str, tuple[str, int, int]],
    origin: str,
    op_label: str,
    now_ms: int,
) -> str | None:
    """Caller owns the transaction. Returns 'added'/'modified'/None."""
    known = manifest.get(fname)
    if known is not None and known[1] == size and known[2] == mtime_ms:
        return None  # stat unchanged → content assumed unchanged
    sha1, actual_size = blobs.put_file(path)
    if known is None:
        event = consts.EVENT_ADDED
    elif known[0] != sha1:
        event = consts.EVENT_MODIFIED
    else:
        # metadata drift only (same content): refresh manifest, no event
        conn.execute(_UPSERT_MANIFEST_SQL, (fname, sha1, actual_size, mtime_ms))
        manifest[fname] = (sha1, actual_size, mtime_ms)
        return None
    conn.execute(
        _INSERT_EVENT_SQL, (fname, now_ms, origin, event, sha1, actual_size)
    )
    conn.execute(_UPSERT_MANIFEST_SQL, (fname, sha1, actual_size, mtime_ms))
    manifest[fname] = (sha1, actual_size, mtime_ms)
    return "added" if event == consts.EVENT_ADDED else "modified"


def _safe_media_name(fname: str) -> bool:
    if not fname or fname in (".", ".."):
        return False
    return "/" not in fname and "\\" not in fname and ":" not in fname
