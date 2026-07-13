"""Restore operations — pylib only, all through public undoable APIs.

Every restore is wrapped in a named custom undo entry
(``add_custom_undo_entry`` … ``merge_undo_entries``), so the user can Ctrl+Z
a restore like any other change; the capture pipeline then records that undo
too (append-only history, reflog-style).

The aqt layer (ui/actions.py) runs these inside a CollectionOp with the
RESTORE_INITIATOR sentinel and writes the ``origin='restore'`` history row on
success.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from anki.collection import Collection, OpChanges

from .records import NotetypeVersion, NoteVersion


class RestoreError(Exception):
    """Restore could not proceed (target missing / incompatible)."""


class GuidMismatchError(RestoreError):
    """The live note's guid differs from the version's — the nid was reused
    (e.g. after an import); in-place restore would hit the wrong note."""


@dataclass(frozen=True)
class NoteRestoreResult:
    changes: OpChanges
    applied_fields: tuple[str, ...]
    skipped_fields: tuple[str, ...]  # names from the version absent today


@dataclass(frozen=True)
class RestoreAsNewResult:
    changes: OpChanges
    new_nid: int


@dataclass(frozen=True)
class NotetypeRestoreResult:
    changes: OpChanges
    applied_templates: tuple[str, ...]
    missing_in_current: tuple[str, ...]  # stored templates with no live match
    missing_in_stored: tuple[str, ...]  # live templates kept as-is


def apply_note_version(
    col: Collection,
    version: NoteVersion,
    only_fields: set[str] | None,
    undo_name: str,
) -> NoteRestoreResult:
    """Overwrite the live note's fields (matched BY NAME, so field renames
    don't scramble content) and, on whole-version restore, its tags."""
    note = col.get_note(version.nid)  # NotFoundError → caller offers restore-as-new
    if version.guid and note.guid != version.guid:
        raise GuidMismatchError(
            f"note {version.nid}: live guid {note.guid!r} != version guid {version.guid!r}"
        )
    current_names = set(note.keys())
    undo_pos = col.add_custom_undo_entry(undo_name)
    applied: list[str] = []
    skipped: list[str] = []
    for name, value in zip(version.field_names, version.fields):
        if only_fields is not None and name not in only_fields:
            continue
        if name in current_names:
            note[name] = value
            applied.append(name)
        else:
            skipped.append(name)
    if only_fields is None:
        note.tags = list(version.tags)
    # NOTE: update_note must create its own undo entry here; skip_undo_entry
    # would make the change non-undoable, which INVALIDATES the whole undo
    # queue (including our custom entry) → "target undo op not found".
    # Verified empirically on 26.05: normal update + merge = one named step.
    col.update_note(note)
    changes = col.merge_undo_entries(undo_pos)
    return NoteRestoreResult(
        changes=changes, applied_fields=tuple(applied), skipped_fields=tuple(skipped)
    )


def restore_deleted_note_as_new(
    col: Collection,
    version: NoteVersion,
    deck_id: int,
    undo_name: str,
) -> RestoreAsNewResult:
    """A deleted nid cannot be resurrected via public APIs — recreate the
    content as a NEW note in the chosen deck."""
    notetype = col.models.get(version.mid)
    if notetype is None:
        raise RestoreError(f"note type {version.mid} no longer exists")
    undo_pos = col.add_custom_undo_entry(undo_name)
    note = col.new_note(notetype)
    current_names = set(note.keys())
    for name, value in zip(version.field_names, version.fields):
        if name in current_names:
            note[name] = value
    note.tags = list(version.tags)
    col.add_note(note, deck_id)
    changes = col.merge_undo_entries(undo_pos)
    return RestoreAsNewResult(changes=changes, new_nid=int(note.id))


def apply_notetype_version(
    col: Collection,
    version: NotetypeVersion,
    undo_name: str,
) -> NotetypeRestoreResult:
    """Restore templates (matched BY NAME) + CSS only. Fields/schema are never
    touched — no forced full sync. Count/name mismatches are reported, never
    'fixed' (templates are not added or removed)."""
    notetype = col.models.get(version.mid)
    if notetype is None:
        raise RestoreError(f"note type {version.mid} no longer exists")
    try:
        stored = json.loads(version.config_json)
    except json.JSONDecodeError as exc:
        raise RestoreError(f"stored version is unreadable: {exc}") from exc
    stored_templates = {t.get("name", ""): t for t in stored.get("tmpls", [])}
    current_templates = {t.get("name", ""): t for t in notetype.get("tmpls", [])}

    applied: list[str] = []
    for name, template in current_templates.items():
        stored_template = stored_templates.get(name)
        if stored_template is None:
            continue
        template["qfmt"] = stored_template.get("qfmt", template["qfmt"])
        template["afmt"] = stored_template.get("afmt", template["afmt"])
        applied.append(name)
    notetype["css"] = stored.get("css", notetype.get("css", ""))

    undo_pos = col.add_custom_undo_entry(undo_name)
    col.models.update_dict(notetype)
    changes = col.merge_undo_entries(undo_pos)
    return NotetypeRestoreResult(
        changes=changes,
        applied_templates=tuple(applied),
        missing_in_current=tuple(n for n in stored_templates if n not in current_templates),
        missing_in_stored=tuple(n for n in current_templates if n not in stored_templates),
    )
