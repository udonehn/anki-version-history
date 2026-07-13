"""Typed, defensive view over the raw add-on config dict.

The aqt side reads the raw dict via ``mw.addonManager.getConfig`` and passes
it to :func:`config_from_dict`; this module never imports aqt so it stays
headless-testable. Unknown/invalid values fall back to safe defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetentionConfig:
    max_auto_versions_per_note: int = 100
    max_age_days: int = 180
    media_max_age_days: int = 0


@dataclass(frozen=True)
class AddonConfig:
    auto_capture: bool = True
    debounce_ms: int = 1500
    heartbeat_scan_minutes: int = 5
    capture_media: bool = True
    media_scan_on_profile_open: bool = True
    media_scan_on_profile_close: bool = False
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    exclude_notetype_ids: tuple[int, ...] = ()
    language: str = "auto"


def _as_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_int(value: object, default: int, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(lo, min(hi, value))


def _as_id_tuple(value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value if isinstance(v, int) and not isinstance(v, bool))


def config_from_dict(raw: dict | None) -> AddonConfig:
    raw = raw if isinstance(raw, dict) else {}
    defaults = AddonConfig()
    raw_retention = raw.get("retention")
    raw_retention = raw_retention if isinstance(raw_retention, dict) else {}
    retention_defaults = RetentionConfig()
    retention = RetentionConfig(
        max_auto_versions_per_note=_as_int(
            raw_retention.get("max_auto_versions_per_note"),
            retention_defaults.max_auto_versions_per_note,
            1,
            100_000,
        ),
        max_age_days=_as_int(
            raw_retention.get("max_age_days"), retention_defaults.max_age_days, 0, 36_500
        ),
        media_max_age_days=_as_int(
            raw_retention.get("media_max_age_days"),
            retention_defaults.media_max_age_days,
            0,
            36_500,
        ),
    )
    language = raw.get("language")
    if not isinstance(language, str):
        language = defaults.language
    return AddonConfig(
        auto_capture=_as_bool(raw.get("auto_capture"), defaults.auto_capture),
        debounce_ms=_as_int(raw.get("debounce_ms"), defaults.debounce_ms, 100, 60_000),
        heartbeat_scan_minutes=_as_int(
            raw.get("heartbeat_scan_minutes"), defaults.heartbeat_scan_minutes, 0, 1_440
        ),
        capture_media=_as_bool(raw.get("capture_media"), defaults.capture_media),
        media_scan_on_profile_open=_as_bool(
            raw.get("media_scan_on_profile_open"), defaults.media_scan_on_profile_open
        ),
        media_scan_on_profile_close=_as_bool(
            raw.get("media_scan_on_profile_close"), defaults.media_scan_on_profile_close
        ),
        retention=retention,
        exclude_notetype_ids=_as_id_tuple(raw.get("exclude_notetype_ids")),
        language=language,
    )
