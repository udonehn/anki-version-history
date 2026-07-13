# Version History — Configuration

Settings are re-read at each capture/restore decision, so changes apply
immediately (no restart needed).

- **auto_capture** (default `true`): automatically capture a version after
  every undoable change (edits, find & replace, undo/redo, …). Manual
  snapshots keep working when this is off.
- **debounce_ms** (default `1500`): wait this many milliseconds after the last
  change before scanning, so rapid consecutive edits coalesce into one scan.
- **heartbeat_scan_minutes** (default `5`): periodic background scan that
  catches changes arriving without a normal operation (e.g. after sync).
  `0` disables the heartbeat.
- **capture_media** (default `true`): version media files referenced by
  changed notes, plus full media-folder scans.
- **media_scan_on_profile_open** (default `true`): run a full media scan when
  the profile opens.
- **media_scan_on_profile_close** (default `false`): run a full media scan
  when the profile closes.
- **retention.max_auto_versions_per_note** (default `100`): keep at most this
  many automatic versions per note; older ones are pruned. Manual snapshots,
  baseline versions and restore markers are never pruned.
- **retention.max_age_days** (default `180`): automatic versions older than
  this are pruned. `0` disables age-based pruning.
- **retention.media_max_age_days** (default `0`): prune media history older
  than this; `0` keeps media history forever.
- **exclude_notetype_ids** (default `[]`): list of note type IDs whose notes
  are not captured.
- **language** (default `"auto"`): UI language — `"auto"` follows Anki's
  language, or force `"en"` / `"ko"`.
