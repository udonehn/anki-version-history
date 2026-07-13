from __future__ import annotations

from note_version_history.appconfig import AddonConfig, config_from_dict


def test_none_yields_defaults():
    config = config_from_dict(None)
    assert config == AddonConfig()
    assert config.debounce_ms == 1500
    assert config.retention.max_auto_versions_per_note == 100


def test_valid_values_pass_through():
    config = config_from_dict(
        {
            "auto_capture": False,
            "debounce_ms": 3000,
            "heartbeat_scan_minutes": 0,
            "capture_media": False,
            "retention": {"max_auto_versions_per_note": 5, "max_age_days": 0},
            "exclude_notetype_ids": [123, 456],
            "language": "ko",
        }
    )
    assert config.auto_capture is False
    assert config.debounce_ms == 3000
    assert config.heartbeat_scan_minutes == 0
    assert config.capture_media is False
    assert config.retention.max_auto_versions_per_note == 5
    assert config.retention.max_age_days == 0
    assert config.retention.media_max_age_days == 0  # untouched default
    assert config.exclude_notetype_ids == (123, 456)
    assert config.language == "ko"


def test_invalid_types_fall_back_to_defaults():
    config = config_from_dict(
        {
            "auto_capture": "yes",  # not a bool
            "debounce_ms": "fast",  # not an int
            "heartbeat_scan_minutes": True,  # bool is not an int here
            "retention": "aggressive",  # not a dict
            "exclude_notetype_ids": "123",  # not a list
            "language": 42,  # not a str
        }
    )
    assert config == AddonConfig()


def test_out_of_range_values_are_clamped():
    config = config_from_dict(
        {
            "debounce_ms": 5,  # below 100
            "heartbeat_scan_minutes": 10_000,  # above 1440
            "retention": {"max_auto_versions_per_note": 0},  # below 1
        }
    )
    assert config.debounce_ms == 100
    assert config.heartbeat_scan_minutes == 1440
    assert config.retention.max_auto_versions_per_note == 1


def test_exclude_ids_filters_non_ints():
    config = config_from_dict({"exclude_notetype_ids": [1, "2", None, True, 3]})
    assert config.exclude_notetype_ids == (1, 3)
