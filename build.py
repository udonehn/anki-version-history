"""Build dist/note_version_history-<version>.ankiaddon.

AnkiWeb rules: the zip must contain the package *contents* at its root (no
top-level folder) and must not contain __pycache__. meta.json (written by
Anki when the addon is installed/linked locally) is excluded; user_files/
ships only its README placeholder.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGE_DIR = ROOT / "src" / "note_version_history"
DIST_DIR = ROOT / "dist"

_EXCLUDED_DIR_NAMES = {"__pycache__"}
_EXCLUDED_FILE_NAMES = {"meta.json"}
_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def _include(path: Path) -> bool:
    rel = path.relative_to(PACKAGE_DIR)
    parts = rel.parts
    if any(part in _EXCLUDED_DIR_NAMES for part in parts):
        return False
    if path.name in _EXCLUDED_FILE_NAMES or path.suffix in _EXCLUDED_SUFFIXES:
        return False
    # user_files/: ship only the placeholder README, never user data
    if parts[0] == "user_files" and rel != Path("user_files/README.txt"):
        return False
    return True


def build() -> Path:
    manifest = json.loads((PACKAGE_DIR / "manifest.json").read_text("utf-8"))
    version = manifest.get("human_version", "0.0.0")
    DIST_DIR.mkdir(exist_ok=True)
    out_path = DIST_DIR / f"note_version_history-{version}.ankiaddon"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PACKAGE_DIR.rglob("*")):
            if path.is_file() and _include(path):
                zf.write(path, path.relative_to(PACKAGE_DIR).as_posix())
    return out_path


if __name__ == "__main__":
    built = build()
    print(f"built: {built}")
