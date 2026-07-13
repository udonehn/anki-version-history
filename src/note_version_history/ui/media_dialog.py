"""Media history dialog: file list + per-file event timeline + restore."""

from __future__ import annotations

from aqt import mw
from aqt.qt import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSplitter,
    Qt,
    QVBoxLayout,
    qconnect,
)
from aqt.utils import askUser, showWarning, tooltip

from .. import capture_media, scheduler
from ..i18n import tr
from ..records import MediaEvent
from . import widgets

_open_dialogs: set["MediaHistoryDialog"] = set()


def open_dialog(parent=None) -> None:
    rt = scheduler.runtime()
    if rt is None:
        tooltip(tr("no_profile_open"))
        return
    dialog = MediaHistoryDialog(parent or mw)
    _open_dialogs.add(dialog)
    dialog.show()


class MediaHistoryDialog(QDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self._events: list[MediaEvent] = []
        self.setWindowTitle(tr("md_title"))
        self.resize(900, 600)
        self._build_ui()
        self._reload_files()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        _open_dialogs.discard(self)
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel(tr("md_filter")))
        self._filter = QLineEdit()
        qconnect(self._filter.textChanged, lambda _text: self._reload_files())
        top.addWidget(self._filter, 1)
        scan_button = QPushButton(tr("md_scan_now"))
        qconnect(scan_button.clicked, self._scan_now)
        top.addWidget(scan_button)
        root.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._files = QListWidget()
        self._files.setMinimumWidth(160)
        qconnect(self._files.currentRowChanged, lambda _row: self._reload_events())
        splitter.addWidget(self._files)
        self._timeline = QListWidget()
        self._timeline.setMinimumWidth(160)
        splitter.addWidget(self._timeline)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([420, 420])
        root.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        self._stats = QLabel("")
        bottom.addWidget(self._stats)
        bottom.addStretch(1)
        restore_button = QPushButton(tr("md_restore"))
        qconnect(restore_button.clicked, self._restore)
        bottom.addWidget(restore_button)
        close_button = QPushButton(tr("hd_close"))
        qconnect(close_button.clicked, self.close)
        bottom.addWidget(close_button)
        root.addLayout(bottom)

    # --- data ---

    def _reload_files(self) -> None:
        rt = scheduler.runtime()
        if rt is None:
            return
        self._files.clear()
        for fname, last_event, ts in capture_media.list_media_files(
            rt.conn, self._filter.text().strip()
        ):
            item = widgets.add_two_line_item(
                self._files,
                fname,
                f"{tr('event_' + last_event)} · {widgets.format_timestamp(ts)}",
            )
            item.setData(0x0100, fname)  # Qt.ItemDataRole.UserRole
        self._reload_events()
        self._update_stats()

    def _current_fname(self) -> str | None:
        item = self._files.currentItem()
        return item.data(0x0100) if item is not None else None

    def _reload_events(self) -> None:
        rt = scheduler.runtime()
        self._timeline.clear()
        self._events = []
        fname = self._current_fname()
        if rt is None or fname is None:
            return
        self._events = capture_media.list_media_events(rt.conn, fname)
        for event in self._events:
            size_kb = event.size / 1000
            self._timeline.addItem(
                f"{widgets.format_timestamp(event.ts)} · {tr('event_' + event.event)}"
                f" · {size_kb:.1f} KB · {tr('origin_' + event.origin)}"
            )

    def _update_stats(self) -> None:
        rt = scheduler.runtime()
        if rt is None:
            return
        stats = rt.blobs.stats()
        events = rt.conn.execute("select count(*) from media_events").fetchone()[0]
        self._stats.setText(
            tr(
                "md_stats",
                count=stats.count,
                mb=stats.total_bytes / 1_000_000,
                events=events,
            )
        )

    # --- actions ---

    def _restore(self) -> None:
        rt = scheduler.runtime()
        fname = self._current_fname()
        row = self._timeline.currentRow()
        if rt is None or fname is None or not (0 <= row < len(self._events)):
            tooltip(tr("md_no_selection"))
            return
        event = self._events[row]
        if not askUser(tr("md_restore_confirm", fname=fname), parent=self):
            return
        try:
            capture_media.restore_media_file(
                mw.col, rt.conn, rt.blobs, fname, event.sha1
            )
        except (OSError, ValueError) as exc:
            showWarning(tr("md_restore_failed", error=str(exc)))
            return
        tooltip(tr("md_restore_done"))
        self._reload_files()

    def _scan_now(self) -> None:
        def on_done(report) -> None:
            tooltip(
                tr(
                    "md_scan_done",
                    added=report.added,
                    modified=report.modified,
                    deleted=report.deleted,
                )
            )
            self._reload_files()

        if not scheduler.request_media_scan(on_done):
            tooltip(tr("md_not_ready"))
