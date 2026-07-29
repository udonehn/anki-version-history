"""On-demand full baseline (notes + note types, then media).

Triggered from the Tools menu, plus a one-time profile-open recommendation
(maybe_profile_open_prompt) while the notes baseline is neither done nor
declined — the capture model itself stays lazy, nothing runs unaccepted.
Informed-consent prompt with exact numbers, background run with progress,
and resume support after interruption.
"""

from __future__ import annotations

from aqt import mw
from aqt.operations import QueryOp
from aqt.utils import askUser, showWarning, tooltip

from .. import baseline, consts, db, scheduler
from ..i18n import tr


def maybe_show(*, first_run: bool = False) -> None:
    """Consent prompt for the full notes baseline. The Tools-menu path
    (default) always asks; ``first_run=True`` is the one-time profile-open
    offer — it bails unless the baseline is still offerable, and a decline
    is recorded so it never auto-asks again (the Tools menu stays open)."""
    rt = scheduler.runtime()
    if rt is None or mw is None or mw.col is None:
        return
    if first_run and (
        rt.baseline_running or not baseline.should_offer_first_run(rt.conn)
    ):
        return
    rt_token = rt
    resuming = int(baseline.get_state(rt.conn).get("notes_cursor") or 0) > 0

    def show_prompt(numbers: dict) -> None:
        current = scheduler.runtime()
        if current is not rt_token:
            return  # profile closed/switched while estimating
        if first_run and (
            current.baseline_running
            or not baseline.should_offer_first_run(current.conn)
        ):
            return  # e.g. a menu-triggered run started while estimating
        if resuming:
            key = "baseline_resume_prompt"
        elif first_run:
            key = "baseline_first_run_intro"
        else:
            key = "baseline_intro"
        text = tr(
            key,
            addon_name=tr("menu_root"),
            notes=numbers["note_count"],
            notetypes=numbers["notetype_count"],
            mb=numbers["field_bytes"] / 1_000_000,
        )
        accepted = askUser(text, title=tr("baseline_intro_title"))
        current = scheduler.runtime()  # the profile can close during the modal
        if current is not rt_token:
            return
        if not accepted:
            if first_run:
                baseline.skip_notes_baseline(current.conn)
            tooltip(tr("baseline_postponed"))
            return
        if baseline.get_state(current.conn)["notes"] == baseline.STATE_SKIPPED:
            # re-opt-in from the Tools menu after a declined first-run offer —
            # back to pending so an interruption resumes on the next open
            baseline.update_state(current.conn, notes=baseline.STATE_PENDING)
        start()

    # the estimate sums every note's field bytes — off the main thread
    QueryOp(parent=mw, op=lambda col: baseline.estimate(col), success=show_prompt).run_in_background()


def start() -> None:
    rt = scheduler.runtime()
    if rt is None or rt.baseline_running or not scheduler.acquire_mutation(rt, "baseline"):
        return
    rt.baseline_running = True
    rt_token = rt
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
        if current is not rt_token:
            return
        rt_token.baseline_running = False
        scheduler.release_mutation(rt_token, "baseline")
        tooltip(tr("baseline_done", count=captured))
        # drain any edits queued while the baseline ran
        scheduler.request_scan(notes=True, notetypes=True)
        maybe_media_step()

    def on_failure(exc: BaseException) -> None:
        current = scheduler.runtime()
        if current is not rt_token:
            return
        rt_token.baseline_running = False
        scheduler.release_mutation(rt_token, "baseline")
        showWarning(tr("baseline_failed", error=str(exc)))

    QueryOp(parent=mw, op=op, success=on_success).failure(on_failure).with_progress(
        tr("baseline_progress")
    ).run_in_background()


# --- profile-open offer ---


def maybe_profile_open_prompt() -> None:
    """Single profile-open prompt slot — at most one consent dialog per open.
    The one-time first-run notes offer wins; otherwise the media resume step
    (which only auto-prompts once notes are done, so the two never stack)."""
    rt = scheduler.runtime()
    if rt is None or mw is None or mw.col is None or rt.baseline_running:
        return
    if baseline.should_offer_first_run(rt.conn):
        maybe_show(first_run=True)
    else:
        maybe_media_step()


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
    resuming = bool(str(baseline.get_state(rt.conn).get("media_cursor") or ""))
    rt_token = rt

    def show_prompt(numbers: dict) -> None:
        current = scheduler.runtime()
        if current is not rt_token:  # profile closed/switched while estimating
            return
        prompt_key = (
            "media_baseline_resume_prompt" if resuming else "media_baseline_prompt"
        )
        text = tr(
            prompt_key,
            count=numbers["file_count"],
            mb=numbers["total_bytes"] / 1_000_000,
        )
        accepted = askUser(text, title=tr("baseline_intro_title"))
        current = scheduler.runtime()
        if current is not rt_token:
            return
        if not accepted:
            baseline.skip_media_baseline(current.conn)
            tooltip(tr("media_baseline_skipped"))
            return
        if state == baseline.STATE_SKIPPED:
            # user re-opted in from the Tools menu
            baseline.update_state(current.conn, media=baseline.STATE_PENDING)
        start_media()

    # the estimate stats every media file — off the main thread
    QueryOp(
        parent=mw, op=lambda col: baseline.estimate_media(col), success=show_prompt
    ).run_in_background()


def start_media() -> None:
    rt = scheduler.runtime()
    if rt is None or rt.baseline_running or not scheduler.acquire_mutation(
        rt, "media_baseline"
    ):
        return
    rt.baseline_running = True
    rt_token = rt
    db_path = scheduler.profile_db_path(rt)
    blobs_root = scheduler.blob_store(rt)

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
        if current is not rt_token:
            return
        rt_token.baseline_running = False
        scheduler.release_mutation(rt_token, "media_baseline")
        tooltip(tr("media_baseline_done", count=captured))
        # drain note/notetype edits queued while the media baseline ran (the
        # notes-baseline success path does the same)
        scheduler.request_scan(notes=True, notetypes=True)

    def on_failure(exc: BaseException) -> None:
        current = scheduler.runtime()
        if current is not rt_token:
            return
        rt_token.baseline_running = False
        scheduler.release_mutation(rt_token, "media_baseline")
        showWarning(tr("baseline_failed", error=str(exc)))

    QueryOp(parent=mw, op=op, success=on_success).failure(on_failure).with_progress(
        tr("media_baseline_progress")
    ).run_in_background()
