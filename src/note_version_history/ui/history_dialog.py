"""Paged note timeline, arbitrary A→B comparison, annotation and restore."""

from __future__ import annotations

from aqt import mw
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTimer,
    Qt,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import askUser, tooltip

from .. import (
    capture_notes,
    comparison,
    consts,
    db,
    diffing,
    restore,
    scheduler,
    timeline,
)
from ..i18n import tr
from ..records import NoteVersion
from . import actions, dialogs, widgets

_open_dialogs: set["HistoryDialog"] = set()
_MODE_KEYS = ("view_only", "vs_current", "vs_previous", "specified_versions")
_DEFAULT_MODE_KEY = "vs_previous"
_FILTER_KEYS = (
    "all",
    "automatic",
    "sync",
    "snapshot",
    "restore",
    "baseline",
    "deleted",
    "pinned",
)


def open_for_note(parent, nid: int) -> None:
    if scheduler.runtime() is None:
        tooltip(tr("no_profile_open"))
        return
    dialog = HistoryDialog(parent or mw, nid)
    _open_dialogs.add(dialog)
    dialog.show()


class HistoryDialog(QDialog):
    def __init__(self, parent, nid: int) -> None:
        super().__init__(parent)
        self._nid = int(nid)
        self._versions: list[NoteVersion] = []
        self._offset = 0
        self._total = 0
        self._a_id: int | None = None
        self._b_id: int | None = None
        self._field_checks: dict[str, QCheckBox] = {}
        self.setWindowTitle(tr("hd_title", nid=self._nid))
        self.resize(1080, 720)
        if type(parent).__name__ == "Browser":
            self.setWindowModality(Qt.WindowModality.WindowModal)
        self._build_ui()
        self._reload(reset_offset=True)
        qconnect(self.finished, lambda _result: _open_dialogs.discard(self))

    def closeEvent(self, event) -> None:  # noqa: N802
        _open_dialogs.discard(self)
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        filters = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("timeline_search"))
        filters.addWidget(self._search, 1)
        self._filter = QComboBox()
        for key in _FILTER_KEYS:
            self._filter.addItem(tr(f"filter_{key}"), key)
        filters.addWidget(self._filter)
        self._content_search = QCheckBox(tr("search_content"))
        filters.addWidget(self._content_search)
        self._pinned_only = QCheckBox(tr("pinned_only"))
        filters.addWidget(self._pinned_only)
        root.addLayout(filters)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        qconnect(self._search_timer.timeout, lambda: self._reload(reset_offset=True))
        qconnect(self._search.textChanged, lambda _text: self._search_timer.start())
        qconnect(
            self._filter.currentIndexChanged,
            lambda _idx: self._reload(reset_offset=True),
        )
        qconnect(
            self._content_search.toggled,
            lambda _checked: self._reload(reset_offset=True),
        )
        qconnect(
            self._pinned_only.toggled,
            lambda _checked: self._reload(reset_offset=True),
        )

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        self._list.setMinimumWidth(230)
        qconnect(self._list.currentRowChanged, lambda _row: self._render())
        left_layout.addWidget(self._list, 1)
        pager = QHBoxLayout()
        self._previous_page = QPushButton(tr("page_previous"))
        self._next_page = QPushButton(tr("page_next"))
        self._range = QLabel()
        qconnect(self._previous_page.clicked, self._page_back)
        qconnect(self._next_page.clicked, self._page_forward)
        pager.addWidget(self._previous_page)
        pager.addWidget(self._range, 1)
        pager.addWidget(self._next_page)
        left_layout.addLayout(pager)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        compare = QHBoxLayout()
        self._mode = QComboBox()
        self._mode.addItems(
            [
                tr("hd_view_only"),
                tr("hd_diff_vs_current"),
                tr("hd_diff_vs_previous"),
                tr("compare_selected_versions"),
            ]
        )
        self._mode.setCurrentIndex(_saved_mode_index())
        qconnect(self._mode.currentIndexChanged, self._on_mode_changed)
        compare.addWidget(self._mode)
        self._set_a = QPushButton(tr("compare_set_a"))
        self._set_b = QPushButton(tr("compare_set_b"))
        self._swap = QPushButton(tr("compare_swap"))
        self._clear_ab = QPushButton(tr("compare_clear"))
        qconnect(self._set_a.clicked, lambda: self._set_endpoint("a"))
        qconnect(self._set_b.clicked, lambda: self._set_endpoint("b"))
        qconnect(self._swap.clicked, self._swap_endpoints)
        qconnect(self._clear_ab.clicked, self._clear_endpoints)
        for button in (self._set_a, self._set_b, self._swap, self._clear_ab):
            compare.addWidget(button)
        right_layout.addLayout(compare)
        self._ab_status = QLabel()
        self._ab_status.setTextFormat(Qt.TextFormat.PlainText)
        right_layout.addWidget(self._ab_status)
        self._update_compare_mode_ui()
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        right_layout.addWidget(self._scroll, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([330, 750])
        root.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self._snapshot_button = QPushButton(tr("hd_snapshot_now"))
        self._annotate_button = QPushButton(tr("edit_version_metadata"))
        qconnect(self._snapshot_button.clicked, self._snapshot_now)
        qconnect(self._annotate_button.clicked, self._edit_annotation)
        buttons.addWidget(self._snapshot_button)
        buttons.addWidget(self._annotate_button)
        buttons.addStretch(1)
        self._restore_fields_button = QPushButton(tr("hd_restore_fields"))
        self._restore_button = QPushButton(tr("hd_restore_version"))
        qconnect(self._restore_fields_button.clicked, self._restore_fields)
        qconnect(self._restore_button.clicked, self._restore_version)
        buttons.addWidget(self._restore_fields_button)
        buttons.addWidget(self._restore_button)
        close_button = QPushButton(tr("hd_close"))
        qconnect(close_button.clicked, self.close)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

    def _filter_value(self) -> timeline.TimelineFilter:
        return timeline.TimelineFilter(
            search=self._search.text(),
            category=str(self._filter.currentData() or "all"),
            pinned_only=self._pinned_only.isChecked(),
            include_content=self._content_search.isChecked(),
        )

    def _reload(
        self, *, reset_offset: bool = False, select_id: int | None = None
    ) -> None:
        rt = scheduler.runtime()
        if rt is None:
            return
        if reset_offset:
            self._offset = 0
        page = capture_notes.page_note_versions(
            rt.conn, self._nid, self._filter_value(), offset=self._offset
        )
        self._versions = list(page.items)
        self._total = page.total
        self._list.clear()
        selected_row = 0
        for index, version in enumerate(self._versions):
            line1, line2 = widgets.timeline_lines(version)
            widgets.add_two_line_item(
                self._list,
                line1,
                line2,
                highlight_red=version.deleted,
                highlight_pinned=version.pinned,
            )
            if version.id == select_id:
                selected_row = index
        self._range.setText(
            tr("page_range", start=page.start, end=page.end, total=page.total)
        )
        self._previous_page.setEnabled(page.has_previous)
        self._next_page.setEnabled(page.has_next)
        if self._versions:
            self._list.setCurrentRow(selected_row)
        else:
            self._render()
        self._update_ab_status()

    def _page_back(self) -> None:
        self._offset = max(0, self._offset - timeline.PAGE_SIZE)
        self._reload()

    def _page_forward(self) -> None:
        if self._offset + timeline.PAGE_SIZE < self._total:
            self._offset += timeline.PAGE_SIZE
            self._reload()

    def _current_version(self) -> NoteVersion | None:
        row = self._list.currentRow()
        return self._versions[row] if 0 <= row < len(self._versions) else None

    def _on_mode_changed(self, index: int) -> None:
        rt = scheduler.runtime()
        if rt is not None and 0 <= index < len(_MODE_KEYS):
            db.meta_set(
                rt.conn, consts.META_UI_HISTORY_COMPARE_MODE, _MODE_KEYS[index]
            )
        self._update_compare_mode_ui()
        self._render()

    def _update_compare_mode_ui(self) -> None:
        visible = self._mode.currentIndex() == 3
        for button in (self._set_a, self._set_b, self._swap, self._clear_ab):
            button.setVisible(visible)
        self._ab_status.setVisible(visible)

    def _set_endpoint(self, endpoint: str) -> None:
        version = self._current_version()
        if version is None or version.id is None:
            return
        if endpoint == "a":
            self._a_id = version.id
        else:
            self._b_id = version.id
        self._update_ab_status()
        self._render()

    def _swap_endpoints(self) -> None:
        self._a_id, self._b_id = self._b_id, self._a_id
        self._update_ab_status()
        self._render()

    def _clear_endpoints(self) -> None:
        self._a_id = self._b_id = None
        self._update_ab_status()
        self._render()

    def _endpoint(self, version_id: int | None) -> NoteVersion | None:
        rt = scheduler.runtime()
        return (
            capture_notes.get_note_version(rt.conn, version_id)
            if rt is not None and version_id is not None
            else None
        )

    def _update_ab_status(self) -> None:
        a = self._endpoint(self._a_id)
        b = self._endpoint(self._b_id)
        self._ab_status.setText(
            tr(
                "compare_status",
                a=_version_short(a),
                b=_version_short(b),
            )
        )
        self._swap.setEnabled(a is not None or b is not None)
        self._clear_ab.setEnabled(a is not None or b is not None)

    def _live_note(self, version: NoteVersion):
        if mw is None or mw.col is None:
            return None
        nid = restore.find_live_note_nid(mw.col, version)
        return mw.col.get_note(nid) if nid is not None else None

    def _comparison(self) -> tuple[NoteVersion | None, NoteVersion | None, bool]:
        if self._mode.currentIndex() == 3:
            a = self._endpoint(self._a_id)
            b = self._endpoint(self._b_id)
            return a, b, True
        target = self._current_version()
        if target is None or self._mode.currentIndex() == 0:
            return None, target, False
        if self._mode.currentIndex() == 2 and target.id is not None:
            rt = scheduler.runtime()
            previous = (
                capture_notes.get_previous_note_version(rt.conn, target.id)
                if rt is not None
                else None
            )
            return previous, target, False
        return None, target, False

    def _render(self) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)
        self._field_checks = {}
        base_version, target, explicit_ab = self._comparison()
        if target is None or (explicit_ab and base_version is None):
            layout.addWidget(
                QLabel(
                    tr("compare_select_both")
                    if explicit_ab
                    else tr("hd_no_versions")
                )
            )
            self._annotate_button.setEnabled(False)
            self._restore_button.setEnabled(False)
            self._restore_fields_button.setEnabled(False)
        else:
            self._annotate_button.setEnabled(True)
            self._render_version(layout, target, base_version, explicit_ab)
        layout.addStretch(1)
        self._scroll.setWidget(container)

    def _render_version(
        self,
        layout: QVBoxLayout,
        target: NoteVersion,
        base_version: NoteVersion | None,
        explicit_ab: bool,
    ) -> None:
        live_note = self._live_note(target)
        live_exists = live_note is not None
        deleted_flow = target.deleted or not live_exists
        if target.deleted:
            layout.addWidget(_banner(tr("hd_deleted_banner")))
        elif not live_exists:
            layout.addWidget(_banner(tr("hd_note_missing_banner")))
        self._restore_button.setText(
            tr("hd_restore_as_new") if deleted_flow else tr("hd_restore_version")
        )
        self._restore_button.setEnabled(True)
        self._restore_fields_button.setEnabled(not deleted_flow)

        surface = comparison.compare_notes(base_version, target)
        target_fields = surface.b_fields
        if explicit_ab:
            base_fields = surface.a_fields
            view_only = False
        elif self._mode.currentIndex() == 1:
            base_fields = (
                {name: live_note[name] for name in live_note.keys()}
                if live_note is not None
                else {}
            )
            view_only = False
        elif self._mode.currentIndex() == 2:
            base_fields = surface.a_fields
            view_only = False
        else:
            base_fields = {}
            view_only = True
        names = list(
            surface.field_names
            if explicit_ab or self._mode.currentIndex() == 2
            else dict.fromkeys((*base_fields.keys(), *target_fields.keys()))
        )
        insert_style, delete_style = widgets.diff_styles()
        for name in names:
            value = target_fields.get(name, "")
            check = QCheckBox(name)
            check.setChecked(True if view_only else base_fields.get(name, "") != value)
            check.setEnabled(name in target_fields and not deleted_flow)
            self._field_checks[name] = check
            layout.addWidget(check)
            view = widgets.NoLoadTextBrowser()
            if view_only:
                view.setHtml(diffing.plain_to_html(value))
            else:
                view.setHtml(
                    diffing.spans_to_html(
                        diffing.word_diff(base_fields.get(name, ""), value),
                        insert_style=insert_style,
                        delete_style=delete_style,
                    )
                )
            view.setMaximumHeight(170)
            layout.addWidget(view)
        if self._mode.currentIndex() == 1 and live_note is not None and not explicit_ab:
            base_tags = " ".join(live_note.tags)
        else:
            base_tags = " ".join(surface.a_tags)
        target_tags = " ".join(surface.b_tags)
        tags = widgets.NoLoadTextBrowser()
        tags.setMaximumHeight(70)
        tags.setHtml(
            diffing.plain_to_html(target_tags)
            if view_only
            else diffing.spans_to_html(
                diffing.word_diff(base_tags, target_tags),
                insert_style=insert_style,
                delete_style=delete_style,
            )
        )
        layout.addWidget(QLabel(tr("hd_tags")))
        layout.addWidget(tags)

    def _restore_version(self) -> None:
        version = self._current_version()
        if version is None:
            return
        if version.deleted or self._live_note(version) is None:
            self._restore_as_new(version)
            return
        if askUser(
            tr("confirm_restore", when=widgets.format_timestamp(version.ts)),
            parent=self,
        ):
            actions.restore_note_version(self, version, None, self._reload)

    def _restore_fields(self) -> None:
        version = self._current_version()
        if version is None or version.deleted:
            return
        names = {
            name for name, check in self._field_checks.items() if check.isChecked()
        }
        if not names:
            tooltip(tr("no_fields_selected"))
            return
        actions.restore_note_version(self, version, names, self._reload)

    def _restore_as_new(self, version: NoteVersion) -> None:
        source = version
        rt = scheduler.runtime()
        if version.deleted and rt is not None and version.id is not None:
            source = capture_notes.get_deleted_restore_source(rt.conn, version.id)
            if source is None:
                return
        if not askUser(tr("restore_as_new_prompt"), parent=self):
            return
        deck_id = dialogs.choose_deck(self, list(mw.col.decks.all_names_and_ids()))
        if deck_id is None:
            return
        actions.restore_deleted_as_new(self, source, deck_id, self._reload)

    def _snapshot_now(self) -> None:
        version = self._current_version()
        live_nid = (
            restore.find_live_note_nid(mw.col, version)
            if version is not None and mw.col is not None
            else self._nid
        )
        if live_nid is None:
            return
        options = dialogs.edit_annotation(
            self, title=tr("snapshot_options"), pinned=True
        )
        if options is None:
            return
        label, pinned = options
        actions.snapshot_notes(
            [live_nid],
            user_label=label,
            pinned=pinned,
            on_done=lambda: self._reload(reset_offset=True),
        )

    def _edit_annotation(self) -> None:
        version = self._current_version()
        rt = scheduler.runtime()
        if version is None or version.id is None or rt is None:
            return
        options = dialogs.edit_annotation(
            self,
            title=tr("edit_version_metadata"),
            user_label=version.user_label,
            pinned=version.pinned,
        )
        if options is None:
            return
        label, pinned = options
        capture_notes.update_note_annotation(
            rt.conn, version.id, user_label=label, pinned=pinned
        )
        self._reload(select_id=version.id)


def _version_short(version: NoteVersion | None) -> str:
    if version is None:
        return "—"
    return version.user_label or widgets.format_timestamp(version.ts)


def _saved_mode_index() -> int:
    key = _DEFAULT_MODE_KEY
    rt = scheduler.runtime()
    if rt is not None:
        stored = db.meta_get(rt.conn, consts.META_UI_HISTORY_COMPARE_MODE)
        if stored in _MODE_KEYS:
            key = stored
    return _MODE_KEYS.index(key)


def _banner(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("color:#cc3333;font-weight:bold;")
    label.setWordWrap(True)
    return label
