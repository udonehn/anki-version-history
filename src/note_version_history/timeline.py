"""Shared, headless timeline paging and annotation primitives."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Generic, TypeVar

PAGE_SIZE = 100
MAX_LABEL_LENGTH = 200

T = TypeVar("T")


@dataclass(frozen=True)
class TimelineFilter:
    search: str = ""
    category: str = "all"
    pinned_only: bool = False
    include_content: bool = False


@dataclass(frozen=True)
class VersionPage(Generic[T]):
    items: tuple[T, ...]
    total: int
    offset: int
    limit: int = PAGE_SIZE

    @property
    def start(self) -> int:
        return self.offset + 1 if self.total else 0

    @property
    def end(self) -> int:
        return min(self.offset + len(self.items), self.total)

    @property
    def has_previous(self) -> bool:
        return self.offset > 0

    @property
    def has_next(self) -> bool:
        return self.offset + len(self.items) < self.total


def normalized_label(value: str) -> str:
    return value.strip()[:MAX_LABEL_LENGTH]


def update_annotation(
    conn: sqlite3.Connection,
    table: str,
    version_id: int,
    *,
    user_label: str,
    pinned: bool,
) -> bool:
    if table not in {"note_versions", "notetype_versions"}:
        raise ValueError("unsupported version table")
    cursor = conn.execute(
        f"UPDATE {table} SET user_label=?, pinned=? WHERE id=?",
        (normalized_label(user_label), 1 if pinned else 0, int(version_id)),
    )
    return cursor.rowcount == 1


def like_pattern(value: str) -> str:
    """Return a LIKE pattern where user %, _ and backslashes are literals."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def category_clause(category: str, *, alias: str = "") -> tuple[str, list[object]]:
    prefix = f"{alias}." if alias else ""
    if category == "all":
        return "", []
    if category == "pinned":
        return f"{prefix}pinned=1", []
    if category == "deleted":
        return f"{prefix}deleted=1", []
    if category == "snapshot":
        return f"{prefix}origin='manual'", []
    if category == "restore":
        return f"{prefix}origin='restore'", []
    if category == "baseline":
        return f"{prefix}origin='baseline'", []
    if category == "automatic":
        return (
            f"{prefix}origin='auto' AND "
            f"({prefix}op_label='' OR {prefix}op_label NOT IN ('@sync'))",
            [],
        )
    if category == "sync":
        return f"{prefix}op_label='@sync'", []
    raise ValueError(f"unsupported timeline category: {category}")
