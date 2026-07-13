"""Tiny dict-based i18n. English is the authoritative fallback."""

from __future__ import annotations

from . import strings

_FALLBACK = "en"
_current_lang = _FALLBACK


def resolve_language(override: str, anki_lang: str) -> str:
    """Pick the UI language: explicit config override wins, else Anki's
    language if we have strings for it, else English."""
    if override and override != "auto":
        candidate = override
    else:
        candidate = (anki_lang or "").replace("_", "-").split("-")[0].lower()
    return candidate if candidate in strings.STRINGS else _FALLBACK


def set_language(lang: str) -> None:
    global _current_lang
    _current_lang = lang if lang in strings.STRINGS else _FALLBACK


def current_language() -> str:
    return _current_lang


def tr(key: str, **kwargs: object) -> str:
    """Translate a key; missing keys fall back to English, then to the key
    itself (so a missing string is visible but never crashes)."""
    text = strings.STRINGS.get(_current_lang, {}).get(key)
    if text is None:
        text = strings.STRINGS[_FALLBACK].get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def display_label(op_label: str, origin: str) -> str:
    """Resolve a stored timeline op_label for display (translated at display
    time, so old rows follow a later UI-language change):

    - ``""``          → derive from the row's origin (baseline/manual/…)
    - ``"@sentinel"`` → our own system event, translated now
    - anything else   → Anki's own already-localized undo text, shown as-is
    """
    if not op_label:
        return tr(f"origin_{origin}")
    if op_label.startswith("@"):
        return tr(op_label)
    return op_label
