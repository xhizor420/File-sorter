# PAZ Suite

One application, two tabs: **Convert** (batch-encode a source video library
to MP4, GPU with CPU fallback, then sort by resolution/frame rate) and
**Library** (a browser and search engine over the converted result, with a
real play/pause/seek/audio player). Previously these were two standalone
scripts (`paz_studio.py`, `paz_den.py`) that duplicated a large amount of
infrastructure between them; this is that same functionality combined into
one maintainable codebase, focused on the two things it's actually for:
telling you what's in your library and what still needs converting.

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
- **One playback engine** (`player_engine.py`) shared by both tabs. The
  Library tab always had real play/pause/seek/volume with audio; the
  Convert tab's inspector used to only scrub and skim still frames. Now
  Convert has a real Play button too (press it, or click the preview),
  built on the exact same ffmpeg-decode + ffplay-audio engine, so both
  tabs can actually watch a clip, not just step through it.
- **Beat Markers is gone.** It was a narrow, PMV-editing-specific feature
  (BPM grids for DaVinci Resolve) that didn't fit a general library/convert
  tool and pulled in an optional `numpy`/`paz_beats.py` dependency for
  something most people never touched. Removing it also means one less
  startup check and a smaller surface area. Everything else — Picks,
  pick sets, the search/tag sidebar — is written in general terms now
  rather than PMV-specific language.
- Roughly 1,500 lines of duplicated theme/e621-client/proxy-folder-
  filtering/probing/thumbnailing/hover-preview code that used to be
  copy-pasted between the two scripts now lives once, in `paz_suite/`.
- The code is split into focused modules (`convert_engine.py` for the pure
  encoding logic, `library_db.py` for the search/index logic, etc.) instead
  of two 5,000-line single files.
- Probing during a folder scan, first-time library indexing, and the
  duplicate finder's initial pass all now run several ffprobe calls in
  parallel (scaled to your CPU count) instead of one file at a time —
  noticeably faster on anything but a tiny library.

Everything else is preserved: GPU/CPU encoding with automatic fallback,
frame-rate snapping and CFR, watch mode, the duplicate finder, the
upscale-gap finder, promote-to-pool, e621 tag lookup and caching, the
canvas gallery with hover-scrub, picks/pick-sets, discreet mode, and the
contact sheet / scrub-preview inspector.

## What it's for

**Library** is the home base for browsing what you already have — search by
artist/character/species/rating/tag, see what's tagged vs. not, what has a
4K/60fps edit-ready copy vs. not (`is:4k` / `is:no4k`), and what's missing
metadata entirely (`is:untagged`, `is:noid`, or just press **Fix missing**).

**Convert** is where you point at a source folder and see exactly what's
queued to convert vs. already done, then Start. **Find upscale gaps**
compares your converted library against the 4K/60+ pool and tells you what
still needs upscaling; **Promote upscales** moves anything that now meets
the bar into the edit pool.

## Setup

```
pip install -r requirements.txt
```

You also need `ffmpeg`, `ffprobe` and `ffplay` on your PATH (all three ship
together with a normal ffmpeg install) — `ffplay` specifically is what
gives both tabs' players audio.

## Run

```
python main.py
```

On first launch, open **Settings → Convert Folders** and point Source /
Converted / 4K 60+ / Needs work / Reports at your real folders — nothing is
guessed or hardcoded to a particular drive letter. The Library tab indexes
the Convert tab's "Converted" folder by default; change that under
**Settings → Library** if your library lives somewhere else.

The window has a sensible floor (1180×700) but every panel — the gallery's
column count, the inspector/player, the queue table — recalculates its own
layout on resize, so it's equally usable maximized on a 4K display or
tiled on a laptop screen.

## Layout

```
main.py                     entry point
paz_suite/
  config.py                 unified AppConfig (+ legacy migration)
  theme.py, format.py       palette/fonts/paw-art, human-readable formatting
  files.py                  proxy-folder filtering, post-ID parsing, open/reveal
  e621.py                   e621 tag lookup + cache (shared by both tabs)
  media.py                  ffprobe, thumbnailing, frame cache, perceptual hash
  player_engine.py          shared ffmpeg-decode + ffplay-audio playback engine
  widgets.py                Card/Bar/StatTile/PeekWindow/Toaster/JobPanel/LogView
  convert_engine.py         encode planning + ffmpeg command/run/verify (no UI)
  convert_widgets.py        queue table, scrub/play preview, contact sheet, dupe finder
  convert_tab.py            the Convert tab
  library_db.py             SQLite schema, search parser (no UI)
  library_player.py         the embedded clip player (thin UI over player_engine)
  library_windows.py        pick sets, hidden tags, help, folders
  library_tab.py            the Library tab
  settings_window.py        the one settings dialog
  app.py                    window shell, tab switcher, shared keyboard dispatch
```

## A note on testing

This was restructured and validated for syntax and logic (`py_compile`,
`pyflakes`, and direct unit checks of the pure-logic modules — formatting,
search-query parsing, encode classification, config migration) in an
environment without a display or Tk available, so the GUI itself has not
been runtime-tested. Please do a smoke test on your machine — launch the
app, scan/convert a couple of files, sync the library, play a clip in both
tabs — before relying on it for real work.
