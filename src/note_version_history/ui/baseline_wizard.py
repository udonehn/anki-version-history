"""On-demand full baseline (notes + note types, then media).

Triggered from the Tools menu (the default capture model is lazy, so there is
no forced first-run baseline). Informed-consent prompt with exact numbers,
background run with progress, and resume support after interruption.
"""

from __future__ import annotations

from aqt import mw
from aqt.operations import QueryOp
from aqt.utils import askUser, showWarning, tooltip

from .. import baseline, consts, db, scheduler
from ..i18n import tr


def maybe_show() -> None:
    rt = scheduler.runtime()
    if rt is None or mw is None or mw.col is None:
        return
    numbers = baseline.estimate(mw.col)
    resuming = int(baseline.get_state(rt.conn).get("notes_cursor") or 0) > 0
    text = tr(
        "baseline_resume_prompt" if resuming else "baseline_intro",
        notes=numbers["note_count"],
        notetypes=numbers["notetype_count"],
        mb=numbers["field_bytes"] / 1_000_000,
    )
    if not askUser(text, title=tr("baseline_intro_title")):
        tooltip(tr("baseline_postponed"))
        return
    start()


def start() -> None:
    rt = scheduler.runtime()
    if rt is None or rt.baseline_running:
        return
    rt.baseline_running = True
    db_path = scheduler.profile_db_path(rt)

    def report_progress(done: int, total: int) -> None:
        mw.taskman.run_on_main(
            lambda: mw.progress.update(
                label=tr("baseline_progress_label", done=done, total=total),
                value=done,
                max=total,
            )
        )

    def op(col):
        own = db.open_history_db(db_path)
        try:
            return baseline.run_notes_baseline(col, own, progress=report_progress)
        finally:
            own.close()

    def on_success(captured: int) -> None:
        current = scheduler.runtime()
        if current is not None:
            current.baseline_running = False
        tooltip(tr("baseline_done", count=captured))
        # drain any edits queued while the baseline ran
        scheduler.request_scan(notes=True, notetypes=True)
        maybe_media_step()

    def on_failure(exc: BaseException) -> None:
        current = scheduler.runtime()
        if current is not None:
            current.baseline_running = False
        showWarning(tr("baseline_failed", error=str(exc)))

    QueryOp(parent=mw, op=op, success=on_success).failure(on_failure).with_progress(
        tr("baseline_progress")
    ).run_in_background()


# --- media baseline step (after notes are done) ---


def maybe_media_step(*, force_prompt: bool = False) -> None:
    """Offer/resume the media baseline with an informed-consent size estimate.
    Called after the notes baseline completes, on profile open when a media
    baseline is still pending, and from the Tools menu (force_prompt)."""
    if not consts.MEDIA_ENABLED:
        return
    rt = scheduler.runtime()
    if rt is None or mw is None or mw.col is None or rt.baseline_running:
        return
    # Media backup is independent of the notes baseline. The auto path (profile
    # open) still waits for a notes baseline, but the Tools menu (force_prompt)
    # can run it standalone.
    if not force_prompt and not baseline.notes_baseline_done(rt.conn):
        return
    state = baseline.media_baseline_state(rt.conn)
    if state in (baseline.STATE_DONE,):
        return
    if state == baseline.STATE_SKIPPED and not force_prompt:
        return
    if not scheduler.load_config().capture_media and not force_prompt:
        return

    numbers = baseline.estimate_media(mw.col)
    resuming = bool(str(baseline.get_state(rt.conn).get("media_cursor") or ""))
    prompt_key = "media_baseline_resume_prompt" if resuming else "media_baseline_prompt"
    text = tr(
        prompt_key,
        count=numbers["file_count"],
        mb=numbers["total_bytes"] / 1_000_000,
    )
    if not askUser(text, title=tr("baseline_intro_title")):
        baseline.skip_media_baseline(rt.conn)
        tooltip(tr("media_baseline_skipped"))
        return
    if state == baseline.STATE_SKIPPED:
        # user re-opted in from the Tools menu
        baseline.update_state(rt.conn, media=baseline.STATE_PENDING)
    start_media()


def start_media() -> None:
    rt = scheduler.runtime()
    if rt is None or rt.baseline_running:
        return
    rt.baseline_running = True
    db_path = scheduler.profile_db_path(rt)
    blobs_root = rt.blobs

    def report_progress(done: int, total: int) -> None:
        mw.taskman.run_on_main(
            lambda: mw.progress.update(
                label=tr("media_baseline_progress_label", done=done, total=total),
                value=done,
                max=total,
            )
        )

    def op(col):
        own = db.open_history_db(db_path)
        try:
            return baseline.run_media_baseline(
                col, own, blobs_root, progress=report_progress
            )
        finally:
            own.close()

    def on_success(captured: int) -> None:
        current = scheduler.runtime()
        if current is not None:
            current.baseline_running = False
        tooltip(tr("media_baseline_done", count=captured))

    def on_failure(exc: BaseException) -> None:
        current = scheduler.runtime()
        if current is not None:
            current.baseline_running = False
        showWarning(tr("baseline_failed", error=str(exc)))

    QueryOp(parent=mw, op=op, success=on_success).failure(on_failure).with_progress(
        tr("media_baseline_progress")
    ).run_in_background()
