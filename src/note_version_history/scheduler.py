"""aqt-side runtime wiring: profile lifecycle + the live capture pipeline.

Threading model:

- Hook handlers run on the MAIN thread; they only mutate small in-memory
  state and (re)start the debounce timer.
- Scans run in a QueryOp background thread with their OWN short-lived sqlite
  connection. The Runtime's main connection is used on the main thread only
  (menus, wizard state, the synchronous final scan on close).
- ``session_touched`` accumulates every nid captured this session. Anki's
  undo queue clears when the collection closes, so undo can only revert
  session ops — the undo re-check stays bounded and exact (see
  capture_notes docstring for why the mod marker alone cannot see undo).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from aqt import gui_hooks, mw
from aqt.operations import QueryOp
from aqt.qt import QTimer, qconnect

from . import (
    baseline,
    capture_media,
    capture_notes,
    capture_notetypes,
    consts,
    db,
    hashing,
    i18n,
    profiles,
    prune,
)

from .appconfig import AddonConfig, config_from_dict
from .blobstore import BlobStore
from .capture_notes import NoteScanContext

_RESCAN_DELAY_MS = 200
_INITIAL_SCAN_DELAY_MS = 1500
_BEFORE_CACHE_MAX = 512


@dataclass
class PendingWork:
    """Change flags accumulated by hooks between debounce firings."""

    labels: list[str] = field(default_factory=list)
    saw_undo: bool = False
    want_notes: bool = False
    want_notetypes: bool = False

    def consume(self) -> "PendingWork":
        taken = PendingWork(
            labels=list(self.labels),
            saw_undo=self.saw_undo,
            want_notes=self.want_notes,
            want_notetypes=self.want_notetypes,
        )
        self.labels.clear()
        self.saw_undo = False
        self.want_notes = False
        self.want_notetypes = False
        return taken


@dataclass
class Runtime:
    """Mutable per-profile session state (exists only while a profile is open)."""

    profile_name: str
    data_dir: Path
    conn: sqlite3.Connection  # MAIN THREAD ONLY
    blobs: BlobStore
    unclean_shutdown: bool
    debounce: QTimer
    heartbeat: QTimer
    pending: PendingWork = field(default_factory=PendingWork)
    session_touched: set[int] = field(default_factory=set)
    before_cache: dict[int, capture_notes.BeforeState] = field(default_factory=dict)
    scan_running: bool = False
    rescan_requested: bool = False
    prev_undo_status: object = None
    baseline_running: bool = False


_runtime: Runtime | None = None


def runtime() -> Runtime | None:
    return _runtime


def addon_dir() -> Path:
    return Path(__file__).resolve().parent


def user_files_dir() -> Path:
    return addon_dir() / "user_files"


def profile_db_path(rt: Runtime) -> Path:
    return profiles.history_db_path(rt.data_dir)


def load_config() -> AddonConfig:
    raw = mw.addonManager.getConfig(__name__) if mw is not None else None
    return config_from_dict(raw)


def apply_language() -> None:
    """Resolve and set the UI language (config override → Anki's language →
    English). Must run BEFORE the Tools menu is built at addon load, or the
    menu freezes in English; also re-applied on each profile open."""
    i18n.set_language(i18n.resolve_language(load_config().language, _anki_lang()))


def setup() -> None:
    gui_hooks.profile_did_open.append(_on_profile_open)
    gui_hooks.profile_will_close.append(_on_profile_close)
    gui_hooks.operation_did_execute.append(_on_operation_did_execute)
    gui_hooks.editor_did_load_note.append(_on_editor_load_note)


def request_scan(*, notes: bool = False, notetypes: bool = False, delay_ms: int = 300) -> None:
    """Public entry for other modules (wizard completion, manual triggers)."""
    rt = _runtime
    if rt is None:
        return
    rt.pending.want_notes |= notes
    rt.pending.want_notetypes |= notetypes
    rt.debounce.start(delay_ms)


def request_full_rescan(on_done=None) -> bool:
    """Background heal: hash-compare every note, run the deletion diff, reset
    the marker (see capture_notes.full_rescan). Menu + unclean-shutdown path."""
    rt = _runtime
    if rt is None or mw is None or mw.col is None:
        return False
    if not baseline.notes_baseline_done(rt.conn):
        return False
    db_path = profile_db_path(rt)

    def report_progress(done: int, total: int) -> None:
        mw.taskman.run_on_main(
            lambda: mw.progress.update(
                label=i18n.tr("rescan_progress_label", done=done, total=total),
                value=done,
                max=total,
            )
        )

    def op(col):
        own = db.open_history_db(db_path)
        try:
            note_report = capture_notes.full_rescan(col, own, progress=report_progress)
            capture_notetypes.scan_notetypes(col, own)
            return note_report
        finally:
            own.close()

    def on_success(report) -> None:
        current = _runtime
        if current is not None:
            current.session_touched.update(report.touched_nids)
        if on_done is not None:
            on_done(report)

    def on_failure(exc: BaseException) -> None:
        print(f"note_version_history: full rescan failed: {exc!r}")

    QueryOp(parent=mw, op=op, success=on_success).failure(on_failure).with_progress(
        i18n.tr("rescan_progress")
    ).run_in_background()
    return True


def request_media_scan(on_done=None) -> bool:
    """Background full media scan. Requires a completed media baseline —
    otherwise a 'scan' would silently BE a baseline. Returns False if not
    runnable right now."""
    rt = _runtime
    if not consts.MEDIA_ENABLED:
        return False
    if rt is None or mw is None or mw.col is None:
        return False
    if baseline.media_baseline_state(rt.conn) != baseline.STATE_DONE:
        return False
    db_path = profile_db_path(rt)
    blobs_root = profiles.blobs_dir(rt.data_dir)

    def op(col):
        own = db.open_history_db(db_path)
        try:
            return capture_media.full_scan(col, own, BlobStore(blobs_root))
        finally:
            own.close()

    def on_success(report) -> None:
        if on_done is not None:
            on_done(report)

    def on_failure(exc: BaseException) -> None:
        print(f"note_version_history: media scan failed: {exc!r}")

    QueryOp(parent=mw, op=op, success=on_success).failure(on_failure).run_in_background()
    return True


# --- profile lifecycle ---


def _on_profile_open() -> None:
    global _runtime
    if _runtime is not None:
        _close_runtime()  # defensive: profile switch without close event
    config = load_config()
    apply_language()
    profile_name = mw.pm.name
    data_dir = profiles.profile_data_dir(user_files_dir(), profile_name)
    try:
        conn = db.open_history_db(profiles.history_db_path(data_dir))
    except db.HistoryDbTooNew:
        _show_warning(i18n.tr("db_too_new"))
        return
    except (db.HistoryDbError, sqlite3.Error, OSError) as exc:
        _show_warning(i18n.tr("db_open_failed", error=str(exc)))
        return
    unclean = db.meta_get(conn, consts.META_CLEAN_SHUTDOWN) == "0"
    fresh = db.meta_get(conn, consts.META_NOTE_SCAN_MARKER) is None
    db.meta_set(conn, consts.META_CLEAN_SHUTDOWN, "0")
    db.meta_set(conn, consts.META_PROFILE_NAME, profile_name)

    debounce = QTimer(mw)
    debounce.setSingleShot(True)
    qconnect(debounce.timeout, _on_debounce_fired)
    heartbeat = QTimer(mw)
    qconnect(heartbeat.timeout, _on_heartbeat)

    _runtime = Runtime(
        profile_name=profile_name,
        data_dir=data_dir,
        conn=conn,
        blobs=BlobStore(profiles.blobs_dir(data_dir)),
        unclean_shutdown=unclean,
        debounce=debounce,
        heartbeat=heartbeat,
    )
    _runtime.prev_undo_status = _safe_undo_status()
    if unclean and baseline.notes_baseline_done(conn):
        # A previous session died mid-flight: schedule the heal path once the
        # UI settles (full rescan hash-compares everything and resets the marker).
        QTimer.singleShot(3_000, lambda: request_full_rescan())

    if config.heartbeat_scan_minutes > 0:
        heartbeat.start(config.heartbeat_scan_minutes * 60_000)

    # Lazy-baseline model: never capture the existing collection up front. A
    # fresh DB just records the capture start point (and baselines the few note
    # types); per-note baselines happen on first edit via the editor-load
    # cache. Later opens do a catch-up scan for changes made while away.
    if fresh and mw.col is not None:
        _init_lazy_install(_runtime.conn)
    else:
        request_scan(notes=True, notetypes=True, delay_ms=_INITIAL_SCAN_DELAY_MS)
    if (
        config.capture_media
        and config.media_scan_on_profile_open
        and baseline.media_baseline_state(_runtime.conn) == baseline.STATE_DONE
    ):
        request_media_scan()
    from .ui import baseline_wizard  # lazy: avoids import cycle

    baseline_wizard.maybe_media_step()  # resume a pending media baseline only


def _init_lazy_install(conn: sqlite3.Connection) -> None:
    """Fresh DB: set the notes capture start point to 'now' so the pre-existing
    collection isn't captured wholesale (only notes edited from here on get a
    baseline, via the editor-load cache). Note types are few, so baseline them
    outright for full template/CSS coverage."""
    max_mod = int(mw.col.db.scalar("select coalesce(max(mod), 0) from notes"))
    count = int(mw.col.db.scalar("select count(*) from notes"))
    # max_mod + 1 (not max_mod): the inclusive `mod >= marker` scan would else
    # grab the single most-recently-modified note. Post-install edits always
    # land in a later second than the collection's last pre-install edit, so
    # excluding exactly max_mod loses nothing. (The running marker stays
    # inclusive, preserving same-second re-edit capture.)
    db.meta_set(conn, consts.META_NOTE_SCAN_MARKER, str(max_mod + 1))
    db.meta_set(conn, consts.META_LAST_NOTE_COUNT, str(count))
    try:
        capture_notetypes.scan_notetypes(
            mw.col, conn, origin=consts.ORIGIN_BASELINE, op_label=""
        )
    except Exception as exc:  # never block profile open
        print(f"note_version_history: notetype baseline failed: {exc!r}")


def _on_profile_close() -> None:
    _close_runtime()


def _close_runtime() -> None:
    global _runtime
    rt = _runtime
    if rt is None:
        return
    try:
        rt.debounce.stop()
        rt.heartbeat.stop()
        _final_scan_on_close(rt)
        db.meta_set(rt.conn, consts.META_CLEAN_SHUTDOWN, "1")
        rt.conn.close()
    except sqlite3.Error:
        pass  # closing must never block Anki shutdown
    finally:
        _runtime = None


def _final_scan_on_close(rt: Runtime) -> None:
    """Synchronous last scan (main thread, main connection): closes the
    "edit then immediately quit" debounce gap. Must never block shutdown."""
    if rt.baseline_running:  # a full baseline is running; let it own capture
        return
    if mw is None or mw.col is None:
        return
    config = load_config()
    if not config.auto_capture:
        return
    try:
        work = rt.pending.consume()
        ctx = _build_context(rt, work, config)
        report = capture_notes.scan_notes(mw.col, rt.conn, ctx)
        capture_notetypes.scan_notetypes(mw.col, rt.conn, op_label=ctx.op_label)
        if consts.MEDIA_ENABLED and config.capture_media:
            capture_media.capture_files_for_notes(
                mw.col, rt.conn, rt.blobs, report.touched_nids, op_label=ctx.op_label
            )
            if (
                config.media_scan_on_profile_close
                and baseline.media_baseline_state(rt.conn) == baseline.STATE_DONE
            ):
                capture_media.full_scan(mw.col, rt.conn, rt.blobs)
    except Exception:
        pass


# --- capture hooks ---


def _on_operation_did_execute(changes, handler: object) -> None:
    rt = _runtime
    if rt is None or handler is consts.RESTORE_INITIATOR:
        return
    relevant = bool(
        getattr(changes, "note", False)
        or getattr(changes, "tag", False)
        or getattr(changes, "note_text", False)
        or getattr(changes, "notetype", False)
    )
    if not relevant:
        return
    config = load_config()
    if not config.auto_capture:
        return
    kind, label = _classify_operation(rt)
    if kind != "normal":
        rt.pending.saw_undo = True
    rt.pending.want_notes |= bool(
        getattr(changes, "note", False)
        or getattr(changes, "tag", False)
        or getattr(changes, "note_text", False)
        or kind != "normal"
    )
    rt.pending.want_notetypes |= bool(getattr(changes, "notetype", False))
    if label:
        rt.pending.labels.append(label)
    rt.debounce.start(config.debounce_ms)


def _on_editor_load_note(editor) -> None:
    """Cache a note's pre-edit state when it loads in the editor. Anki has no
    pre-edit hook, so this is the only place we can see a note's "before".
    Consumed by the scan when the note is first captured (lazy baseline)."""
    rt = _runtime
    if rt is None:
        return
    note = getattr(editor, "note", None)
    if note is None or not getattr(note, "id", 0):
        return  # brand-new note in the Add screen has no id yet
    try:
        mid = int(note.mid)
        fields = tuple(note.fields)
        tags = tuple(note.tags)
        field_names = tuple(f["name"] for f in note.note_type()["flds"])
    except Exception:
        return
    rt.before_cache[int(note.id)] = capture_notes.BeforeState(
        ts=int(time.time() * 1000),
        guid=note.guid,
        mid=mid,
        fields=fields,
        field_names=field_names,
        tags=tags,
        hash=hashing.note_hash(mid, fields, tags),
    )
    if len(rt.before_cache) > _BEFORE_CACHE_MAX:
        # dict preserves insertion order → drop the oldest half
        for key in list(rt.before_cache)[: _BEFORE_CACHE_MAX // 2]:
            del rt.before_cache[key]


def _classify_operation(rt: Runtime) -> tuple[str, str]:
    """Compare undo_status against the cached previous status: after an undo,
    the undone op's label moves from .undo to .redo (and vice versa)."""
    status = _safe_undo_status()
    if status is None:
        return "normal", ""
    prev = rt.prev_undo_status
    rt.prev_undo_status = status
    prev_undo = getattr(prev, "undo", "") if prev is not None else ""
    prev_redo = getattr(prev, "redo", "") if prev is not None else ""
    if status.redo and status.redo == prev_undo:
        return "undo", i18n.tr("label_undo", label=status.redo)
    if status.undo and status.undo == prev_redo:
        return "redo", i18n.tr("label_redo", label=status.undo)
    return "normal", status.undo


def _safe_undo_status():
    try:
        if mw is not None and mw.col is not None:
            return mw.col.undo_status()
    except Exception:
        pass
    return None


# --- scan orchestration ---


def _on_debounce_fired() -> None:
    _start_scan()


def _on_heartbeat() -> None:
    """Catch-all for changes that arrive without a usable hook (sync merges)."""
    rt = _runtime
    if rt is None or not load_config().auto_capture:
        return
    rt.pending.want_notes = True
    rt.pending.want_notetypes = True
    _start_scan()


def _start_scan() -> None:
    rt = _runtime
    if rt is None or mw is None or mw.col is None:
        return
    if rt.baseline_running:
        return  # a full baseline is running; let it own capture
    if rt.scan_running:
        rt.rescan_requested = True
        return
    config = load_config()
    work = rt.pending.consume()
    if not (work.want_notes or work.want_notetypes):
        return
    ctx = _build_context(rt, work, config)
    want_notetypes = work.want_notetypes
    db_path = profile_db_path(rt)
    rt.scan_running = True

    capture_media_files = consts.MEDIA_ENABLED and config.capture_media
    blobs_root = profiles.blobs_dir(rt.data_dir)

    def op(col):
        own = db.open_history_db(db_path)
        try:
            note_report = capture_notes.scan_notes(col, own, ctx)
            if want_notetypes:
                capture_notetypes.scan_notetypes(col, own, op_label=ctx.op_label)
            if capture_media_files and note_report.touched_nids:
                capture_media.capture_files_for_notes(
                    col,
                    own,
                    BlobStore(blobs_root),
                    note_report.touched_nids,
                    op_label=ctx.op_label,
                )
            if prune.maintenance_due(own):
                prune.run_maintenance(own, BlobStore(blobs_root), config.retention)
            return note_report
        finally:
            own.close()

    def on_success(report) -> None:
        current = _runtime
        if current is None:
            return
        current.scan_running = False
        current.session_touched.update(report.touched_nids)
        if current.rescan_requested:
            current.rescan_requested = False
            current.debounce.start(_RESCAN_DELAY_MS)

    def on_failure(exc: BaseException) -> None:
        current = _runtime
        if current is not None:
            current.scan_running = False
        print(f"note_version_history: scan failed: {exc!r}")

    QueryOp(parent=mw, op=op, success=on_success).failure(on_failure).run_in_background()


def _build_context(rt: Runtime, work: PendingWork, config: AddonConfig) -> NoteScanContext:
    return NoteScanContext(
        origin=consts.ORIGIN_AUTO,
        op_label=_format_label(work.labels),
        saw_undo=work.saw_undo,
        session_touched_nids=frozenset(rt.session_touched),
        exclude_mids=frozenset(config.exclude_notetype_ids),
        before_states=dict(rt.before_cache),  # snapshot for the background scan
    )


def _format_label(labels: list[str]) -> str:
    unique = list(dict.fromkeys(label for label in labels if label))
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    return f"{unique[-1]} (+{len(unique) - 1})"


# --- misc ---


def _anki_lang() -> str:
    try:
        import anki.lang

        return getattr(anki.lang, "current_lang", "") or "en"
    except Exception:
        return "en"


def _show_warning(text: str) -> None:
    from aqt.utils import showWarning

    showWarning(text, title=i18n.tr("addon_name"))
