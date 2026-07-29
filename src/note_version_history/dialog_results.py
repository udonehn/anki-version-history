"""Headless result normalization for Qt choice dialogs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def accepted_choice(
    accepted: bool, choices: Sequence[T], selected_index: int
) -> T | None:
    if not accepted or not (0 <= selected_index < len(choices)):
        return None
    return choices[selected_index]
