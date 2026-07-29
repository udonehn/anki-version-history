"""Per-profile storage locations under the add-on's user_files/.

Profile names may contain any characters (e.g. Korean "사용자 1"), so the
folder key is an ASCII slug plus a stable hash of the exact name.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

_SLUG_MAX_LEN = 24
PROFILE_STORAGE_KEY = "note_version_history_storage_key"


@dataclass(frozen=True)
class HistoryCandidate:
    storage_key: str
    profile_name: str
    row_count: int
    latest_ts: int


def profile_key(profile_name: str) -> str:
    """Stable, filesystem-safe folder name for a profile."""
    slug = re.sub(r"[^a-z0-9]+", "_", profile_name.lower()).strip("_")[:_SLUG_MAX_LEN].strip("_")
    digest = hashlib.sha1(profile_name.encode("utf-8")).hexdigest()[:8]
    if slug:
        return f"p_{slug}_{digest}"
    return f"p_{digest}"


def profile_data_dir(user_files_dir: Path, profile_name: str) -> Path:
    """Create (if needed) and return this profile's data directory."""
    data_dir = Path(user_files_dir) / profile_key(profile_name)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def new_storage_key() -> str:
    return f"p_{uuid.uuid4().hex}"


def valid_storage_key(value: object) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"p_[a-z0-9_]{8,64}", value)
    )


def profile_data_dir_for_key(user_files_dir: Path, storage_key: str) -> Path:
    if not valid_storage_key(storage_key):
        raise ValueError("invalid profile history storage key")
    data_dir = Path(user_files_dir) / storage_key
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def choose_storage_key(
    user_files: Path, profile_name: str, saved_key: object
) -> tuple[str, bool]:
    """Return (key, changed), preserving the legacy name-derived directory."""
    if valid_storage_key(saved_key):
        return str(saved_key), False
    legacy = profile_key(profile_name)
    legacy_db = history_db_path(Path(user_files) / legacy)
    return (legacy if legacy_db.exists() else new_storage_key()), True


def discover_histories(user_files: Path) -> list[HistoryCandidate]:
    """Inspect existing DBs without creating, merging, or deleting directories."""
    candidates: list[HistoryCandidate] = []
    root = Path(user_files)
    if not root.is_dir():
        return candidates
    for entry in root.iterdir():
        path = history_db_path(entry)
        if not entry.is_dir() or not valid_storage_key(entry.name) or not path.is_file():
            continue
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            profile_row = conn.execute(
                "SELECT value FROM meta WHERE key='profile_name'"
            ).fetchone()
            profile_name = str(profile_row[0]) if profile_row is not None else ""
            note_count = int(
                conn.execute("SELECT count(*) FROM note_versions").fetchone()[0]
            )
            notetype_count = int(
                conn.execute("SELECT count(*) FROM notetype_versions").fetchone()[0]
            )
            latest = int(
                conn.execute(
                    "SELECT max(ts) FROM ("
                    " SELECT max(ts) AS ts FROM note_versions"
                    " UNION ALL SELECT max(ts) FROM notetype_versions"
                    ")"
                ).fetchone()[0]
                or 0
            )
            candidates.append(
                HistoryCandidate(
                    storage_key=entry.name,
                    profile_name=profile_name,
                    row_count=note_count + notetype_count,
                    latest_ts=latest,
                )
            )
        except (sqlite3.Error, OSError, ValueError):
            continue
        finally:
            if conn is not None:
                conn.close()
    return sorted(candidates, key=lambda item: item.latest_ts, reverse=True)


def history_db_path(profile_data_dir: Path) -> Path:
    return Path(profile_data_dir) / "history.db"


def blobs_dir(profile_data_dir: Path) -> Path:
    return Path(profile_data_dir) / "blobs"
