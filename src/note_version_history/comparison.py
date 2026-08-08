"""Headless semantic surfaces for arbitrary version A→B comparisons."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeVar

from .records import NotetypeVersion, NoteVersion

VersionT = TypeVar("VersionT")


@dataclass(frozen=True)
class NoteComparison:
    field_names: tuple[str, ...]
    a_fields: dict[str, str]
    b_fields: dict[str, str]
    a_tags: tuple[str, ...]
    b_tags: tuple[str, ...]


@dataclass(frozen=True)
class NotetypeComparison:
    template_names: tuple[str, ...]
    a_templates: dict[str, dict]
    b_templates: dict[str, dict]
    a_css: str
    b_css: str


def action_target(
    current: VersionT | None,
    *,
    explicit_mode: bool,
    endpoint_a: VersionT | None,
    endpoint_b: VersionT | None,
) -> VersionT | None:
    """Return the version represented by an action on the rendered surface.

    In A→B mode the right-hand surface is B, even when another timeline row
    remains selected.  Requiring both endpoints also makes a stale button event
    harmless while the comparison is incomplete.
    """
    if not explicit_mode:
        return current
    return endpoint_b if endpoint_a is not None and endpoint_b is not None else None


def compare_notes(
    a: NoteVersion | None, b: NoteVersion | None
) -> NoteComparison:
    a_fields = _note_fields(a)
    b_fields = _note_fields(b)
    return NoteComparison(
        field_names=tuple(dict.fromkeys((*a_fields.keys(), *b_fields.keys()))),
        a_fields=a_fields,
        b_fields=b_fields,
        a_tags=() if a is None or a.deleted else a.tags,
        b_tags=() if b is None or b.deleted else b.tags,
    )


def compare_notetypes(
    a: NotetypeVersion | None, b: NotetypeVersion | None
) -> NotetypeComparison:
    a_config = _config(a)
    b_config = _config(b)
    a_templates = {
        str(item.get("name", "")): item for item in a_config.get("tmpls", [])
    }
    b_templates = {
        str(item.get("name", "")): item for item in b_config.get("tmpls", [])
    }
    return NotetypeComparison(
        template_names=tuple(
            dict.fromkeys((*a_templates.keys(), *b_templates.keys()))
        ),
        a_templates=a_templates,
        b_templates=b_templates,
        a_css=str(a_config.get("css", "")),
        b_css=str(b_config.get("css", "")),
    )


def _note_fields(version: NoteVersion | None) -> dict[str, str]:
    if version is None or version.deleted:
        return {}
    return dict(zip(version.field_names, version.fields))


def _config(version: NotetypeVersion | None) -> dict:
    if version is None or version.deleted or not version.config_json:
        return {}
    try:
        value = json.loads(version.config_json)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
