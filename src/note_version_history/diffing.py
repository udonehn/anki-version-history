"""Semantic diffs for field and template content.

Produces colorless spans — the UI layer supplies theme styles. HTML rendering
escapes everything: fields contain arbitrary HTML which must never execute or
load resources inside the diff view.
"""

from __future__ import annotations

import difflib
import html
import re
from dataclasses import dataclass

# Above this size, word-level diffing gets quadratic-slow; fall back to lines.
LARGE_CONTENT_CHARS = 50_000
# SequenceMatcher cost scales with TOKEN count, and autojunk is off (it would
# mis-drop legitimate repeated words) — so dense token soup below the char cap
# (e.g. tens of thousands of 1-char tokens) must also fall back to lines.
LARGE_TOKEN_COUNT = 6_000

EQUAL = "equal"
INSERT = "insert"
DELETE = "delete"

# Words, whitespace runs, and HTML-ish tags as atomic tokens keeps diffs
# readable inside field markup.
_TOKEN_RE = re.compile(r"\s+|<[^<>]{0,200}?>|[^\s<]+")


@dataclass(frozen=True)
class DiffSpan:
    kind: str  # EQUAL | INSERT | DELETE
    text: str


def word_diff(old: str, new: str) -> list[DiffSpan]:
    """Token-level diff spans from old → new (falls back to line-level for
    very large or very token-dense content — see :func:`_tokenize`)."""
    old_tokens, new_tokens = _tokenize(old, new)
    matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    spans: list[DiffSpan] = []
    for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if tag == "equal":
            _append(spans, EQUAL, "".join(old_tokens[a_start:a_end]))
        elif tag == "insert":
            _append(spans, INSERT, "".join(new_tokens[b_start:b_end]))
        elif tag == "delete":
            _append(spans, DELETE, "".join(old_tokens[a_start:a_end]))
        else:  # replace
            _append(spans, DELETE, "".join(old_tokens[a_start:a_end]))
            _append(spans, INSERT, "".join(new_tokens[b_start:b_end]))
    return spans


def spans_to_html(
    spans: list[DiffSpan],
    *,
    insert_style: str = "background-color:#d0f0d0;",
    delete_style: str = "background-color:#f0d0d0;",
) -> str:
    """Render spans as fully-escaped HTML. The input text is treated as plain
    text: tags/scripts inside fields become visible characters, never markup."""
    parts: list[str] = []
    for span in spans:
        text = html.escape(span.text).replace("\n", "<br>")
        if span.kind == EQUAL:
            parts.append(text)
        elif span.kind == INSERT:
            parts.append(f'<span style="{insert_style}">{text}</span>')
        else:
            parts.append(
                f'<span style="{delete_style}text-decoration:line-through;">{text}</span>'
            )
    return "".join(parts)


def plain_to_html(text: str, *, monospace: bool = False) -> str:
    """Render content as fully-escaped HTML with no diff highlighting — for
    the "view this version only, no comparison" mode. Field HTML/scripts
    become visible characters, never markup."""
    escaped = html.escape(text)
    if monospace:
        return (
            '<div style="font-family:monospace;white-space:pre-wrap;">'
            f"{escaped or '&nbsp;'}</div>"
        )
    return escaped.replace("\n", "<br>")


def unified_text_diff(old: str, new: str, from_label: str, to_label: str) -> str:
    """Classic unified diff (plain text) for templates/CSS views."""
    lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=from_label,
        tofile=to_label,
    )
    return "".join(lines)


def unified_to_html(
    diff_text: str,
    *,
    insert_style: str = "background-color:#d0f0d0;",
    delete_style: str = "background-color:#f0d0d0;",
    context_style: str = "color:#888888;",
) -> str:
    """Render a unified diff as fully-escaped monospace HTML (+/- colored)."""
    rendered: list[str] = []
    for line in diff_text.splitlines():
        escaped = html.escape(line) or "&nbsp;"
        if line.startswith(("+++", "---", "@@")):
            rendered.append(f'<span style="{context_style}">{escaped}</span>')
        elif line.startswith("+"):
            rendered.append(f'<span style="{insert_style}">{escaped}</span>')
        elif line.startswith("-"):
            rendered.append(f'<span style="{delete_style}">{escaped}</span>')
        else:
            rendered.append(escaped)
    body = "<br>".join(rendered)
    return f'<div style="font-family:monospace;white-space:pre;">{body}</div>'


def _tokenize(old: str, new: str) -> tuple[list[str], list[str]]:
    """Pick the diff granularity: word tokens normally, whole lines when the
    content is too big (chars) or too dense (token count) for the quadratic
    matcher to stay interactive on the UI thread."""
    if max(len(old), len(new)) > LARGE_CONTENT_CHARS:
        return old.splitlines(keepends=True), new.splitlines(keepends=True)
    old_tokens = _TOKEN_RE.findall(old)
    new_tokens = _TOKEN_RE.findall(new)
    if max(len(old_tokens), len(new_tokens)) > LARGE_TOKEN_COUNT:
        return old.splitlines(keepends=True), new.splitlines(keepends=True)
    return old_tokens, new_tokens


def _append(spans: list[DiffSpan], kind: str, text: str) -> None:
    if not text:
        return
    if spans and spans[-1].kind == kind:
        spans[-1] = DiffSpan(kind, spans[-1].text + text)
    else:
        spans.append(DiffSpan(kind, text))
