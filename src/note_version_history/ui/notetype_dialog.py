"""Note type version history dialog: picker + timeline + per-template /
CSS unified diffs, with templates+CSS restore and manual snapshot."""

from __future__ import annotations

import json

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
    QSplitter,
    QTimer,
    Qt,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import askUser, showWarning, tooltip

from .. import capture_notetypes, comparison, diffing, scheduler, timeline
from ..i18n import display_label, tr
from ..records import NotetypeVersion
from . import actions, dialogs, menus, widgets

_open_dialogs: set["NotetypeHistoryDialog"] = set()
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
        self._offset = 0
        self._total = 0
        self._a_id: int | None = None
        self._b_id: int | None = None
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
        # reject() (Esc) bypasses closeEvent; finished covers every close path
        qconnect(self.finished, lambda _result: _open_dialogs.discard(self))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        _open_dialogs.discard(self)
        super().closeEvent(event)

    # --- UI construction ---

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel(tr("ntd_pick")))
        self._picker = QComboBox()
        qconnect(
            self._picker.currentIndexChanged,
            self._on_picker_changed,
        )
        top.addWidget(self._picker, 1)
        self._mode = QComboBox()
        # index 0 = view only, 1 = vs current, 2 = vs previous, 3 = A→B
        self._mode.addItems(
            [
                tr("hd_view_only"),
                tr("ntd_diff_vs_current"),
                tr("hd_diff_vs_previous"),
                tr("compare_selected_versions"),
            ]
        )
        qconnect(self._mode.currentIndexChanged, self._on_mode_changed)
        top.addWidget(self._mode)
        self._set_a = QPushButton(tr("compare_set_a"))
        self._set_b = QPushButton(tr("compare_set_b"))
        self._swap = QPushButton(tr("compare_swap"))
        self._clear_ab = QPushButton(tr("compare_clear"))
        qconnect(self._set_a.clicked, lambda: self._set_endpoint("a"))
        qconnect(self._set_b.clicked, lambda: self._set_endpoint("b"))
        qconnect(self._swap.clicked, self._swap_endpoints)
        qconnect(self._clear_ab.clicked, self._clear_endpoints)
        for button in (self._set_a, self._set_b, self._swap, self._clear_ab):
            top.addWidget(button)
        root.addLayout(top)
        self._ab_status = QLabel()
        self._ab_status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self._ab_status)
        self._update_compare_mode_ui()

        filters = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("timeline_search"))
        filters.addWidget(self._search, 1)
        self._filter = QComboBox()
        for key in _FILTER_KEYS:
            self._filter.addItem(tr(f"filter_{key}"), key)
        filters.addWidget(self._filter)
        self._content_search = QCheckBox(tr("search_content"))
        self._pinned_only = QCheckBox(tr("pinned_only"))
        filters.addWidget(self._content_search)
        filters.addWidget(self._pinned_only)
        root.addLayout(filters)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        qconnect(
            self._search_timer.timeout,
            lambda: self._reload_timeline(reset_offset=True),
        )
        qconnect(self._search.textChanged, lambda _text: self._search_timer.start())
        qconnect(
            self._filter.currentIndexChanged,
            lambda _idx: self._reload_timeline(reset_offset=True),
        )
        qconnect(
            self._content_search.toggled,
            lambda _checked: self._reload_timeline(reset_offset=True),
        )
        qconnect(
            self._pinned_only.toggled,
            lambda _checked: self._reload_timeline(reset_offset=True),
        )

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        self._list.setMinimumWidth(200)  # floor so the timestamp line still fits
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
        self._annotate_button = QPushButton(tr("edit_version_metadata"))
        qconnect(self._annotate_button.clicked, self._edit_annotation)
        buttons.addWidget(self._annotate_button)
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

    def _reload_timeline(
        self, *, reset_offset: bool = False, select_id: int | None = None
    ) -> None:
        rt = scheduler.runtime()
        mid = self._current_mid()
        if reset_offset:
            self._offset = 0
        self._list.clear()
        self._versions = []
        if rt is None or mid is None:
            self._render()
            return
        page = capture_notetypes.page_notetype_versions(
            rt.conn,
            mid,
            timeline.TimelineFilter(
                search=self._search.text(),
                category=str(self._filter.currentData() or "all"),
                pinned_only=self._pinned_only.isChecked(),
                include_content=self._content_search.isChecked(),
            ),
            offset=self._offset,
        )
        self._versions = list(page.items)
        self._total = page.total
        selected_row = 0
        for index, version in enumerate(self._versions):
            label = display_label(version.op_label, version.origin)
            if version.user_label:
                label = f"{version.user_label} · {label}"
            if version.deleted:
                label = f"{label} {tr('ntd_deleted_suffix')}"
            widgets.add_two_line_item(
                self._list,
                # same icon language as the note timeline (origin/deleted/sync)
                f"{widgets.row_icon(version)} {widgets.format_timestamp(version.ts)}",
                label,
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
        self._reload_timeline()

    def _page_forward(self) -> None:
        if self._offset + timeline.PAGE_SIZE < self._total:
            self._offset += timeline.PAGE_SIZE
            self._reload_timeline()

    def _on_picker_changed(self, _index: int) -> None:
        self._a_id = self._b_id = None
        self._update_ab_status()
        self._reload_timeline(reset_offset=True)

    def _on_mode_changed(self, _index: int) -> None:
        self._update_compare_mode_ui()
        self._render()

    def _update_compare_mode_ui(self) -> None:
        visible = self._mode.currentIndex() == 3
        for button in (self._set_a, self._set_b, self._swap, self._clear_ab):
            button.setVisible(visible)
        self._ab_status.setVisible(visible)

    def _endpoint(self, version_id: int | None) -> NotetypeVersion | None:
        rt = scheduler.runtime()
        return (
            capture_notetypes.get_notetype_version(rt.conn, version_id)
            if rt is not None and version_id is not None
            else None
        )

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

    def _restore_target(self) -> NotetypeVersion | None:
        explicit_mode = self._mode.currentIndex() == 3
        return comparison.action_target(
            self._current_version(),
            explicit_mode=explicit_mode,
            endpoint_a=self._endpoint(self._a_id) if explicit_mode else None,
            endpoint_b=self._endpoint(self._b_id) if explicit_mode else None,
        )

    # --- rendering ---

    def _base_config(self, target: NotetypeVersion) -> dict:
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
        rt = scheduler.runtime()
        previous = (
            capture_notetypes.get_previous_notetype_version(rt.conn, target.id)
            if rt is not None and target.id is not None
            else None
        )
        if previous is not None and not previous.deleted and previous.config_json:
            try:
                return json.loads(previous.config_json)
            except json.JSONDecodeError:
                return {}
        return {}

    def _render(self) -> None:
        self._tabs.clear()
        explicit_mode = self._mode.currentIndex() == 3
        endpoint_a = self._endpoint(self._a_id) if explicit_mode else None
        endpoint_b = self._endpoint(self._b_id) if explicit_mode else None
        explicit_ab = (
            explicit_mode and endpoint_a is not None and endpoint_b is not None
        )
        if explicit_mode and not explicit_ab:
            self._tabs.addTab(QLabel(tr("compare_select_both")), "A→B")
            self._restore_button.setEnabled(False)
            self._annotate_button.setEnabled(self._current_version() is not None)
            return
        version = comparison.action_target(
            self._current_version(),
            explicit_mode=explicit_mode,
            endpoint_a=endpoint_a,
            endpoint_b=endpoint_b,
        )
        if version is None:
            self._tabs.addTab(QLabel(tr("ntd_no_versions")), "—")
            self._restore_button.setEnabled(False)
            self._annotate_button.setEnabled(False)
            return
        self._annotate_button.setEnabled(self._current_version() is not None)
        mid = self._current_mid()
        live_exists = bool(mw.col and mid is not None and mw.col.models.get(mid))
        if (version.deleted or not version.config_json) and not explicit_ab:
            banner = QLabel(tr("ntd_deleted_banner"))
            banner.setStyleSheet("color:#cc3333;font-weight:bold;padding:12px;")
            self._tabs.addTab(banner, "—")
            self._restore_button.setEnabled(False)
            return
        self._restore_button.setEnabled(
            live_exists and not version.deleted and bool(version.config_json)
        )

        view_only = self._mode.currentIndex() == 0 and not explicit_ab
        if explicit_ab:
            surface = comparison.compare_notetypes(endpoint_a, version)
            base = {"css": surface.a_css}
            config = {"css": surface.b_css}
            base_templates = surface.a_templates
            target_templates = surface.b_templates
            template_names = list(surface.template_names)
        else:
            config = _version_config(version)
            base = {} if view_only else self._base_config(version)
            base_templates = {
                t.get("name", ""): t for t in base.get("tmpls", [])
            }
            target_templates = {
                t.get("name", ""): t for t in config.get("tmpls", [])
            }
            template_names = list(
                dict.fromkeys((*base_templates.keys(), *target_templates.keys()))
            )
        insert_style, delete_style = widgets.diff_styles()
        label = widgets.format_timestamp(version.ts)

        for name in template_names:
            template = target_templates.get(name, {})
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
        version = self._restore_target()
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
        options = dialogs.edit_annotation(
            self, title=tr("snapshot_options"), pinned=True
        )
        if options is None:
            return
        label, pinned = options
        actions.snapshot_notetype(
            mid,
            user_label=label,
            pinned=pinned,
            on_done=lambda: self._reload_timeline(reset_offset=True),
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
        capture_notetypes.update_notetype_annotation(
            rt.conn, version.id, user_label=label, pinned=pinned
        )
        self._reload_timeline(select_id=version.id)


def _version_config(version: NotetypeVersion | None) -> dict:
    if version is None or version.deleted or not version.config_json:
        return {}
    try:
        value = json.loads(version.config_json)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _version_short(version: NotetypeVersion | None) -> str:
    if version is None:
        return "—"
    return version.user_label or widgets.format_timestamp(version.ts)
