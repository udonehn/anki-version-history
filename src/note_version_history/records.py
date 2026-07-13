"""Immutable value objects shared between capture, storage and UI layers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NoteVersion:
    """One captured state of a note (append-only history row)."""

    nid: int
    guid: str
    mid: int
    ts: int  # capture wall time, epoch milliseconds
    origin: str  # consts.ORIGIN_*
    op_label: str
    fields: tuple[str, ...]
    field_names: tuple[str, ...]  # field schema at capture time
    tags: tuple[str, ...]
    hash: str
    deleted: bool = False  # True = deletion marker row (fields empty)
    id: int | None = None  # DB row id once stored


@dataclass(frozen=True)
class NotetypeVersion:
    """One captured state of a note type (full dict JSON + restorable-surface hash)."""

    mid: int
    ts: int
    origin: str
    op_label: str
    name: str
    config_json: str  # full notetype dict as JSON (tmpls, css, flds, ...)
    hash: str  # hash of name + templates(name,qfmt,afmt) + css only
    deleted: bool = False
    id: int | None = None


@dataclass(frozen=True)
class MediaEvent:
    """One media file event (added/modified/deleted); sha1 references the blob store."""

    fname: str
    ts: int
    origin: str
    event: str  # consts.EVENT_*
    sha1: str
    size: int
    id: int | None = None
