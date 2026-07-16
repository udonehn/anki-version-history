"""Shared UI helpers: the resource-blocked text browser, theme-aware diff
styles, and timeline row formatting."""

from __future__ import annotations

from datetime import datetime

from aqt.qt import QLabel, QListWidget, QListWidgetItem, Qt, QTextBrowser
from aqt.theme import theme_manager

from .. import consts
from ..i18n import display_label, tr
from ..records import NoteVersion

# NB: U+FE0F (emoji variation selector) forces color-emoji rendering for
# code points that default to monochrome text glyphs (⏱ ↩ 🗑) — without it
# rows mix colored and grey icons depending on the platform font.
_ORIGIN_ICONS = {
    "baseline": "🏁",
    "auto": "⏱️",
    "manual": "📌",
    "restore": "↩️",
}
_DELETED_ICON = "🗑️"
_SYNC_ICON = "🔄"


def row_icon(version) -> str:
    """Timeline row icon for a note or notetype version (duck-typed: needs
    ``deleted``, ``op_label``, ``origin``). Event-specific icons (deletion,
    sync) win over the plain origin icon so the icon column is scannable."""
    if version.deleted:
        return _DELETED_ICON
    if version.op_label == consts.LABEL_SYNC:
        return _SYNC_ICON
    return _ORIGIN_ICONS.get(version.origin, "•")


class NoLoadTextBrowser(QTextBrowser):
    """Rich-text viewer that cannot execute or fetch anything.

    QTextBrowser has no JS engine at all, and overriding loadResource blocks
    file/network loads — so even hostile field content rendered (escaped)
    into the diff view stays inert."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setReadOnly(True)

    def loadResource(self, resource_type: int, name):  # noqa: N802 - Qt override
        return None


def diff_styles() -> tuple[str, str]:
    """(insert_style, delete_style) CSS matching the active Anki theme."""
    if theme_manager.night_mode:
        return (
            "background-color:#1e4620;color:#c8e6c9;",
            "background-color:#5c2626;color:#ffcdd2;",
        )
    return ("background-color:#d4f7d4;", "background-color:#f7d4d4;")


def format_timestamp(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def timeline_lines(version: NoteVersion) -> tuple[str, str]:
    """(time line, label line) for a note-version timeline row."""
    label = display_label(version.op_label, version.origin)
    if version.deleted:
        label = f"{label} {tr('ntd_deleted_suffix')}"
    return f"{row_icon(version)} {format_timestamp(version.ts)}", label


def add_two_line_item(
    list_widget: QListWidget,
    line1: str,
    line2: str,
    *,
    highlight_red: bool = False,
) -> QListWidgetItem:
    """Append a two-line row rendered by a QLabel widget.

    Item-delegate text rendering is unreliable here: Anki's style fixes item
    height to one line, so multi-line item TEXT gets collapsed and elided to
    "…" regardless of width/wrap/elide settings. A per-row label widget with
    an explicit size hint is immune to that."""
    item = QListWidgetItem()
    label = QLabel(f"{line1}\n{line2}" if line2 else line1)
    label.setWordWrap(True)
    label.setContentsMargins(8, 4, 8, 4)
    # clicks must fall through to the list so rows stay selectable
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    if highlight_red:
        label.setStyleSheet("color:#cc3333;")
    item.setSizeHint(label.sizeHint())
    list_widget.addItem(item)
    list_widget.setItemWidget(item, label)
    return item
