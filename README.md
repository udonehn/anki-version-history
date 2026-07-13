# Version History for Anki — Notes & Note Types

*Read this in other languages: [한국어](README.ko.md)*

Git-like, append-only version history for Anki, all inside the app. Every change
to your **notes** (fields & tags) and **note types** (card templates + CSS) is
captured; browse any item's timeline with per-field diffs and restore any
version.

> History is stored **locally** per profile in the add-on's `user_files/` and is
> **never** written into `collection.anki2`. It does not sync between devices.

## Screenshots

| Note history & per-field diff | Note type (template/CSS) diff | Tools menu |
| :---: | :---: | :---: |
| ![Note history](assets/1-note.png) | ![Note type diff](assets/2-notetype.png) | ![Tools menu](assets/3-menu.png) |

## Features

- **Automatic capture** — edits are captured as you make them, riding Anki's
  undo system (undo/redo are recorded too, reflog-style).
- **Lazy by default** — no forced first-run baseline. A note's "before" state is
  captured when you open it in the editor, and recorded the first time you
  change the note. You can baseline the whole collection on demand instead.
- **Per-field diff & restore** — restore a whole version or selected fields.
  Restores are themselves undoable (Ctrl+Z) and recorded (append-only history).
- **Note types** — colored diffs of card templates and CSS; restore templates +
  CSS **without** touching the field schema (so no forced full sync).
- **Retention & maintenance** — configurable pruning and a compact command to
  reclaim space.
- **English / 한국어** UI (follows Anki's language).

> **Planned:** media-file version history is implemented but disabled in this
> release; it will be enabled in a future update.

## Requirements

- Anki **23.10+** (Qt6). Developed and tested on Anki 26.5.

## Installation

- **AnkiWeb** (recommended): install code **`1237174160`** — in Anki, Tools →
  Add-ons → Get Add-ons, and paste the code.
- **Manual**: download the `.ankiaddon` from the
  [Releases](https://github.com/udonehn/anki-version-history/releases) page and
  double-click it (or drag it into Anki).

## Usage

- **Note history** — in the Browser, right-click a card → **🕘 Version
  History**, press **Ctrl+Alt+H**, or use the **🕘** button in the editor
  toolbar.
- **Note type history** — Tools → *Note Version History* → **Note Type
  History…**, or the **🕘** button inside the card-type editor.
- **Full baseline** (optional, for complete coverage) — Tools → *Note Version
  History* → **Baseline Entire Collection…**.
- Diff modes in each dialog: *show this version only*, *compare with current*,
  and *compare with previous version*.

## How it works

Anki exposes no "before an edit" hook, so the add-on caches a note's state when
it loads in the editor and records that as the baseline the first time the note
actually changes. Everything lives in a per-profile SQLite database under the
add-on's `user_files/`; the collection is only ever read, and all restores go
through Anki's public, undoable APIs.

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
