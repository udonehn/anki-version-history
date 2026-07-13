from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make src/ importable even when pytest's `pythonpath` ini is unavailable.
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "history.db"


@pytest.fixture
def conn(tmp_db_path: Path):
    from note_version_history import db

    connection = db.open_history_db(tmp_db_path)
    yield connection
    connection.close()
