"""Note version history dialog: timeline (left) + per-field diff (right),
with whole-version / per-field restore and manual snapshot."""

from __future__ import annotations

from anki.errors import NotFoundError
from aqt import mw
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSplitter,
    Qt,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import askUser, chooseList, tooltip

from .. import capture_notes, diffing, scheduler
from ..i18n import tr
from ..records import NoteVersion
from . import actions, widgets

# Non-modal dialogs need a Python-side reference or Qt's wrapper may be GC'd.
_open_dialogs: set["HistoryDialog"] = set()


def open_for_note(parent, nid: int) -> None:
    rt = scheduler.runtime()
    if rt is None:
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
        self._field_checks: dict[str, QCheckBox] = {}
        self.setWindowTitle(tr("hd_title", nid=self._nid))
        self.resize(980, 640)
        # Opened from the Browser: be modal to it, so it must be closed before
        # browsing continues (consistent with the card-type editor flow).
        if type(parent).__name__ == "Browser":
            self.setWindowModality(Qt.WindowModality.WindowModal)
        self._build_ui()
        self._reload()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        _open_dialogs.discard(self)
        super().closeEvent(event)

    # --- UI construction ---

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        # Draggable split so the user can widen/narrow the timeline list.
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._list = QListWidget()
        self._list.setMinimumWidth(200)  # floor so the timestamp line still fits
        qconnect(self._list.currentRowChanged, lambda _row: self._render())
        splitter.addWidget(self._list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._mode = QComboBox()
        # index 0 = view only (default), 1 = vs current, 2 = vs previous
        self._mode.addItems(
            [tr("hd_view_only"), tr("hd_diff_vs_current"), tr("hd_diff_vs_previous")]
        )
        qconnect(self._mode.currentIndexChanged, lambda _idx: self._render())
        right_layout.addWidget(self._mode)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        right_layout.addWidget(self._scroll, 1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)  # list keeps its width…
        splitter.setStretchFactor(1, 1)  # …the diff pane absorbs resizing
        splitter.setChildrenCollapsible(False)  # can shrink to min, not vanish
        splitter.setSizes([300, 680])
        root.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self._snapshot_button = QPushButton(tr("hd_snapshot_now"))
        qconnect(self._snapshot_button.clicked, self._snapshot_now)
        buttons.addWidget(self._snapshot_button)
        buttons.addStretch(1)
        self._restore_fields_button = QPushButton(tr("hd_restore_fields"))
        qconnect(self._restore_fields_button.clicked, self._restore_fields)
        buttons.addWidget(self._restore_fields_button)
        self._restore_button = QPushButton(tr("hd_restore_version"))
        qconnect(self._restore_button.clicked, self._restore_version)
        buttons.addWidget(self._restore_button)
        close_button = QPushButton(tr("hd_close"))
        qconnect(close_button.clicked, self.close)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

    # --- data ---

    def _reload(self) -> None:
        rt = scheduler.runtime()
        if rt is None:
            return
        self._versions = capture_notes.list_note_versions(rt.conn, self._nid)
        self._list.clear()
        for version in self._versions:
            line1, line2 = widgets.timeline_lines(version)
            widgets.add_two_line_item(
                self._list, line1, line2, highlight_red=version.deleted
            )
        if self._versions:
            self._list.setCurrentRow(0)
        else:
            self._render()

    def _current_version(self) -> NoteVersion | None:
        row = self._list.currentRow()
        if 0 <= row < len(self._versions):
            return self._versions[row]
        return None

    def _live_fields(self) -> dict[str, str] | None:
        """name→value of the live note, or None if it no longer exists."""
        try:
            note = mw.col.get_note(self._nid)
        except NotFoundError:
            return None
        return {name: note[name] for name in note.keys()}

    def _base_fields(self, _version: NoteVersion) -> dict[str, str]:
        """What the diff compares against (current note or previous version).
        View-only mode never calls this."""
        if self._mode.currentIndex() == 1:  # vs current
            return self._live_fields() or {}
        row = self._list.currentRow()  # vs previous
        if 0 <= row < len(self._versions) - 1:
            previous = self._versions[row + 1]
            if not previous.deleted:
                return dict(zip(previous.field_names, previous.fields))
        return {}

    # --- rendering ---

    def _render(self) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)
        self._field_checks = {}
        version = self._current_version()
        if version is None:
            layout.addWidget(QLabel(tr("hd_no_versions")))
        else:
            self._render_version(layout, version)
        layout.addStretch(1)
        self._scroll.setWidget(container)

    def _render_version(self, layout: QVBoxLayout, version: NoteVersion) -> None:
        live_exists = self._live_fields() is not None
        deleted_flow = version.deleted or not live_exists
        if version.deleted:
            layout.addWidget(_banner(tr("hd_deleted_banner")))
        elif not live_exists:
            layout.addWidget(_banner(tr("hd_note_missing_banner")))

        self._restore_button.setText(
            tr("hd_restore_as_new") if deleted_flow else tr("hd_restore_version")
        )
        self._restore_fields_button.setEnabled(not deleted_flow)
        if version.deleted:
            return  # deletion markers carry no content to show

        view_only = self._mode.currentIndex() == 0
        insert_style, delete_style = widgets.diff_styles()
        base = {} if view_only else self._base_fields(version)
        for name, value in zip(version.field_names, version.fields):
            check = QCheckBox(name)
            # view-only has no diff to flag changes → offer all fields for restore
            check.setChecked(True if view_only else base.get(name, "") != value)
            self._field_checks[name] = check
            layout.addWidget(check)
            view = widgets.NoLoadTextBrowser()
            if view_only:
                view.setHtml(diffing.plain_to_html(value))
            else:
                spans = diffing.word_diff(base.get(name, ""), value)
                view.setHtml(
                    diffing.spans_to_html(
                        spans, insert_style=insert_style, delete_style=delete_style
                    )
                )
            view.setMaximumHeight(170)
            layout.addWidget(view)
        layout.addWidget(QLabel(f"{tr('hd_tags')}: {' '.join(version.tags)}"))

    # --- actions ---

    def _restore_version(self) -> None:
        version = self._current_version()
        if version is None:
            return
        if version.deleted or self._live_fields() is None:
            self._restore_as_new(version)
            return
        when = widgets.format_timestamp(version.ts)
        if not askUser(tr("confirm_restore", when=when), parent=self):
            return
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
        if version.deleted:
            # deletion markers carry no content; use the newest content row
            source = next((v for v in self._versions if not v.deleted), None)
            if source is None:
                return
        if not askUser(tr("restore_as_new_prompt"), parent=self):
            return
        decks = list(mw.col.decks.all_names_and_ids())
        index = chooseList(
            tr("restore_pick_deck"), [deck.name for deck in decks], parent=self
        )
        actions.restore_deleted_as_new(self, source, int(decks[index].id), self._reload)

    def _snapshot_now(self) -> None:
        actions.snapshot_notes([self._nid])
        self._reload()


def _banner(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("color:#cc3333;font-weight:bold;")
    return label
