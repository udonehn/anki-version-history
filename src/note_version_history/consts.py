"""Shared constants for the Version History add-on."""

from __future__ import annotations

ADDON_PACKAGE = "note_version_history"

# Media file version history is fully implemented (capture_media.py, media_dialog,
# media baseline) but DISABLED for now — flip to True to re-enable everything
# (menu items, capture pipeline wiring, baseline step). Kept behind one flag so
# the work can be resumed later without re-implementing.
MEDIA_ENABLED = False

# Version row origins
ORIGIN_BASELINE = "baseline"
ORIGIN_AUTO = "auto"
ORIGIN_MANUAL = "manual"
ORIGIN_RESTORE = "restore"

# Media event kinds
EVENT_ADDED = "added"
EVENT_MODIFIED = "modified"
EVENT_DELETED = "deleted"

# System-generated op_labels are stored as stable "@" sentinels and translated
# at DISPLAY time (so old rows follow a later UI-language change). An empty
# op_label falls back to the row's origin; anything else is Anki's own already-
# localized undo text, shown verbatim. Baseline/manual/restore need no sentinel
# — their origin already carries the meaning.
LABEL_DELETE_NOTE = "@delete_note"
LABEL_UNDO_DELETE = "@undo_delete"
LABEL_FULL_RESCAN = "@full_rescan"
LABEL_DELETE_NOTETYPE = "@delete_notetype"

# meta table keys
META_SCHEMA_VERSION = "schema_version"
META_PROFILE_NAME = "profile_name"
META_BASELINE_STATE = "baseline_state"
META_NOTE_SCAN_MARKER = "note_scan_marker"
META_LAST_NOTE_COUNT = "last_note_count"
META_LAST_UNDO_STATUS = "last_undo_status"
META_LAST_PRUNE_MS = "last_prune_ms"
META_LAST_MEDIA_SCAN_MS = "last_media_scan_ms"
META_CLEAN_SHUTDOWN = "clean_shutdown"
META_COL_MTIME_SEEN = "col_mtime_seen"


class RestoreInitiator:
    """Sentinel passed as CollectionOp ``initiator`` so our own restore
    operations are recognized (and skipped) by the capture hook."""


RESTORE_INITIATOR = RestoreInitiator()
