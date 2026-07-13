"""Per-profile storage locations under the add-on's user_files/.

Profile names may contain any characters (e.g. Korean "사용자 1"), so the
folder key is an ASCII slug plus a stable hash of the exact name.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_SLUG_MAX_LEN = 24


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


def history_db_path(profile_data_dir: Path) -> Path:
    return Path(profile_data_dir) / "history.db"


def blobs_dir(profile_data_dir: Path) -> Path:
    return Path(profile_data_dir) / "blobs"
