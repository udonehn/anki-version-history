"""Entry points: Tools submenu, browser context menu, editor button."""

from __future__ import annotations

import weakref

from aqt import gui_hooks, mw
from aqt.qt import QAction, QKeySequence, QMenu, QPushButton, qconnect
from aqt.utils import showInfo, tooltip

from .. import consts, scheduler
from ..i18n import tr
from . import actions


def setup() -> None:
    if mw is None:
        return
    _setup_tools_menu()
    gui_hooks.browser_will_show_context_menu.append(_on_browser_context_menu)
    gui_hooks.browser_menus_did_init.append(_on_browser_menus_did_init)
    gui_hooks.editor_did_init_buttons.append(_on_editor_buttons)
    gui_hooks.card_layout_will_show.append(_on_card_layout_will_show)


def _setup_tools_menu() -> None:
    root = QMenu(tr("menu_root"), mw)
    mw.form.menuTools.addMenu(root)

    # View history (most frequent)
    notetype_action = QAction(tr("menu_notetype_history"), mw)
    qconnect(notetype_action.triggered, lambda _checked=False: _open_notetype_history(mw))
    root.addAction(notetype_action)

    if consts.MEDIA_ENABLED:
        media_action = QAction(tr("menu_media_history"), mw)
        qconnect(media_action.triggered, lambda _checked=False: _open_media_history())
        root.addAction(media_action)

    # Backup (full coverage)
    root.addSeparator()
    baseline_now_action = QAction(tr("menu_baseline_now"), mw)
    qconnect(baseline_now_action.triggered, lambda _checked=False: _baseline_now())
    root.addAction(baseline_now_action)

    if consts.MEDIA_ENABLED:
        resume_media_action = QAction(tr("menu_resume_media_baseline"), mw)
        qconnect(
            resume_media_action.triggered, lambda _checked=False: _resume_media_baseline()
        )
        root.addAction(resume_media_action)

    # Maintenance
    root.addSeparator()
    rescan_action = QAction(tr("menu_full_rescan"), mw)
    qconnect(rescan_action.triggered, lambda _checked=False: _full_rescan())
    root.addAction(rescan_action)

    compact_action = QAction(tr("menu_compact"), mw)
    qconnect(compact_action.triggered, lambda _checked=False: _compact())
    root.addAction(compact_action)

    root.addSeparator()
    about_action = QAction(tr("menu_about"), mw)
    qconnect(about_action.triggered, show_about)
    root.addAction(about_action)


def _full_rescan() -> None:
    from .. import baseline

    rt = scheduler.runtime()
    if rt is None:
        tooltip(tr("no_profile_open"))
        return
    if not baseline.notes_baseline_done(rt.conn):
        # Full rescan heals a baselined collection; running it lazily would
        # dump every note as 'auto'. Point the user at the baseline instead.
        tooltip(tr("rescan_needs_baseline"))
        return
    scheduler.request_full_rescan(
        lambda report: tooltip(tr("rescan_done", captured=report.captured))
    )


def _compact() -> None:
    from aqt.operations import QueryOp

    from .. import db, profiles, prune
    from ..blobstore import BlobStore

    rt = scheduler.runtime()
    if rt is None:
        tooltip(tr("no_profile_open"))
        return
    db_path = scheduler.profile_db_path(rt)
    blobs_root = profiles.blobs_dir(rt.data_dir)

    def op(_col):
        own = db.open_history_db(db_path)
        try:
            removed = prune.gc_blobs(own, BlobStore(blobs_root))
            prune.full_vacuum(own)
            return removed
        finally:
            own.close()

    QueryOp(
        parent=mw,
        op=op,
        success=lambda removed: tooltip(tr("compact_done", blobs=removed)),
    ).with_progress(tr("compact_progress")).run_in_background()


def _open_media_history() -> None:
    from . import media_dialog

    media_dialog.open_dialog(mw)


def _resume_media_baseline() -> None:
    from . import baseline_wizard

    baseline_wizard.maybe_media_step(force_prompt=True)


def _baseline_now() -> None:
    from . import baseline_wizard

    baseline_wizard.maybe_show()  # consent dialog, then full notes+media baseline


def _open_notetype_history(parent, preselect_mid: int | None = None) -> None:
    from . import notetype_dialog

    notetype_dialog.open_dialog(parent, preselect_mid)


# Open CardLayout dialogs hold their own IN-MEMORY copy of the notetype;
# restores must go through that copy (not the DB) while one is open, or the
# editor's preview/save state desyncs. Track them weakly.
_open_clayouts: "weakref.WeakSet" = weakref.WeakSet()


def open_clayout_for(mid: int):
    """The visible CardLayout editing this notetype, if any."""
    for clayout in list(_open_clayouts):
        model = getattr(clayout, "model", None)
        try:
            if model is not None and int(model["id"]) == int(mid) and clayout.isVisible():
                return clayout
        except (KeyError, TypeError, ValueError, RuntimeError):
            continue  # deleted Qt object or malformed model
    return None


def _on_card_layout_will_show(clayout) -> None:
    """Inject a history button into the template editor, preselected to the
    note type being edited. All attribute access is defensive — if the
    CardLayout internals change, the Tools menu still covers this."""
    _open_clayouts.add(clayout)
    buttons = getattr(clayout, "buttons", None)
    model = getattr(clayout, "model", None)
    if buttons is None or model is None or not hasattr(buttons, "insertWidget"):
        return
    try:
        mid = int(model["id"])
    except (KeyError, TypeError, ValueError):
        return
    button = QPushButton(tr("clayout_history_button"))
    qconnect(button.clicked, lambda _checked=False: _open_notetype_history(clayout, mid))
    buttons.insertWidget(0, button)


# --- browser ---


def _on_browser_context_menu(browser, menu) -> None:
    nids = _selected_notes(browser)
    if not nids:
        return
    menu.addSeparator()
    if len(nids) == 1:
        history_action = menu.addAction(tr("menu_note_history"))
        qconnect(
            history_action.triggered,
            lambda _checked=False, nid=int(nids[0]): _open_history(browser, nid),
        )
    snapshot_action = menu.addAction(tr("menu_snapshot_selected", count=len(nids)))
    qconnect(
        snapshot_action.triggered,
        lambda _checked=False, ids=tuple(int(n) for n in nids): actions.snapshot_notes(ids),
    )


def _on_browser_menus_did_init(browser) -> None:
    """Add a Notes-menu entry + shortcut so history opens from a selected card
    without needing the editor to be focused (the 🕘 editor button only works
    while a field is active). Falls back silently if the menu isn't present."""
    menu = getattr(getattr(browser, "form", None), "menu_Notes", None)
    if menu is None or not hasattr(menu, "addAction"):
        return
    action = QAction(tr("menu_note_history"), browser)
    action.setShortcut(QKeySequence("Ctrl+Alt+H"))
    qconnect(
        action.triggered,
        lambda _checked=False, b=browser: _open_history_for_selection(b),
    )
    menu.addAction(action)


def _open_history_for_selection(browser) -> None:
    nids = _selected_notes(browser)
    if not nids:
        tooltip(tr("no_note_selected"))
        return
    _open_history(browser, int(nids[0]))


def _selected_notes(browser) -> list[int]:
    for attr in ("selected_notes", "selectedNotes"):
        method = getattr(browser, attr, None)
        if callable(method):
            try:
                return list(method())
            except Exception:
                return []
    return []


def _open_history(parent, nid: int) -> None:
    from . import history_dialog

    history_dialog.open_for_note(parent, nid)


# --- editor ---


def _on_editor_buttons(buttons, editor) -> None:
    add_button = getattr(editor, "addButton", None)
    if not callable(add_button):
        # NewEditor without the classic button API — the browser context menu
        # still covers the full functionality.
        return
    button = add_button(
        icon=None,
        cmd="nvh_history",
        func=_open_from_editor,
        tip=tr("editor_history_tip"),
        label="🕘",
    )
    buttons.append(button)


def _open_from_editor(editor) -> None:
    note = getattr(editor, "note", None)
    if note is None or not note.id:
        tooltip(tr("editor_unsaved_note"))
        return
    nid = int(note.id)
    parent = getattr(editor, "parentWindow", None) or mw
    save_then = getattr(editor, "call_after_note_saved", None)
    if callable(save_then):
        save_then(lambda: _open_history(parent, nid))
    else:
        _open_history(parent, nid)


# --- about ---


def show_about() -> None:
    rt = scheduler.runtime()
    if rt is None:
        showInfo(tr("about_no_profile"), title=tr("about_title"))
        return
    counts = {
        "notes": _count(rt, "note_versions"),
        "notetypes": _count(rt, "notetype_versions"),
        "media": _count(rt, "media_events"),
    }
    blob_stats = rt.blobs.stats()
    showInfo(
        tr(
            "about_body",
            notes=counts["notes"],
            notetypes=counts["notetypes"],
            media=counts["media"],
            blob_count=blob_stats.count,
            blob_mb=blob_stats.total_bytes / 1_000_000,
            db_path=str(rt.data_dir),
        ),
        title=tr("about_title"),
    )


def _count(rt: scheduler.Runtime, table: str) -> int:
    return rt.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
