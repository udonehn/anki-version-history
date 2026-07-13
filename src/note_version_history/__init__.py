"""Version History — Notes, Note Types & Media.

Git-like, append-only version history for Anki notes, note types
(templates/CSS) and media, with in-app timeline, diff and restore.

This entry point only wires hooks; everything heavy loads lazily. The
package also imports cleanly outside Anki (headless tests import submodules
directly — aqt is absent there, so setup is skipped).
"""

from __future__ import annotations


def _setup_in_anki() -> bool:
    try:
        from aqt import mw
    except ImportError:
        return False  # headless context (tests); nothing to wire
    if mw is None:
        return False
    from . import scheduler
    from .ui import menus

    # Localize BEFORE building the Tools menu — Anki has already set its
    # language by the time add-ons load, but our i18n default is still English.
    scheduler.apply_language()
    scheduler.setup()
    menus.setup()
    return True


_setup_in_anki()
