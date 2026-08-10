# PAZ Suite

One application, two tabs: **Convert** (batch-encode a source video library
to MP4, GPU with CPU fallback, then sort by resolution/frame rate) and
**Library** (an e621-style browser and search engine over the converted
result, with an embedded player). Previously these were two standalone
scripts (`paz_studio.py`, `paz_den.py`) that duplicated a large amount of
infrastructure between them; this is that same functionality combined into
one maintainable codebase.

## What changed from the two standalone scripts

- **One config file** (`~/.video_tool/paz_config.json`) instead of two. On
  first run, if you're upgrading from the old two-app setup, your existing
  `config.json` and `den_config.json` are migrated automatically.
- **One SQLite database, one e621 tag cache, one thumbnail/frame cache**,
  shared by both tabs — a clip probed while converting is already available
  to the Library tab's sync, and vice versa.
- **One settings dialog** instead of two, with a tab per concern (Convert
  Folders, Encoding, Sorting, Library, e621 & App) instead of overlapping
  fields living in different windows.
- **One window** with a Convert/Library tab switcher instead of two
  separate app windows; one Discreet-mode toggle and one F12 hide affect
  both tabs at once.
- Roughly 1,500 lines of duplicated theme/e621-client/proxy-folder-
  filtering/probing/thumbnailing/hover-preview code that used to be
  copy-pasted between the two scripts now lives once, in `paz_suite/`.
- The code is split into focused modules (`convert_engine.py` for the pure
  encoding logic, `library_db.py` for the search/index logic, etc.) instead
  of two 5,000-line single files.

All the original features are preserved: GPU/CPU encoding with automatic
fallback, frame-rate snapping and CFR, watch mode, duplicate finder,
upscale-gap finder, promote-to-pool, e621 tag lookup and caching, the
canvas gallery with hover-scrub, the inline player with synced audio,
picks/pick-sets, Beat Markers for DaVinci Resolve, discreet mode, and the
contact sheet / scrub-preview inspector.

## Setup

```
pip install -r requirements.txt
```

You also need `ffmpeg` and `ffprobe` on your PATH (`ffplay` too, for audio
in the Library tab's embedded player). Beat Markers additionally needs
`paz_beats.py` (not included here) plus `numpy` — the app tells you so, and
still works normally without it.

## Run

```
python main.py
```

On first launch, open **Settings → Convert Folders** and point Source /
Converted / 4K 60+ / Needs work / Reports at your real folders — nothing is
guessed or hardcoded to a particular drive letter. The Library tab indexes
the Convert tab's "Converted" folder by default; change that under
**Settings → Library** if your library lives somewhere else.

## Layout

```
main.py                     entry point
paz_suite/
  config.py                 unified AppConfig (+ legacy migration)
  theme.py, format.py       palette/fonts/paw-art, human-readable formatting
  files.py                  proxy-folder filtering, post-ID parsing, open/reveal
  e621.py                   e621 tag lookup + cache (shared by both tabs)
  media.py                  ffprobe, thumbnailing, frame cache, perceptual hash
  widgets.py                Card/Bar/StatTile/PeekWindow/Toaster/JobPanel/LogView
  convert_engine.py         encode planning + ffmpeg command/run/verify (no UI)
  convert_widgets.py        queue table, scrub preview, contact sheet, dupe finder
  convert_tab.py            the Convert tab
  library_db.py             SQLite schema, search parser (no UI)
  library_player.py         the embedded clip player
  library_windows.py        Beat Markers, pick sets, hidden tags, help, folders
  library_tab.py            the Library tab
  settings_window.py        the one settings dialog
  app.py                    window shell, tab switcher, shared keyboard dispatch
```

## A note on testing

This was restructured and validated for syntax and logic (`py_compile`,
`pyflakes`, and direct unit checks of the pure-logic modules — formatting,
search-query parsing, encode classification) in an environment without a
display or Tk available, so the GUI itself has not been runtime-tested.
Please do a smoke test on your machine — launch the app, scan/convert a
couple of files, sync the library, play a clip — before relying on it for
real work.
