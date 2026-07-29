# Version History for Anki — Notes & Note Types

*Read this in other languages: [한국어](README.ko.md)*

Git-like, append-only version history for Anki, all inside the app. Every change
to your **notes** (fields & tags) and **note types** (card templates + CSS) is
captured; browse any item's timeline with per-field diffs and restore any
version.

> History is stored **locally** per profile in the add-on's `user_files/` and is
> **never** written into `collection.anki2`. The history itself does not sync —
> each device keeps its own (changes *arriving* via sync are still captured).

## Screenshots

| Note history & per-field diff | Note type (template/CSS) diff | Tools menu |
| :---: | :---: | :---: |
| ![Note history](assets/1-note.png) | ![Note type diff](assets/2-notetype.png) | ![Tools menu](assets/3-menu.png) |

## Features

- **Automatic capture** — edits are captured as you make them, riding Anki's
  undo system (undo/redo are recorded too, reflog-style).
- **Sync-aware** — changes merged in by AnkiWeb sync (edits made on your other
  devices) are captured as well and labeled *Sync* in the timeline; after a
  full-sync download the add-on rescans automatically to stay consistent.
- **Lazy by default** — no forced first-run baseline. A note's "before" state is
  captured when you open it in the editor, and recorded the first time you
  change the note. On profile open the add-on offers (once) to baseline the
  whole collection — declining just leaves that option in the Tools menu.
- **Per-field diff & restore** — restore a whole version or selected fields.
  Restores are themselves undoable (Ctrl+Z) and recorded (append-only history).
- **Advanced timelines** — search/filter 100-row pages, compare any two
  versions as A→B (even across pages), and compare fields/tags or
  templates/CSS by name.
- **Named, pinned snapshots** — name snapshots and pin important versions.
  Pinned automatic versions are protected from pruning; manual snapshots
  remain permanent after unpinning.
- **Note types** — colored diffs of card templates and CSS; restore templates +
  CSS **without** touching the field schema (so no forced full sync).
- **Stable profile storage** — history follows profile renames. *Reconnect
  Previous Profile History…* reconnects an orphaned older DB without merging
  or deleting directories.
- **Retention & maintenance** — configurable pruning and a compact command to
  reclaim space.
- **English / 한국어** UI (follows Anki's language).

> **Planned:** media-file version history is implemented but disabled in this
> release; it will be enabled in a future update.

## Requirements

- Anki **23.10+** (Qt6). Developed and tested on Anki 26.5.

## Installation

In Anki: **Tools → Add-ons → Get Add-ons…**, then paste the code
**`1237174160`** ([AnkiWeb page](https://ankiweb.net/shared/info/1237174160)).

## Usage

- **Note history** — in the Browser, right-click a card → **🕘 Version
  History**, press **Ctrl+Alt+H**, or use the **🕘** button in the editor
  toolbar.
- **Note type history** — Tools → *Note Version History* → **Note Type
  History…**, or the **🕘** button inside the card-type editor.
- **Full baseline** (optional, for complete coverage) — Tools → *Note Version
  History* → **Baseline Entire Collection…**.
- Search version names, operations, origins, and tags; optionally include note
  fields or note-type templates/CSS. Filters cover automatic, sync, snapshot,
  restore, baseline, deletion, and pinned rows.
- Select *Compare specified versions (A→B)* to reveal **Set A**, **Set B**,
  swap, and clear controls for an arbitrary comparison across pages and
  filters. Other comparison modes ignore the retained A/B endpoints. Browser
  bulk snapshots apply one name/pin choice to all selected notes in a
  background, chunked job.

## How it works

Anki exposes no "before an edit" hook, so the add-on caches a note's state when
it loads in the editor and records that as the baseline the first time the note
actually changes. Everything lives in a schema-v2 SQLite database under the
add-on's `user_files/`. Notes are identified by Anki GUID, and an add-on storage
key keeps the DB attached through profile renames. The collection is only ever
read directly, and restores use Anki's public, undoable APIs.

## Development

```bash
python -m venv .venv
# Windows
.venv/Scripts/python -m pip install anki pytest pytest-cov ruff
.venv/Scripts/python -m pytest                      # headless tests (real anki pylib)
.venv/Scripts/python -m ruff check src tests build.py
.venv/Scripts/python build.py                       # -> dist/*.ankiaddon
```

Link the package into Anki for live testing (Windows, run as the user Anki runs
as):

```
cmd /c mklink /J "%APPDATA%\Anki2\addons21\note_version_history" "<repo>\src\note_version_history"
```

Only `__init__.py`, `scheduler.py`, and `ui/` import `aqt`; everything else is
headless and unit-tested.

## License

[AGPL-3.0](LICENSE) © 2026 udonehn. Anki's `anki`/`aqt` packages are AGPL-3.0 and
this add-on links against them.
