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
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from threading import current_thread, main_thread

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
from .install_lifecycle import run_on_owner_thread_sync
from .workqueue import PendingWork, heal_scope, retry_delay, shutdown_can_be_clean

_RESCAN_DELAY_MS = 200
_INITIAL_SCAN_DELAY_MS = 1500
_SYNC_SCAN_DELAY_MS = 300
_BEFORE_CACHE_MAX = 512
_MAX_SCAN_FAILURES = 3
# Profile-open consent prompt: first fires after the catch-up scan (1.5s) and
# the heal rescan (3s) have been scheduled; while Anki is busy (startup sync,
# any progress window) it retries a few times, then defers to the next open.
_PROFILE_OPEN_PROMPT_DELAY_MS = 4_000
_PROFILE_OPEN_PROMPT_RETRY_MS = 5_000
_PROFILE_OPEN_PROMPT_MAX_RETRIES = 6
_INSTALL_TEARDOWN_TIMEOUT_SECONDS = 30.0


@dataclass
class Runtime:
    """Mutable per-profile session state (exists only while a profile is open)."""

    profile_name: str
    data_dir: Path
    conn: sqlite3.Connection  # MAIN THREAD ONLY
    blobs: BlobStore | None
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
    # full rescan queued behind a running scan/baseline (single-flight)
    full_rescan_pending: bool = False
    pending_rescan_done: object = None
    media_scan_running: bool = False
    # sync coordination
    sync_active: bool = False
    full_sync_seen: bool = False
    pre_sync_usn: int = -1
    # repeated-failure surfacing
    scan_failures: int = 0
    scan_warning_shown: bool = False
    mutation_owner: str | None = None


_runtime: Runtime | None = None
_update_started = False


def runtime() -> Runtime | None:
    return _runtime


def mutation_busy(rt: Runtime | None = None) -> bool:
    target = rt if rt is not None else _runtime
    return target is not None and target.mutation_owner is not None


def acquire_mutation(rt: Runtime, owner: str) -> bool:
    if rt.mutation_owner is not None:
        return False
    rt.mutation_owner = owner
    return True


def release_mutation(rt: Runtime, owner: str) -> None:
    if rt.mutation_owner == owner:
        rt.mutation_owner = None


def blob_store(rt: Runtime) -> BlobStore:
    if rt.blobs is None:
        rt.blobs = BlobStore(profiles.blobs_dir(rt.data_dir))
    return rt.blobs


def addon_dir() -> Path:
    return Path(__file__).resolve().parent


def user_files_dir() -> Path:
    return addon_dir() / "user_files"


def profile_db_path(rt: Runtime) -> Path:
    return profiles.history_db_path(rt.data_dir)


_config_cache: AddonConfig | None = None


def load_config() -> AddonConfig:
    """The add-on config, cached: ``getConfig`` re-parses meta.json from disk on
    every call and this runs on every note-relevant operation. Invalidated via
    ``setConfigUpdatedAction`` when Anki's config editor saves."""
    global _config_cache
    if _config_cache is None:
        raw = mw.addonManager.getConfig(__name__) if mw is not None else None
        _config_cache = config_from_dict(raw)
    return _config_cache


def _on_config_updated(_new_config: object) -> None:
    """Config editor saved: drop the cache and re-apply settings that would
    otherwise only take effect on the next profile open."""
    global _config_cache
    _config_cache = None
    apply_language()
    rt = _runtime
    if rt is None:
        return
    config = load_config()
    rt.heartbeat.stop()
    if not config.auto_capture:
        rt.debounce.stop()
    if (
        config.auto_capture
        and config.heartbeat_scan_minutes > 0
        and not rt.sync_active
    ):
        rt.heartbeat.start(config.heartbeat_scan_minutes * 60_000)


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
    gui_hooks.sync_will_start.append(_on_sync_will_start)
    gui_hooks.sync_did_finish.append(_on_sync_did_finish)
    gui_hooks.collection_will_temporarily_close.append(_on_collection_will_temporarily_close)
    install_hook = getattr(gui_hooks, "addon_manager_will_install_addon", None)
    if install_hook is not None:
        install_hook.append(_on_addon_manager_will_install)
    delete_hook = getattr(gui_hooks, "addons_dialog_will_delete_addons", None)
    if delete_hook is not None:
        delete_hook.append(_on_addons_will_delete)
    mw.addonManager.setConfigUpdatedAction(__name__, _on_config_updated)


def _on_addon_manager_will_install(manager, module: str) -> None:
    """Release every handle before Anki moves ``user_files`` out of the way.

    AnkiWeb updates invoke this hook on the serialized collection worker;
    local ``.ankiaddon`` installs invoke it synchronously on the Qt main
    thread. The persistent connection and timers belong to the main thread,
    so the worker path must hand off and wait for completion.
    """
    if module != manager.addonFromModule(__name__):
        return
    on_main = current_thread() is main_thread()
    run_on_owner_thread_sync(
        _prepare_for_install_on_main,
        mw.taskman.run_on_main,
        already_on_owner=on_main,
        timeout_seconds=_INSTALL_TEARDOWN_TIMEOUT_SECONDS,
    )
    if on_main:
        _drain_collection_executor()


def _on_addons_will_delete(_dialog, modules: list[str]) -> None:
    """Make removal from Anki's add-on dialog safe on Windows as well."""
    if mw.addonManager.addonFromModule(__name__) not in modules:
        return
    _prepare_for_install_on_main()
    _drain_collection_executor()


def _prepare_for_install_on_main() -> None:
    """Pause the old in-memory code until Anki is restarted after install."""
    global _update_started
    _update_started = True
    _close_runtime(for_update=True)


def _drain_collection_executor() -> None:
    """Wait for local-install QueryOps to close their short-lived DB handles.

    This is only called from the main-thread local install/delete paths. An
    AnkiWeb installer already occupies the same single-worker executor, where
    submitting and waiting for this barrier would deadlock.
    """
    barrier = mw.taskman.run_in_background(lambda: None)
    try:
        barrier.result(timeout=_INSTALL_TEARDOWN_TIMEOUT_SECONDS)
    except FutureTimeoutError as exc:
        raise RuntimeError(
            "timed out waiting for version-history background work; "
            "refusing to continue installation"
        ) from exc


def request_scan(*, notes: bool = False, notetypes: bool = False, delay_ms: int = 300) -> None:
    """Public entry for other modules (wizard completion, manual triggers)."""
    rt = _runtime
    if rt is None:
        return
    rt.pending.want_notes |= notes
    rt.pending.want_notetypes |= notetypes
    rt.debounce.start(delay_ms)


def request_full_rescan(on_done=None) -> bool:
    """Background heal for marker regression / unclean shutdown. For a baselined
    collection this hash-compares every note and resets the marker
    (:func:`capture_notes.full_rescan`); for a lazy install (no baseline) it
    re-checks only tracked notes and re-anchors the marker
    (:func:`capture_notes.rescan_indexed`), never dumping the whole collection.

    Participates in the single-flight scan gate: if a scan or baseline is already
    running, it is queued and started when that one finishes."""
    rt = _runtime
    if rt is None or mw is None or mw.col is None:
        return False
    if mutation_busy(rt) or rt.full_rescan_pending:
        rt.full_rescan_pending = True
        rt.pending_rescan_done = on_done
        return True
    if not acquire_mutation(rt, "full_rescan"):
        return False
    rt.scan_running = True
    rt_token = rt
    baselined = baseline.notes_baseline_done(rt.conn)
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
            if baselined:
                note_report = capture_notes.full_rescan(col, own, progress=report_progress)
            else:
                note_report = capture_notes.rescan_indexed(col, own)
            capture_notetypes.scan_notetypes(col, own)
            return note_report
        finally:
            own.close()

    def on_success(report) -> None:
        if _runtime is not rt_token:
            return
        rt_token.scan_running = False
        rt_token.scan_failures = 0
        release_mutation(rt_token, "full_rescan")
        # NB: deliberately NOT folding report.touched_nids into
        # session_touched — a full rescan touches the whole collection, which
        # would make every later undo re-check everything.
        if on_done is not None:
            on_done(report)
        _drain_pending_scan()

    def on_failure(exc: BaseException) -> None:
        if _runtime is not rt_token:
            return
        rt_token.scan_running = False
        release_mutation(rt_token, "full_rescan")
        _note_scan_failure(exc, "full rescan", rt_token)
        delay = _retry_delay(rt_token.scan_failures)
        rt_token.full_rescan_pending = True
        rt_token.pending_rescan_done = on_done
        rt_token.debounce.start(delay)

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
    if mutation_busy(rt) or rt.media_scan_running:
        return False  # a baseline or another media scan owns the media DB now
    if baseline.media_baseline_state(rt.conn) != baseline.STATE_DONE:
        return False
    db_path = profile_db_path(rt)
    blobs_root = profiles.blobs_dir(rt.data_dir)
    if not acquire_mutation(rt, "media_scan"):
        return False
    rt.media_scan_running = True
    rt_token = rt

    def op(col):
        own = db.open_history_db(db_path)
        try:
            return capture_media.full_scan(col, own, BlobStore(blobs_root))
        finally:
            own.close()

    def on_success(report) -> None:
        if _runtime is not rt_token:
            return
        rt_token.media_scan_running = False
        release_mutation(rt_token, "media_scan")
        if on_done is not None:
            on_done(report)
        _drain_pending_scan()

    def on_failure(exc: BaseException) -> None:
        if _runtime is not rt_token:
            return
        rt_token.media_scan_running = False
        release_mutation(rt_token, "media_scan")
        print(f"note_version_history: media scan failed: {exc!r}")
        _drain_pending_scan()

    QueryOp(parent=mw, op=op, success=on_success).failure(on_failure).run_in_background()
    return True


# --- profile lifecycle ---


def _on_profile_open() -> None:
    global _runtime
    if _update_started:
        return
    if _runtime is not None:
        _close_runtime()  # defensive: profile switch without close event
    config = load_config()
    apply_language()
    profile_name = mw.pm.name
    conn: sqlite3.Connection | None = None
    try:
        profile_prefs = getattr(mw.pm, "profile", None)
        saved_key = (
            profile_prefs.get(profiles.PROFILE_STORAGE_KEY)
            if isinstance(profile_prefs, dict)
            else None
        )
        storage_key, changed = profiles.choose_storage_key(
            user_files_dir(), profile_name, saved_key
        )
        data_dir = profiles.profile_data_dir_for_key(
            user_files_dir(), storage_key
        )
        conn = db.open_history_db(profiles.history_db_path(data_dir))
        if changed and isinstance(profile_prefs, dict):
            profile_prefs[profiles.PROFILE_STORAGE_KEY] = storage_key
            save_profile = getattr(mw.pm, "save", None)
            if callable(save_profile):
                save_profile()
    except db.HistoryDbTooNew:
        if conn is not None:
            conn.close()
        _show_warning(i18n.tr("db_too_new"))
        return
    except Exception as exc:
        if conn is not None:
            conn.close()
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
        blobs=None,
        unclean_shutdown=unclean,
        debounce=debounce,
        heartbeat=heartbeat,
    )
    _runtime.prev_undo_status = _safe_undo_status()
    unclean_heal = heal_scope(unclean, baseline.notes_baseline_done(conn))

    if config.auto_capture and config.heartbeat_scan_minutes > 0:
        heartbeat.start(config.heartbeat_scan_minutes * 60_000)

    # Lazy-baseline model: never capture the existing collection up front. A
    # fresh DB just records the capture start point (and baselines the few note
    # types); per-note baselines happen on first edit via the editor-load
    # cache. Later opens do a catch-up scan for changes made while away. A
    # one-time full-baseline offer is scheduled at the end of this function.
    if fresh and mw.col is not None:
        _init_lazy_install(
            _runtime.conn, capture_notetype_baseline=config.auto_capture
        )
    elif config.auto_capture and unclean_heal is not None:
        # A previous session died mid-flight: the full rescan hash-compares
        # everything and resets the marker, subsuming the catch-up scan — so
        # schedule ONLY it (both on the same connection would otherwise race).
        QTimer.singleShot(3_000, lambda: request_full_rescan())
    elif config.auto_capture:
        request_scan(notes=True, notetypes=True, delay_ms=_INITIAL_SCAN_DELAY_MS)
    if (
        config.auto_capture
        and config.capture_media
        and config.media_scan_on_profile_open
        and baseline.media_baseline_state(_runtime.conn) == baseline.STATE_DONE
        and _media_scan_stale(_runtime.conn)
    ):
        request_media_scan()
    rt_token = _runtime  # the lambda must not late-bind a newer profile's runtime
    QTimer.singleShot(
        _PROFILE_OPEN_PROMPT_DELAY_MS,
        lambda: _maybe_profile_open_prompt(rt_token),
    )


def _media_scan_stale(conn: sqlite3.Connection) -> bool:
    """Throttle the profile-open full media scan: a whole-folder stat pass on
    every open is wasted work when profiles are switched often. Manual scans
    (media dialog) bypass this."""
    last = db.meta_get_int(conn, consts.META_LAST_MEDIA_SCAN_MS, 0)
    now = int(time.time() * 1000)
    return now - last >= consts.MEDIA_SCAN_MIN_INTERVAL_MS


def _maybe_profile_open_prompt(rt_token: Runtime, attempt: int = 0) -> None:
    """Delayed profile-open consent prompt (first-run baseline offer, or the
    media resume step). Deferred while Anki is busy — startup auto-sync, the
    heal rescan, any progress window — and after a few retries it gives up for
    this session; meta state is untouched, so the next open offers again.
    ``rt_token`` pins the profile this chain was scheduled for: a profile
    switch invalidates it (the new open schedules its own chain), so prompts
    never double up."""
    rt = _runtime
    if rt is None or rt is not rt_token or mw is None or mw.col is None:
        return
    busy = (
        rt.sync_active
        or rt.scan_running
        or rt.baseline_running
        or rt.media_scan_running
        or mw.progress.busy()
    )
    if busy:
        if attempt < _PROFILE_OPEN_PROMPT_MAX_RETRIES:
            QTimer.singleShot(
                _PROFILE_OPEN_PROMPT_RETRY_MS,
                lambda: _maybe_profile_open_prompt(rt_token, attempt + 1),
            )
        return
    from .ui import baseline_wizard  # lazy: avoids import cycle

    baseline_wizard.maybe_profile_open_prompt()


def _init_lazy_install(
    conn: sqlite3.Connection, *, capture_notetype_baseline: bool = True
) -> None:
    """Fresh DB: set the notes capture start point to 'now' so the pre-existing
    collection isn't captured wholesale (only notes edited from here on get a
    baseline, via the editor-load cache). When automatic capture is enabled,
    the few note types are baselined outright for template/CSS coverage."""
    capture_notes.initialize_lazy_boundary(mw.col, conn)
    if not capture_notetype_baseline:
        return
    try:
        capture_notetypes.scan_notetypes(
            mw.col, conn, origin=consts.ORIGIN_BASELINE, op_label=""
        )
    except Exception as exc:  # never block profile open
        print(f"note_version_history: notetype baseline failed: {exc!r}")


def _on_profile_close() -> None:
    _close_runtime()


def _close_runtime(*, for_update: bool = False) -> None:
    global _runtime
    rt = _runtime
    if rt is None:
        return
    clean = False
    try:
        rt.debounce.stop()
        rt.heartbeat.stop()
        final_succeeded = False
        if (
            not for_update
            and not mutation_busy(rt)
            and not rt.sync_active
            and not rt.full_rescan_pending
        ):
            final_succeeded = _final_scan_on_close(rt)
        clean = shutdown_can_be_clean(
            mutation_busy=mutation_busy(rt),
            sync_active=rt.sync_active,
            full_rescan_pending=rt.full_rescan_pending,
            final_scan_succeeded=final_succeeded,
        )
        if clean:
            db.meta_set(rt.conn, consts.META_CLEAN_SHUTDOWN, "1")
    except Exception as exc:
        print(f"note_version_history: profile close failed: {exc!r}")
    finally:
        try:
            rt.conn.close()
        except sqlite3.Error:
            if for_update:
                raise
        finally:
            _runtime = None


def _final_scan_on_close(rt: Runtime) -> bool:
    """Synchronous last scan (main thread, main connection): closes the
    "edit then immediately quit" debounce gap. Must never block shutdown."""
    if rt.baseline_running or rt.scan_running or rt.media_scan_running:
        return False
    if mw is None or mw.col is None:
        return True
    config = load_config()
    if not config.auto_capture:
        return True
    try:
        work = rt.pending.consume()
        ctx = _build_context(rt, work, config)
        report = capture_notes.scan_notes(mw.col, rt.conn, ctx)
        capture_notetypes.scan_notetypes(mw.col, rt.conn, op_label=ctx.op_label)
        if consts.MEDIA_ENABLED and config.capture_media:
            capture_media.capture_files_for_notes(
                mw.col, rt.conn, blob_store(rt), report.touched_nids
            )
            if (
                config.media_scan_on_profile_close
                and baseline.media_baseline_state(rt.conn) == baseline.STATE_DONE
            ):
                capture_media.full_scan(mw.col, rt.conn, blob_store(rt))
        return not report.interrupted
    except Exception as exc:  # must never block shutdown, but leave a trace
        print(f"note_version_history: final scan on close failed: {exc!r}")
        return False


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


# --- sync coordination ---


def _on_sync_will_start() -> None:
    """Pause capture and record the pre-sync usn high-water so the post-sync
    hook can find exactly the notes this sync round changed."""
    rt = _runtime
    if rt is None or mw is None or mw.col is None:
        return
    rt.sync_active = True
    rt.debounce.stop()
    rt.heartbeat.stop()
    rt.pre_sync_usn = _max_note_usn()


def _on_collection_will_temporarily_close(_col) -> None:
    """Fires when the collection is closed for a FULL sync (upload/download) —
    the signal for a whole-collection replacement that may rewind mods."""
    rt = _runtime
    if rt is not None:
        rt.full_sync_seen = True


def _on_sync_did_finish() -> None:
    rt = _runtime
    if rt is None or mw is None or mw.col is None:
        return
    rt.sync_active = False
    config = load_config()
    if config.auto_capture and config.heartbeat_scan_minutes > 0:
        rt.heartbeat.start(config.heartbeat_scan_minutes * 60_000)
    full_sync = rt.full_sync_seen
    rt.full_sync_seen = False
    pre_usn = rt.pre_sync_usn
    rt.pre_sync_usn = -1
    if not config.auto_capture:
        return
    # A full download can rewind mods below our marker (blinding the incremental
    # scan); heal with a full/indexed rescan. marker_regressed also covers the
    # case where collection_will_temporarily_close didn't fire.
    if full_sync or capture_notes.marker_regressed(mw.col, rt.conn):
        request_full_rescan()
        return
    # Normal merge: sync-changed notes carry a fresh server usn but may keep a
    # mod below our marker; drive exactly those through the usn-window recheck.
    changed = _notes_changed_since_usn(pre_usn)
    if changed:
        rt.pending.want_notes = True
        rt.pending.recheck_nids |= changed
    rt.pending.want_notetypes = True
    rt.pending.force_deletion_diff = True  # sync may delete notes at net-zero count
    # rows captured by this scan show "Sync" in the timeline, not "Auto"
    rt.pending.labels.append(consts.LABEL_SYNC)
    rt.debounce.start(_SYNC_SCAN_DELAY_MS)


def _max_note_usn() -> int:
    try:
        return int(mw.col.db.scalar("select coalesce(max(usn), 0) from notes"))
    except Exception:
        return -1


def _notes_changed_since_usn(pre_usn: int) -> frozenset[int]:
    if pre_usn < 0 or mw is None or mw.col is None:
        return frozenset()
    try:
        rows = mw.col.db.list("select id from notes where usn > ?", pre_usn)
    except Exception:
        return frozenset()
    return frozenset(int(nid) for nid in rows)


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
    if rt.sync_active:
        return  # collection may be mid close/reopen for sync; pending is kept
    if rt.baseline_running:
        return  # a full baseline is running; let it own capture
    if mutation_busy(rt):
        rt.rescan_requested = True
        return
    if rt.full_rescan_pending:
        # a heal queued behind a scan/baseline that has since ended runs first
        rt.full_rescan_pending = False
        done = rt.pending_rescan_done
        rt.pending_rescan_done = None
        request_full_rescan(done)
        return
    config = load_config()
    if not config.auto_capture:
        return
    work = rt.pending.consume()
    if not (work.want_notes or work.want_notetypes):
        return
    ctx = _build_context(rt, work, config)
    want_notetypes = work.want_notetypes
    db_path = profile_db_path(rt)
    if not acquire_mutation(rt, "scan"):
        rt.pending.merge_before(work)
        return
    rt.scan_running = True
    rt_token = rt

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
                )
            if prune.maintenance_due(own):
                prune.run_maintenance(
                    own,
                    BlobStore(blobs_root) if capture_media_files else None,
                    config.retention,
                )
            return note_report
        finally:
            own.close()

    def on_success(report) -> None:
        if _runtime is not rt_token:
            return
        rt_token.scan_running = False
        release_mutation(rt_token, "scan")
        rt_token.scan_failures = 0
        rt_token.session_touched.update(report.touched_nids)
        _drain_pending_scan()

    def on_failure(exc: BaseException) -> None:
        if _runtime is not rt_token:
            return
        rt_token.scan_running = False
        release_mutation(rt_token, "scan")
        rt_token.pending.merge_before(work)
        _note_scan_failure(exc, rt=rt_token)
        rt_token.debounce.start(_retry_delay(rt_token.scan_failures))

    QueryOp(parent=mw, op=op, success=on_success).failure(on_failure).run_in_background()


def _drain_pending_scan() -> None:
    """Start whatever was queued behind the scan that just finished — a pending
    full rescan wins over a plain re-scan request."""
    rt = _runtime
    if rt is None:
        return
    if rt.full_rescan_pending:
        rt.full_rescan_pending = False
        done = rt.pending_rescan_done
        rt.pending_rescan_done = None
        request_full_rescan(done)
        return
    if rt.rescan_requested:
        rt.rescan_requested = False
        rt.debounce.start(_RESCAN_DELAY_MS)


def _note_scan_failure(
    exc: BaseException, what: str = "scan", rt: Runtime | None = None
) -> None:
    """Log a background-scan failure and, after several in a row, warn once per
    session so a persistent fault (disk full, locked DB) isn't silent."""
    print(f"note_version_history: {what} failed: {exc!r}")
    target = rt if rt is not None else _runtime
    if target is None:
        return
    target.scan_failures += 1
    if target.scan_failures >= _MAX_SCAN_FAILURES and not target.scan_warning_shown:
        target.scan_warning_shown = True
        _show_warning(i18n.tr("scan_failed_repeatedly"))


def _retry_delay(failures: int) -> int:
    return retry_delay(failures)


def _build_context(rt: Runtime, work: PendingWork, config: AddonConfig) -> NoteScanContext:
    return NoteScanContext(
        origin=consts.ORIGIN_AUTO,
        op_label=_format_label(work.labels),
        saw_undo=work.saw_undo,
        session_touched_nids=frozenset(rt.session_touched),
        exclude_mids=frozenset(config.exclude_notetype_ids),
        before_states=dict(rt.before_cache),  # snapshot for the background scan
        force_deletion_diff=work.force_deletion_diff,
        recheck_nids=work.recheck_nids,
    )


def _format_label(labels: list[str]) -> str:
    unique = list(dict.fromkeys(label for label in labels if label))
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    if unique[-1].startswith("@"):
        # "@" sentinels are translated at display time by exact key — a
        # " (+N)" suffix would break the lookup and leak the raw sentinel
        return unique[-1]
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
