"""Small explicit-result dialogs used by snapshot and restore flows."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
    qconnect,
)

from ..dialog_results import accepted_choice
from ..i18n import tr


def edit_annotation(
    parent,
    *,
    title: str,
    user_label: str = "",
    pinned: bool = True,
) -> tuple[str, bool] | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    root = QVBoxLayout(dialog)
    form = QFormLayout()
    label = QLineEdit(user_label)
    label.setMaxLength(200)
    form.addRow(tr("version_name"), label)
    pin = QCheckBox(tr("pin_important"))
    pin.setChecked(pinned)
    form.addRow("", pin)
    root.addLayout(form)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel
    )
    qconnect(buttons.accepted, dialog.accept)
    qconnect(buttons.rejected, dialog.reject)
    root.addWidget(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return label.text().strip(), pin.isChecked()


def choose_deck(parent, decks: Sequence[object]) -> int | None:
    """Return the selected deck id, or None for Cancel/close/empty input."""
    if not decks:
        return None
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("restore_pick_deck"))
    root = QVBoxLayout(dialog)
    picker = QComboBox()
    for deck in decks:
        picker.addItem(str(deck.name), int(deck.id))
    root.addWidget(picker)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel
    )
    qconnect(buttons.accepted, dialog.accept)
    qconnect(buttons.rejected, dialog.reject)
    root.addWidget(buttons)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    selected = accepted_choice(
        accepted,
        [int(deck.id) for deck in decks],
        picker.currentIndex(),
    )
    return int(selected) if selected is not None else None


def choose_history(parent, candidates: Sequence[object]) -> str | None:
    if not candidates:
        return None
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("connect_history_title"))
    root = QVBoxLayout(dialog)
    picker = QComboBox()
    for candidate in candidates:
        latest = (
            datetime.fromtimestamp(candidate.latest_ts / 1000).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if candidate.latest_ts
            else "—"
        )
        picker.addItem(
            tr(
                "connect_history_item",
                profile=candidate.profile_name or "—",
                rows=candidate.row_count,
                latest=latest,
            ),
            candidate.storage_key,
        )
    root.addWidget(picker)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel
    )
    qconnect(buttons.accepted, dialog.accept)
    qconnect(buttons.rejected, dialog.reject)
    root.addWidget(buttons)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return accepted_choice(
        accepted,
        [str(candidate.storage_key) for candidate in candidates],
        picker.currentIndex(),
    )
