"""Canonical content hashes used for version dedupe and the blob store."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024


def _sha1_of_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def note_hash(mid: int, fields: Sequence[str], tags: Iterable[str]) -> str:
    """Canonical hash of a note's restorable content.

    Tags are sorted: tag order is not meaningful in Anki, so a pure reorder
    must not produce a new version.
    """
    canonical = json.dumps(
        [int(mid), list(fields), sorted(tags)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _sha1_of_text(canonical)


def notetype_hash(name: str, templates: Sequence[tuple[str, str, str]], css: str) -> str:
    """Hash of a note type's restorable surface only: name, template
    (name, qfmt, afmt) triples, and CSS. Non-template changes (sort field,
    LaTeX prefs, field tweaks) intentionally do not change this hash."""
    canonical = json.dumps(
        [name, [list(t) for t in templates], css],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _sha1_of_text(canonical)


def notetype_hash_from_dict(notetype: dict) -> str:
    templates = [
        (t.get("name", ""), t.get("qfmt", ""), t.get("afmt", ""))
        for t in notetype.get("tmpls", [])
    ]
    return notetype_hash(notetype.get("name", ""), templates, notetype.get("css", ""))


def data_sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def file_sha1(path: Path | str) -> str:
    """Streamed sha1 of a file (media files can be large)."""
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
