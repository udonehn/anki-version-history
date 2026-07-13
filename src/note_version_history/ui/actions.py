"""CollectionOp wrappers around restore.py.

Restores run in the background with the RESTORE_INITIATOR sentinel (the
capture hook recognizes and skips them) and write their own
``origin='restore'`` history row in the success callback (main thread, main
connection) — history stays append-only.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable

from aqt import mw
from aqt.operations import CollectionOp
from aqt.utils import showWarning, tooltip

from .. import capture_notes, capture_notetypes, consts, restore, scheduler
from ..i18n import tr
from ..records import NotetypeVersion, NoteVersion


def restore_note_version(
    parent,
    version: NoteVersion,
    only_fields: set[str] | None,
    on_done: Callable[[], None] | None = None,
) -> None:
    outcome: dict = {}

    def op(col):
        result = restore.apply_note_version(
            col, version, only_fields, tr("undo_restore_note")
        )
        outcome["result"] = result
        return result.changes

    def on_success(_changes) -> None:
        _record_restore_row(version.nid)
        result = outcome.get("result")
        if result is not None and result.skipped_fields:
            showWarning(
                tr("restore_skipped_fields", fields=", ".join(result.skipped_fields))
            )
        if only_fields is not None and result is not None:
            tooltip(tr("restore_fields_done", fields=", ".join(result.applied_fields)))
        else:
            tooltip(tr("restore_done"))
        if on_done is not None:
            on_done()

    CollectionOp(parent=parent, op=op).success(on_success).failure(
        _on_restore_failure
    ).run_in_background(initiator=consts.RESTORE_INITIATOR)


def restore_deleted_as_new(
    parent,
    version: NoteVersion,
    deck_id: int,
    on_done: Callable[[], None] | None = None,
) -> None:
    outcome: dict = {}

    def op(col):
        result = restore.restore_deleted_note_as_new(
            col, version, deck_id, tr("undo_restore_as_new")
        )
        outcome["new_nid"] = result.new_nid
        return result.changes

    def on_success(_changes) -> None:
        new_nid = outcome.get("new_nid")
        if new_nid:
            _record_restore_row(int(new_nid))
        tooltip(tr("restore_as_new_done"))
        if on_done is not None:
            on_done()

    CollectionOp(parent=parent, op=op).success(on_success).failure(
        _on_restore_failure
    ).run_in_background(initiator=consts.RESTORE_INITIATOR)


def restore_notetype_version(
    parent,
    version: NotetypeVersion,
    on_done: Callable[[], None] | None = None,
) -> None:
    outcome: dict = {}

    def op(col):
        result = restore.apply_notetype_version(
            col, version, tr("undo_restore_notetype")
        )
        outcome["result"] = result
        return result.changes

    def on_success(_changes) -> None:
        _record_notetype_restore_row(version.mid)
        result = outcome.get("result")
        if result is not None and (result.missing_in_current or result.missing_in_stored):
            showWarning(
                tr(
                    "ntd_mismatch_warning",
                    applied=", ".join(result.applied_templates) or "-",
                    missing_current=", ".join(result.missing_in_current) or "-",
                    missing_stored=", ".join(result.missing_in_stored) or "-",
                )
            )
        tooltip(tr("ntd_restore_done"))
        if on_done is not None:
            on_done()

    CollectionOp(parent=parent, op=op).success(on_success).failure(
        _on_restore_failure
    ).run_in_background(initiator=consts.RESTORE_INITIATOR)


def apply_notetype_version_into_clayout(clayout, version: NotetypeVersion) -> bool:
    """Load a version's templates+CSS into an OPEN CardLayout's in-memory
    model and refresh its editor/preview through the dialog's own plumbing.
    No DB write — the user confirms with the dialog's Save button (which the
    capture pipeline then records normally).

    Returns False if the CardLayout internals are not as expected; the caller
    falls back to asking the user to close the editor."""
    try:
        stored = json.loads(version.config_json)
    except json.JSONDecodeError:
        return False
    model = getattr(clayout, "model", None)
    if not isinstance(model, dict):
        return False

    stored_templates = {t.get("name", ""): t for t in stored.get("tmpls", [])}
    for template in model.get("tmpls", []):
        stored_template = stored_templates.get(template.get("name", ""))
        if stored_template is None:
            continue
        template["qfmt"] = stored_template.get("qfmt", template.get("qfmt", ""))
        template["afmt"] = stored_template.get("afmt", template.get("afmt", ""))
    model["css"] = stored.get("css", model.get("css", ""))

    # activate the dialog's unsaved-changes state so Save applies it
    tracker = getattr(clayout, "change_tracker", None)
    mark = getattr(tracker, "mark_basic", None)
    if callable(mark):
        mark()

    # redraw_everything reloads the edit areas from the model AND re-renders
    # the preview (verified in aqt 26.05 clayout.py); older fallbacks kept.
    redraw = getattr(clayout, "redraw_everything", None)
    if callable(redraw):
        redraw()
        return True
    refreshed = False
    fill = getattr(clayout, "fill_fields_from_template", None)
    if callable(fill):
        fill()
        refreshed = True
    render = getattr(clayout, "renderPreview", None)
    if callable(render):
        render()
        refreshed = True
    return refreshed


def snapshot_notetype(mid: int) -> bool:
    """Manual note type snapshot. Main thread, main connection."""
    rt = scheduler.runtime()
    if rt is None or mw is None or mw.col is None:
        tooltip(tr("no_profile_open"))
        return False
    # empty op_label → the timeline derives it from origin='manual'
    done = capture_notetypes.snapshot_notetype(
        mw.col, rt.conn, int(mid), op_label=""
    )
    if done:
        tooltip(tr("ntd_snapshot_done"))
    return done


def snapshot_notes(nids: Iterable[int]) -> int:
    """Manual snapshot from browser/editor. Main thread, main connection."""
    rt = scheduler.runtime()
    if rt is None or mw is None or mw.col is None:
        tooltip(tr("no_profile_open"))
        return 0
    count = capture_notes.snapshot_notes(
        mw.col, rt.conn, [int(n) for n in nids], op_label=""
    )
    tooltip(tr("snapshot_done", count=count))
    return count


def _record_restore_row(nid: int) -> None:
    # empty op_label → the timeline derives it from origin='restore'
    rt = scheduler.runtime()
    if rt is None or mw is None or mw.col is None:
        return
    capture_notes.snapshot_notes(
        mw.col, rt.conn, [nid], origin=consts.ORIGIN_RESTORE, op_label=""
    )


def _record_notetype_restore_row(mid: int) -> None:
    rt = scheduler.runtime()
    if rt is None or mw is None or mw.col is None:
        return
    capture_notetypes.snapshot_notetype(
        mw.col, rt.conn, int(mid), origin=consts.ORIGIN_RESTORE, op_label=""
    )


def _on_restore_failure(exc: BaseException) -> None:
    if isinstance(exc, restore.GuidMismatchError):
        showWarning(tr("restore_guid_mismatch"))
    else:
        showWarning(tr("restore_failed", error=str(exc)))
