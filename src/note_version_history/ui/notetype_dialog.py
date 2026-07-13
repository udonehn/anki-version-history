"""Note type version history dialog: picker + timeline + per-template /
CSS unified diffs, with templates+CSS restore and manual snapshot."""

from __future__ import annotations

import json

from aqt import mw
from aqt.qt import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    Qt,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import askUser, showWarning, tooltip

from .. import capture_notetypes, diffing, scheduler
from ..i18n import display_label, tr
from ..records import NotetypeVersion
from . import actions, menus, widgets

_open_dialogs: set["NotetypeHistoryDialog"] = set()


def open_dialog(parent=None, preselect_mid: int | None = None) -> None:
    rt = scheduler.runtime()
    if rt is None:
        tooltip(tr("no_profile_open"))
        return
    dialog = NotetypeHistoryDialog(parent or mw, preselect_mid)
    _open_dialogs.add(dialog)
    dialog.show()


class NotetypeHistoryDialog(QDialog):
    def __init__(self, parent, preselect_mid: int | None = None) -> None:
        super().__init__(parent)
        self._entries: list[tuple[int, str, bool]] = []  # (mid, name, alive)
        self._versions: list[NotetypeVersion] = []
        self.setWindowTitle(tr("ntd_title"))
        self.resize(1000, 660)
        # Opened from the card-type editor (🕘 button): be modal to it so the
        # two windows can't fight over the shared in-memory model — the editor
        # is reachable again only once this dialog is closed. Opened from the
        # Tools menu (parent = main window), stay non-modal.
        if type(parent).__name__ == "CardLayout":
            self.setWindowModality(Qt.WindowModality.WindowModal)
        self._build_ui()
        self._populate_picker(preselect_mid)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        _open_dialogs.discard(self)
        super().closeEvent(event)

    # --- UI construction ---

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel(tr("ntd_pick")))
        self._picker = QComboBox()
        qconnect(self._picker.currentIndexChanged, lambda _idx: self._reload_timeline())
        top.addWidget(self._picker, 1)
        self._mode = QComboBox()
        # index 0 = view only (default), 1 = vs current, 2 = vs previous
        self._mode.addItems(
            [tr("hd_view_only"), tr("ntd_diff_vs_current"), tr("hd_diff_vs_previous")]
        )
        qconnect(self._mode.currentIndexChanged, lambda _idx: self._render())
        top.addWidget(self._mode)
        root.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._list = QListWidget()
        self._list.setMinimumWidth(200)  # floor so the timestamp line still fits
        qconnect(self._list.currentRowChanged, lambda _row: self._render())
        splitter.addWidget(self._list)

        self._tabs = QTabWidget()
        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([300, 700])
        root.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self._snapshot_button = QPushButton(tr("hd_snapshot_now"))
        qconnect(self._snapshot_button.clicked, self._snapshot_now)
        buttons.addWidget(self._snapshot_button)
        buttons.addStretch(1)
        self._restore_button = QPushButton(tr("ntd_restore"))
        qconnect(self._restore_button.clicked, self._restore)
        buttons.addWidget(self._restore_button)
        close_button = QPushButton(tr("hd_close"))
        qconnect(close_button.clicked, self.close)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

    # --- data ---

    def _populate_picker(self, preselect_mid: int | None) -> None:
        rt = scheduler.runtime()
        if rt is None or mw is None or mw.col is None:
            return
        live = {int(entry.id): entry.name for entry in mw.col.models.all_names_and_ids()}
        self._entries = [
            (mid, name, True)
            for mid, name in sorted(live.items(), key=lambda kv: kv[1].lower())
        ]
        # note types that exist only in history (deleted) stay browsable
        for row in rt.conn.execute("select mid, alive from notetype_index"):
            mid = int(row["mid"])
            if row["alive"] == 0 and mid not in live:
                last = rt.conn.execute(
                    "select name from notetype_versions where mid=? and name != ''"
                    " order by id desc limit 1",
                    (mid,),
                ).fetchone()
                name = last["name"] if last is not None else str(mid)
                self._entries.append((mid, f"{name} {tr('ntd_deleted_suffix')}", False))
        self._picker.clear()
        for _mid, name, _alive in self._entries:
            self._picker.addItem(name)
        if preselect_mid is not None:
            for index, (mid, _name, _alive) in enumerate(self._entries):
                if mid == int(preselect_mid):
                    self._picker.setCurrentIndex(index)
                    break
        self._reload_timeline()

    def _current_mid(self) -> int | None:
        index = self._picker.currentIndex()
        if 0 <= index < len(self._entries):
            return self._entries[index][0]
        return None

    def _current_version(self) -> NotetypeVersion | None:
        row = self._list.currentRow()
        if 0 <= row < len(self._versions):
            return self._versions[row]
        return None

    def _reload_timeline(self) -> None:
        rt = scheduler.runtime()
        mid = self._current_mid()
        self._list.clear()
        self._versions = []
        if rt is None or mid is None:
            self._render()
            return
        self._versions = capture_notetypes.list_notetype_versions(rt.conn, mid)
        for version in self._versions:
            label = display_label(version.op_label, version.origin)
            if version.deleted:
                label = f"{label} {tr('ntd_deleted_suffix')}"
            widgets.add_two_line_item(
                self._list,
                widgets.format_timestamp(version.ts),
                label,
                highlight_red=version.deleted,
            )
        if self._versions:
            self._list.setCurrentRow(0)
        else:
            self._render()

    # --- rendering ---

    def _base_config(self) -> dict:
        """What diffs compare against: the live notetype or the previous
        version. View-only mode never calls this."""
        if self._mode.currentIndex() == 1:  # vs current
            mid = self._current_mid()
            if mid is None:
                return {}
            # If the card-type editor is open, "current" means its live
            # in-memory buffer (UNSAVED edits included) — re-read on each
            # render. Otherwise fall back to the last saved state in the DB.
            clayout = menus.open_clayout_for(mid)
            editing = getattr(clayout, "model", None) if clayout is not None else None
            if isinstance(editing, dict):
                return editing
            live = mw.col.models.get(mid) if mw.col is not None else None
            return live or {}
        row = self._list.currentRow()  # vs previous
        if 0 <= row < len(self._versions) - 1:
            previous = self._versions[row + 1]
            if not previous.deleted and previous.config_json:
                try:
                    return json.loads(previous.config_json)
                except json.JSONDecodeError:
                    return {}
        return {}

    def _render(self) -> None:
        self._tabs.clear()
        version = self._current_version()
        if version is None:
            self._tabs.addTab(QLabel(tr("ntd_no_versions")), "—")
            self._restore_button.setEnabled(False)
            return
        mid = self._current_mid()
        live_exists = bool(mw.col and mid is not None and mw.col.models.get(mid))
        if version.deleted or not version.config_json:
            banner = QLabel(tr("ntd_deleted_banner"))
            banner.setStyleSheet("color:#cc3333;font-weight:bold;padding:12px;")
            self._tabs.addTab(banner, "—")
            self._restore_button.setEnabled(False)
            return
        self._restore_button.setEnabled(live_exists)

        try:
            config = json.loads(version.config_json)
        except json.JSONDecodeError:
            self._tabs.addTab(QLabel(tr("restore_failed", error="bad JSON")), "—")
            return
        view_only = self._mode.currentIndex() == 0
        base = {} if view_only else self._base_config()
        base_templates = {t.get("name", ""): t for t in base.get("tmpls", [])}
        insert_style, delete_style = widgets.diff_styles()
        label = widgets.format_timestamp(version.ts)

        for template in config.get("tmpls", []):
            name = template.get("name", "?")
            base_template = base_templates.get(name, {})
            tab = QWidget()
            layout = QVBoxLayout(tab)
            if not live_exists:
                layout.addWidget(QLabel(tr("ntd_notetype_missing")))
            for key, label_key in (("qfmt", "ntd_front"), ("afmt", "ntd_back")):
                layout.addWidget(QLabel(tr(label_key)))
                view = widgets.NoLoadTextBrowser()
                if view_only:
                    view.setHtml(diffing.plain_to_html(template.get(key, ""), monospace=True))
                else:
                    diff_text = diffing.unified_text_diff(
                        base_template.get(key, ""), template.get(key, ""), "base", label
                    )
                    view.setHtml(
                        diffing.unified_to_html(
                            diff_text, insert_style=insert_style, delete_style=delete_style
                        )
                    )
                layout.addWidget(view, 1)
            self._tabs.addTab(tab, name)

        css_view = widgets.NoLoadTextBrowser()
        if view_only:
            css_view.setHtml(diffing.plain_to_html(config.get("css", ""), monospace=True))
        else:
            css_diff = diffing.unified_text_diff(
                base.get("css", ""), config.get("css", ""), "base", label
            )
            css_view.setHtml(
                diffing.unified_to_html(
                    css_diff, insert_style=insert_style, delete_style=delete_style
                )
            )
        self._tabs.addTab(css_view, tr("ntd_css_tab"))

    # --- actions ---

    def _restore(self) -> None:
        version = self._current_version()
        if version is None or version.deleted or not version.config_json:
            return
        when = widgets.format_timestamp(version.ts)
        if not askUser(
            tr("ntd_confirm_restore", name=version.name, when=when), parent=self
        ):
            return
        # An open CardLayout holds its own in-memory copy of this notetype —
        # writing the DB behind its back desyncs its preview/save state. Load
        # the version INTO the editor instead; the user confirms with Save.
        clayout = menus.open_clayout_for(version.mid)
        if clayout is not None:
            if actions.apply_notetype_version_into_clayout(clayout, version):
                tooltip(tr("ntd_loaded_into_editor"))
            else:
                showWarning(tr("ntd_editor_conflict"))
            return
        actions.restore_notetype_version(self, version, self._reload_timeline)

    def _snapshot_now(self) -> None:
        mid = self._current_mid()
        if mid is None:
            return
        actions.snapshot_notetype(mid)
        self._reload_timeline()
