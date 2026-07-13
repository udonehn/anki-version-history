from __future__ import annotations

import hashlib

from note_version_history import hashing


def test_note_hash_deterministic():
    a = hashing.note_hash(1, ["front", "back"], ["tag1", "tag2"])
    b = hashing.note_hash(1, ["front", "back"], ["tag1", "tag2"])
    assert a == b
    assert len(a) == 40


def test_note_hash_changes_on_field_change():
    base = hashing.note_hash(1, ["front", "back"], [])
    assert hashing.note_hash(1, ["front!", "back"], []) != base
    assert hashing.note_hash(2, ["front", "back"], []) != base  # mid matters


def test_note_hash_ignores_tag_order():
    a = hashing.note_hash(1, ["f"], ["zebra", "apple"])
    b = hashing.note_hash(1, ["f"], ["apple", "zebra"])
    assert a == b
    assert hashing.note_hash(1, ["f"], ["apple"]) != a


def test_note_hash_unicode_fields():
    a = hashing.note_hash(1, ["한국어 필드 🙂"], ["태그"])
    b = hashing.note_hash(1, ["한국어 필드 🙂"], ["태그"])
    assert a == b


def _notetype_dict(**overrides) -> dict:
    base = {
        "name": "Basic",
        "css": ".card { color: black; }",
        "tmpls": [
            {"name": "Card 1", "qfmt": "{{Front}}", "afmt": "{{Back}}", "ord": 0, "did": None}
        ],
        "flds": [{"name": "Front", "ord": 0}, {"name": "Back", "ord": 1}],
        "sortf": 0,
        "mod": 123,
    }
    base.update(overrides)
    return base


def test_notetype_hash_ignores_non_template_changes():
    a = hashing.notetype_hash_from_dict(_notetype_dict())
    b = hashing.notetype_hash_from_dict(
        _notetype_dict(sortf=1, mod=999, flds=[{"name": "Front", "ord": 0}])
    )
    assert a == b


def test_notetype_hash_tracks_restorable_surface():
    base = hashing.notetype_hash_from_dict(_notetype_dict())
    assert hashing.notetype_hash_from_dict(_notetype_dict(css=".card{}")) != base
    assert hashing.notetype_hash_from_dict(_notetype_dict(name="Basic 2")) != base

    changed_tmpl = _notetype_dict()
    changed_tmpl["tmpls"][0]["qfmt"] = "{{Front}}!"
    assert hashing.notetype_hash_from_dict(changed_tmpl) != base


def test_file_and_data_sha1_agree(tmp_path):
    payload = b"binary \x00 payload" * 1000
    expected = hashlib.sha1(payload).hexdigest()

    assert hashing.data_sha1(payload) == expected

    file_path = tmp_path / "sample.bin"
    file_path.write_bytes(payload)
    assert hashing.file_sha1(file_path) == expected
