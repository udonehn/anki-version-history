from __future__ import annotations

import re

from note_version_history import profiles


def test_korean_profile_name_maps_to_safe_stable_key():
    key = profiles.profile_key("사용자 1")
    assert key.startswith("p_")
    assert re.fullmatch(r"[a-z0-9_]+", key)
    assert key == profiles.profile_key("사용자 1")  # stable


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
