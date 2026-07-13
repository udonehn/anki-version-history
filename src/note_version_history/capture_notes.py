"""Note capture: post-operation scanning of the collection into history rows.

Headless — imports anki pylib only; the aqt scheduler feeds it a
:class:`NoteScanContext` built from hook-time observations.

Correctness notes:

- ``notes.mod`` has 1-second granularity, and undo REWINDS it (the backend
  restores the previous row verbatim). The inclusive ``mod >= marker`` query
  therefore cannot see undone notes; callers pass ``session_touched_nids``
  for an exact re-check, and deletions/resurrections are found via count
  triage + set-diff against ``note_index``.
- The scan marker is a high-water mark; it never decreases.
- Work is committed in chunks; an interrupted scan resumes idempotently
  because content hashes dedupe re-processing.
- Reads from the collection use public APIs / read-only SELECTs only.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace

from anki.collection import Collection
from anki.errors import NotFoundError

from . import consts, db, hashing
from .records import NoteVersion

DEFAULT_CHUNK_SIZE = 1000
DELETED_HASH = "__deleted__"

_INSERT_VERSION_SQL = (
    "INSERT INTO note_versions"
    " (nid, guid, mid, ts, origin, op_label, fields, field_names, tags, hash, deleted)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_UPSERT_INDEX_SQL = (
    "INSERT INTO note_index (nid, guid, latest_hash, latest_version, alive)"
    " VALUES (?, ?, ?, ?, ?)"
    " ON CONFLICT(nid) DO UPDATE SET guid=excluded.guid,"
    " latest_hash=excluded.latest_hash, latest_version=excluded.latest_version,"
    " alive=excluded.alive"
)


@dataclass(frozen=True)
class BeforeState:
    """A note's pre-edit state, captured when it loads in the editor. Since
    Anki has no pre-edit hook, this in-memory snapshot is the only way to
    record a note's "before" the first time it changes (lazy baseline)."""

    ts: int
    guid: str
    mid: int
    fields: tuple[str, ...]
    field_names: tuple[str, ...]
    tags: tuple[str, ...]
    hash: str


@dataclass(frozen=True)
class NoteScanContext:
    origin: str = consts.ORIGIN_AUTO
    op_label: str = ""
    saw_undo: bool = False
    session_touched_nids: frozenset[int] = frozenset()
    exclude_mids: frozenset[int] = frozenset()
    chunk_size: int = DEFAULT_CHUNK_SIZE
    now_ms: int | None = None
    should_stop: Callable[[], bool] | None = None
    # nid → pre-edit snapshot; the first captured version of such a note is
    # preceded by a 'baseline' row so the change stays restorable.
    before_states: dict[int, BeforeState] = field(default_factory=dict)


@dataclass(frozen=True)
class NoteScanReport:
    captured: int = 0
    deleted: int = 0
    resurrected: int = 0
    interrupted: bool = False
    touched_nids: frozenset[int] = frozenset()


def scan_notes(col: Collection, conn: sqlite3.Connection, ctx: NoteScanContext) -> NoteScanReport:
    """Scan for changed/deleted/resurrected notes and append history rows."""
    now_ms = ctx.now_ms if ctx.now_ms is not None else _now_ms()
    marker = db.meta_get_int(conn, consts.META_NOTE_SCAN_MARKER, 0)
    note_count = int(col.db.scalar("select count(*) from notes"))
    last_count = db.meta_get_int(conn, consts.META_LAST_NOTE_COUNT, -1)

    candidates = col.db.all(
        "select id, mod from notes where mod >= ? order by mod, id", marker
    )

    captured = 0
    touched: set[int] = set()
    interrupted = False

    for chunk in _chunks(candidates, ctx.chunk_size):
        if _stopped(ctx):
            interrupted = True
            break
        marker, chunk_captured = _process_marker_chunk(col, conn, ctx, chunk, marker, now_ms)
        captured += chunk_captured
        touched.update(int(nid) for nid, _mod in chunk)

    if not interrupted and ctx.saw_undo and ctx.session_touched_nids:
        already = {int(nid) for nid, _mod in candidates}
        extras = sorted(nid for nid in ctx.session_touched_nids if nid not in already)
        for chunk in _chunks(extras, ctx.chunk_size):
            if _stopped(ctx):
                interrupted = True
                break
            captured += _process_recheck_chunk(col, conn, ctx, chunk, now_ms)
            touched.update(chunk)

    deleted = resurrected = 0
    if not interrupted and (ctx.saw_undo or note_count != last_count):
        deleted, resurrected, back_nids = _diff_deletions(col, conn, ctx, now_ms)
        touched |= back_nids

    if not interrupted:
        db.meta_set(conn, consts.META_LAST_NOTE_COUNT, str(note_count))

    return NoteScanReport(
        captured=captured,
        deleted=deleted,
        resurrected=resurrected,
        interrupted=interrupted,
        touched_nids=frozenset(touched),
    )


def snapshot_notes(
    col: Collection,
    conn: sqlite3.Connection,
    nids: Iterable[int],
    *,
    origin: str = consts.ORIGIN_MANUAL,
    op_label: str = "",
    now_ms: int | None = None,
) -> int:
    """Manual/restore snapshot: always inserts (dedupe bypassed) — these are
    user-pinned rows. Returns the number of notes snapshotted."""
    resolved_now = now_ms if now_ms is not None else _now_ms()
    ctx = NoteScanContext(origin=origin, op_label=op_label, now_ms=resolved_now)
    count = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for nid in nids:
            if capture_note(col, conn, int(nid), ctx, resolved_now, force=True):
                count += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return count


def capture_note(
    col: Collection,
    conn: sqlite3.Connection,
    nid: int,
    ctx: NoteScanContext,
    now_ms: int,
    *,
    force: bool,
) -> bool:
    """Capture one note's current state if it differs from the latest stored
    version (or unconditionally when ``force``). Caller owns the transaction.
    Returns True when a row was inserted."""
    try:
        note = col.get_note(nid)
    except NotFoundError:
        return False  # deletion set-diff owns disappeared notes
    mid = int(note.mid)
    if mid in ctx.exclude_mids:
        return False
    fields = list(note.fields)
    tags = list(note.tags)
    content_hash = hashing.note_hash(mid, fields, tags)
    index_row = conn.execute(
        "select latest_hash, alive from note_index where nid=?", (nid,)
    ).fetchone()
    is_unchanged = (
        index_row is not None
        and index_row["latest_hash"] == content_hash
        and index_row["alive"] == 1
    )
    if is_unchanged and not force:
        return False
    # Lazy baseline: the first time we ever record this note, if we cached its
    # pre-edit state (from editor load) and it differs, store that as the
    # 'baseline' so the change we're about to record stays restorable.
    if index_row is None:
        before = ctx.before_states.get(nid)
        if before is not None and before.hash != content_hash:
            _insert_before_baseline(conn, nid, before)
    field_names = [f["name"] for f in note.note_type()["flds"]]
    version = NoteVersion(
        nid=nid,
        guid=note.guid,
        mid=mid,
        ts=now_ms,
        origin=ctx.origin,
        op_label=ctx.op_label,
        fields=tuple(fields),
        field_names=tuple(field_names),
        tags=tuple(tags),
        hash=content_hash,
    )
    version_id = _insert_version(conn, version)
    conn.execute(_UPSERT_INDEX_SQL, (nid, note.guid, content_hash, version_id, 1))
    return True


def full_rescan(
    col: Collection,
    conn: sqlite3.Connection,
    *,
    op_label: str = consts.LABEL_FULL_RESCAN,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    now_ms: int | None = None,
    should_stop: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> NoteScanReport:
    """Heal path: hash-compare EVERY note (marker ignored), then the deletion
    set-diff, then RESET the marker to the collection's current max mod.

    The downward marker reset is the point: after a full-sync download the
    collection's mods can regress en masse below our high-water marker, which
    would blind incremental scans; a completed full rescan re-establishes a
    correct baseline for them. Used for unclean shutdowns, detected mod
    regression, and Tools → Full Rescan."""
    resolved_now = now_ms if now_ms is not None else _now_ms()
    ctx = NoteScanContext(
        origin=consts.ORIGIN_AUTO,
        op_label=op_label,
        now_ms=resolved_now,
        chunk_size=chunk_size,
        should_stop=should_stop,
    )
    nids = col.db.list("select id from notes order by id")
    total = len(nids)
    captured = 0
    done = 0
    touched: set[int] = set()
    interrupted = False
    for start in range(0, total, chunk_size):
        if _stopped(ctx):
            interrupted = True
            break
        chunk = nids[start : start + chunk_size]
        captured += _process_recheck_chunk(col, conn, ctx, [int(n) for n in chunk], resolved_now)
        touched.update(int(n) for n in chunk)
        done += len(chunk)
        if progress is not None:
            progress(done, total)

    deleted = resurrected = 0
    if not interrupted:
        deleted, resurrected, back_nids = _diff_deletions(col, conn, ctx, resolved_now)
        touched |= back_nids
        max_mod = int(col.db.scalar("select coalesce(max(mod), 0) from notes"))
        db.meta_set(conn, consts.META_NOTE_SCAN_MARKER, str(max_mod))
        db.meta_set(conn, consts.META_LAST_NOTE_COUNT, str(total))
    return NoteScanReport(
        captured=captured,
        deleted=deleted,
        resurrected=resurrected,
        interrupted=interrupted,
        touched_nids=frozenset(touched),
    )


def list_note_versions(conn: sqlite3.Connection, nid: int) -> list[NoteVersion]:
    """All stored versions of a note, newest first."""
    rows = conn.execute(
        "select id, nid, guid, mid, ts, origin, op_label, fields, field_names,"
        " tags, hash, deleted from note_versions where nid=? order by id desc",
        (nid,),
    ).fetchall()
    return [_row_to_version(row) for row in rows]


# --- internals ---


def _now_ms() -> int:
    return int(time.time() * 1000)


def _stopped(ctx: NoteScanContext) -> bool:
    return ctx.should_stop is not None and ctx.should_stop()


def _chunks(seq: Sequence, size: int) -> Iterator[Sequence]:
    for start in range(0, len(seq), size):
        yield seq[start : start + size]


def _process_marker_chunk(
    col: Collection,
    conn: sqlite3.Connection,
    ctx: NoteScanContext,
    chunk: Sequence,
    marker: int,
    now_ms: int,
) -> tuple[int, int]:
    """One transaction: capture a chunk of marker-query candidates and advance
    the high-water marker to the chunk's max mod. Returns (marker, captured)."""
    captured = 0
    chunk_marker = marker
    conn.execute("BEGIN IMMEDIATE")
    try:
        for nid, mod in chunk:
            if capture_note(col, conn, int(nid), ctx, now_ms, force=False):
                captured += 1
            chunk_marker = max(chunk_marker, int(mod))
        db.meta_set(conn, consts.META_NOTE_SCAN_MARKER, str(chunk_marker))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return chunk_marker, captured


def _process_recheck_chunk(
    col: Collection,
    conn: sqlite3.Connection,
    ctx: NoteScanContext,
    chunk: Sequence[int],
    now_ms: int,
) -> int:
    """Undo/redo re-check: hash-compare session-touched notes whose mod may
    have been rewound below the marker. No marker movement here."""
    captured = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for nid in chunk:
            if capture_note(col, conn, int(nid), ctx, now_ms, force=False):
                captured += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return captured


def _diff_deletions(
    col: Collection,
    conn: sqlite3.Connection,
    ctx: NoteScanContext,
    now_ms: int,
) -> tuple[int, int, set[int]]:
    """Set-diff note_index against the collection: emit deletion marker rows
    for vanished notes and force-capture resurrected ones (undo of a delete
    brings a note back with its ORIGINAL mod — invisible to the marker)."""
    current_nids = {int(nid) for nid in col.db.list("select id from notes")}
    known = conn.execute("select nid, guid, alive from note_index").fetchall()
    dead = [(row["nid"], row["guid"]) for row in known
            if row["alive"] == 1 and row["nid"] not in current_nids]
    back = [row["nid"] for row in known
            if row["alive"] == 0 and row["nid"] in current_nids]

    delete_ctx = replace(ctx, op_label=ctx.op_label or consts.LABEL_DELETE_NOTE)
    revive_ctx = replace(ctx, op_label=ctx.op_label or consts.LABEL_UNDO_DELETE)

    conn.execute("BEGIN IMMEDIATE")
    try:
        for nid, guid in dead:
            _insert_deletion_marker(conn, nid, guid, delete_ctx, now_ms)
        revived: set[int] = set()
        for nid in back:
            if capture_note(col, conn, int(nid), revive_ctx, now_ms, force=True):
                revived.add(int(nid))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(dead), len(revived), revived


def _insert_before_baseline(conn: sqlite3.Connection, nid: int, before: BeforeState) -> None:
    """Persist a cached pre-edit snapshot as this note's baseline row."""
    version = NoteVersion(
        nid=nid,
        guid=before.guid,
        mid=before.mid,
        ts=before.ts,
        origin=consts.ORIGIN_BASELINE,
        op_label="",
        fields=before.fields,
        field_names=before.field_names,
        tags=before.tags,
        hash=before.hash,
    )
    version_id = _insert_version(conn, version)
    conn.execute(_UPSERT_INDEX_SQL, (nid, before.guid, before.hash, version_id, 1))


def _insert_deletion_marker(
    conn: sqlite3.Connection, nid: int, guid: str, ctx: NoteScanContext, now_ms: int
) -> None:
    last_mid = conn.execute(
        "select mid from note_versions where nid=? order by id desc limit 1", (nid,)
    ).fetchone()
    version = NoteVersion(
        nid=nid,
        guid=guid,
        mid=int(last_mid[0]) if last_mid is not None else 0,
        ts=now_ms,
        origin=ctx.origin,
        op_label=ctx.op_label,
        fields=(),
        field_names=(),
        tags=(),
        hash=DELETED_HASH,
        deleted=True,
    )
    version_id = _insert_version(conn, version)
    conn.execute(_UPSERT_INDEX_SQL, (nid, guid, DELETED_HASH, version_id, 0))


def _insert_version(conn: sqlite3.Connection, version: NoteVersion) -> int:
    cursor = conn.execute(
        _INSERT_VERSION_SQL,
        (
            version.nid,
            version.guid,
            version.mid,
            version.ts,
            version.origin,
            version.op_label,
            _dump(version.fields),
            _dump(version.field_names),
            _dump(version.tags),
            version.hash,
            1 if version.deleted else 0,
        ),
    )
    return int(cursor.lastrowid)


def _row_to_version(row: sqlite3.Row) -> NoteVersion:
    return NoteVersion(
        id=row["id"],
        nid=row["nid"],
        guid=row["guid"],
        mid=row["mid"],
        ts=row["ts"],
        origin=row["origin"],
        op_label=row["op_label"],
        fields=tuple(json.loads(row["fields"])),
        field_names=tuple(json.loads(row["field_names"])),
        tags=tuple(json.loads(row["tags"])),
        hash=row["hash"],
        deleted=bool(row["deleted"]),
    )


def _dump(values: Iterable[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False)
