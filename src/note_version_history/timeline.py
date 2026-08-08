"""Shared, headless timeline paging and annotation primitives."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Generic, TypeVar

from . import consts, strings

PAGE_SIZE = 100
MAX_LABEL_LENGTH = 200

T = TypeVar("T")

_DISPLAY_SEARCH_KEYS = {
    consts.ORIGIN_BASELINE: "origin_baseline",
    consts.ORIGIN_AUTO: "origin_auto",
    consts.ORIGIN_MANUAL: "origin_manual",
    consts.ORIGIN_RESTORE: "origin_restore",
    consts.LABEL_DELETE_NOTE: consts.LABEL_DELETE_NOTE,
    consts.LABEL_UNDO_DELETE: consts.LABEL_UNDO_DELETE,
    consts.LABEL_FULL_RESCAN: consts.LABEL_FULL_RESCAN,
    consts.LABEL_DELETE_NOTETYPE: consts.LABEL_DELETE_NOTETYPE,
    consts.LABEL_SYNC: consts.LABEL_SYNC,
}


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


def search_aliases(value: str) -> tuple[str, ...]:
    """Expand translated display-label searches to their stored tokens.

    Timeline rows store stable origins/sentinels, while the UI displays their
    English or Korean translations.  A substring of either display label must
    therefore add the corresponding raw token to the SQL search.
    """
    query = value.casefold()
    probe = value.strip().casefold()
    aliases = [query]
    if not probe:
        return tuple(aliases)
    for stored, label_key in _DISPLAY_SEARCH_KEYS.items():
        labels = (
            translations.get(label_key, "").casefold()
            for translations in strings.STRINGS.values()
        )
        if probe in stored.casefold() or any(probe in label for label in labels):
            aliases.append(stored.casefold())
    return tuple(dict.fromkeys(aliases))


def matches_deleted_display(value: str) -> bool:
    """Whether a query matches the deleted suffix shown on timeline rows."""
    probe = value.strip().casefold()
    return bool(probe) and any(
        probe in translations.get("ntd_deleted_suffix", "").casefold()
        for translations in strings.STRINGS.values()
    )


def text_search_clause(
    value: str,
    columns: tuple[str, ...],
    *,
    deleted_column: str | None = None,
) -> tuple[str, list[object]]:
    """Build a shared case-insensitive search across trusted text columns."""
    parts: list[str] = []
    params: list[object] = []
    for alias in search_aliases(value):
        pattern = like_pattern(alias)
        for column in columns:
            parts.append(f"lower({column}) LIKE ? ESCAPE '\\'")
            params.append(pattern)
    if deleted_column is not None and matches_deleted_display(value):
        parts.append(f"{deleted_column}=1")
    return "(" + " OR ".join(parts) + ")", params


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
