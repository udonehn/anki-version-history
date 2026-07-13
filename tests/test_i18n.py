from __future__ import annotations

import pytest

from note_version_history import i18n, strings


@pytest.fixture(autouse=True)
def reset_language():
    yield
    i18n.set_language("en")


def test_english_lookup():
    i18n.set_language("en")
    assert i18n.tr("menu_root") == "Note Version History"


def test_korean_lookup():
    i18n.set_language("ko")
    assert i18n.tr("menu_root") == "노트 버전 기록"


def test_missing_key_returns_key():
    i18n.set_language("en")
    assert i18n.tr("no_such_key_xyz") == "no_such_key_xyz"


def test_korean_missing_key_falls_back_to_english():
    strings.STRINGS["ko"].pop("_test_only", None)
    strings.STRINGS["en"]["_test_only"] = "english text"
    try:
        i18n.set_language("ko")
        assert i18n.tr("_test_only") == "english text"
    finally:
        strings.STRINGS["en"].pop("_test_only", None)


def test_format_kwargs():
    i18n.set_language("en")
    text = i18n.tr("db_open_failed", error="disk full")
    assert "disk full" in text


def test_bad_format_args_do_not_crash():
    i18n.set_language("en")
    # about_body needs many kwargs; give none → returns unformatted template
    text = i18n.tr("about_body")
    assert "{notes}" in text


def test_resolve_language():
    assert i18n.resolve_language("auto", "ko") == "ko"
    assert i18n.resolve_language("auto", "ko-KR") == "ko"
    assert i18n.resolve_language("auto", "ja") == "en"  # unsupported → fallback
    assert i18n.resolve_language("en", "ko") == "en"  # explicit override wins
    assert i18n.resolve_language("ko", "en") == "ko"
    assert i18n.resolve_language("de", "ko") == "en"  # unknown override → fallback
    assert i18n.resolve_language("auto", "") == "en"


def test_set_language_rejects_unknown():
    i18n.set_language("xx")
    assert i18n.current_language() == "en"


def test_en_ko_key_parity():
    en = set(strings.STRINGS["en"])
    ko = set(strings.STRINGS["ko"])
    assert en == ko, (
        f"missing in ko: {sorted(en - ko)}; extra in ko: {sorted(ko - en)}"
    )


def test_display_label_resolves_by_kind():
    i18n.set_language("ko")
    # empty → derived from origin
    assert i18n.display_label("", "baseline") == "베이스라인"
    assert i18n.display_label("", "manual") == "스냅샷"
    assert i18n.display_label("", "restore") == "복원"
    # "@" sentinel → translated at display time
    assert i18n.display_label("@delete_note", "auto") == "노트 삭제"
    assert i18n.display_label("@full_rescan", "auto") == "전체 재검사"
    # Anki's own already-localized undo text passes through verbatim
    assert i18n.display_label("메모 업데이트", "auto") == "메모 업데이트"


def test_display_label_follows_language_switch():
    # the SAME stored row renders in whichever language is active now
    i18n.set_language("en")
    assert i18n.display_label("@delete_note", "auto") == "Deleted note"
    i18n.set_language("ko")
    assert i18n.display_label("@delete_note", "auto") == "노트 삭제"
