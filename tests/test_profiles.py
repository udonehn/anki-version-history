from __future__ import annotations

import re

import pytest

from note_version_history import consts, db, profiles


def test_korean_profile_name_maps_to_safe_stable_key():
    key = profiles.profile_key("사용자 1")
    assert key.startswith("p_")
    assert re.fullmatch(r"[a-z0-9_]+", key)
    assert key == profiles.profile_key("사용자 1")  # stable


def test_non_ascii_only_profile_and_storage_key_validation(tmp_path):
    key = profiles.profile_key("사용자")
    assert re.fullmatch(r"p_[0-9a-f]{8}", key)
    generated = profiles.new_storage_key()
    assert profiles.valid_storage_key(generated)
    assert not profiles.valid_storage_key("../unsafe")
    data_dir = profiles.profile_data_dir_for_key(tmp_path, generated)
    assert data_dir.is_dir()
    with pytest.raises(ValueError):
        profiles.profile_data_dir_for_key(tmp_path, "../unsafe")


def test_distinct_names_get_distinct_keys():
    assert profiles.profile_key("사용자 1") != profiles.profile_key("사용자 2")
    assert profiles.profile_key("User") != profiles.profile_key("user ")


def test_ascii_name_keeps_readable_slug():
    key = profiles.profile_key("My Profile!")
    assert "my_profile" in key


def test_long_names_are_truncated_but_unique():
    long_a = "x" * 100 + "a"
    long_b = "x" * 100 + "b"
    key_a = profiles.profile_key(long_a)
    key_b = profiles.profile_key(long_b)
    assert key_a != key_b
    assert len(key_a) <= 2 + 24 + 1 + 8  # p_ + slug + _ + digest


def test_profile_data_dir_and_paths(tmp_path):
    data_dir = profiles.profile_data_dir(tmp_path, "사용자 1")
    assert data_dir.is_dir()
    assert data_dir.parent == tmp_path

    db_path = profiles.history_db_path(data_dir)
    blob_root = profiles.blobs_dir(data_dir)
    assert db_path.name == "history.db"
    assert blob_root.name == "blobs"
    assert db_path.parent == data_dir
    assert blob_root.parent == data_dir


def test_storage_key_survives_rename_and_reuses_legacy_database(tmp_path):
    legacy = profiles.profile_key("Old Name")
    legacy_dir = tmp_path / legacy
    connection = db.open_history_db(profiles.history_db_path(legacy_dir))
    connection.close()

    key, changed = profiles.choose_storage_key(tmp_path, "Old Name", None)
    assert changed and key == legacy
    renamed_key, changed_again = profiles.choose_storage_key(
        tmp_path, "New Name", key
    )
    assert not changed_again and renamed_key == legacy


def test_discover_histories_reports_profile_count_and_latest(tmp_path):
    storage_key = profiles.new_storage_key()
    connection = db.open_history_db(
        profiles.history_db_path(tmp_path / storage_key)
    )
    db.meta_set(connection, consts.META_PROFILE_NAME, "Previous Profile")
    connection.execute(
        "INSERT INTO note_versions"
        " (nid,guid,mid,ts,origin,fields,field_names,tags,hash)"
        " VALUES (1,'g',1,1234,'auto','[]','[]','[]','h')"
    )
    connection.close()

    found = profiles.discover_histories(tmp_path)
    assert found == [
        profiles.HistoryCandidate(storage_key, "Previous Profile", 1, 1234)
    ]


def test_discover_histories_ignores_missing_and_invalid_databases(tmp_path):
    assert profiles.discover_histories(tmp_path / "missing") == []
    invalid = tmp_path / profiles.new_storage_key()
    invalid.mkdir()
    (invalid / "history.db").write_text("not sqlite", encoding="utf-8")
    (tmp_path / "not-a-key").mkdir()
    assert profiles.discover_histories(tmp_path) == []
