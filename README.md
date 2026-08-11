# PAZ Suite

Personal video pipeline: batch-convert clips to 4K/60, browse and search
the library, track what's already used in a project, cut to the beat.
Four tabs, one app.

- **Convert** — watches a source folder, encodes to MP4 (GPU with CPU
  fallback), sorts output by resolution/fps into a 4K 60+ pool vs. a
  needs-work folder.
- **Library** — browse/search everything by artist/character/species/
  rating/tag, play clips (defaults to the 4K/60 copy when one exists),
  fix missing metadata, verify integrity.
- **Vault** — paste a list of post IDs or filenames, find them in the
  library, mark the ones used in a named project so they're visibly
  flagged later instead of getting mixed back into the unused pool.
- **Beats** — pick a clip, run beat-this (a real trained beat/downbeat
  model, not a heuristic) against it, export DaVinci Resolve markers.
  Optional: needs `torch` + `beat-this` installed (see below) - every
  other tab works identically without them.

## Setup

Python 3.9+, plus `ffmpeg`/`ffprobe`/`ffplay` on PATH.

```
pip install -r requirements.txt
python main.py
```

The Beats tab needs its own heavier, optional install - skip this if you
don't need it:

```
pip install -r requirements-beats.txt
```

First launch: **Settings → Convert Folders** to point Source / Converted /
4K 60+ / Needs work at your real folders. Library indexes Convert's
"Converted" folder by default — change that under **Settings → Library**
if it lives elsewhere.

## Search syntax (Library)

`artist:` `character:` `species:` `rating:` `folder:` `id:` `is:` `used:` as
prefixes, `-term` to exclude, `*` wildcards. `is:untagged`, `is:noid`,
`is:4k`/`is:no4k`, `is:portrait`/`is:widescreen`/`is:square`, and
`used:"project name"` (quoted for names with spaces) are the useful
specials. In-app Help (the tab's Help button) covers the rest.

## Layout

```
main.py                     entry point
paz_suite/
  config.py                 AppConfig (+ legacy migration)
  theme.py, format.py       palette/fonts, human-readable formatting
  files.py                  proxy-folder filtering, post-ID parsing, open/reveal
  e621.py                   e621 tag lookup + cache
  media.py                  ffprobe, thumbnailing, frame/storyboard cache, dhash
  player_engine.py          shared ffmpeg-decode + ffplay-audio playback engine
  widgets.py                Card/Bar/StatTile/PeekWindow/Toaster/JobPanel/LogView
  convert_engine.py         encode planning + ffmpeg command/run/verify (no UI)
  convert_widgets.py        queue table, scrub/play preview, contact sheet, dupe finder
  convert_tab.py            the Convert tab
  library_db.py             SQLite schema, search parser, Vault mark helpers (no UI)
  library_player.py         the embedded clip player
  library_windows.py        hidden tags, help, folders, integrity verifier
  library_tab.py            the Library tab
  vault_tab.py              the Vault tab
  beats_engine.py           optional torch/beat-this import, audio extraction, analysis (no UI)
  beats_export.py           DaVinci Resolve marker export: script/XML/EDL/CSV (no UI)
  beats_tab.py              the Beats tab
  settings_window.py        the settings dialog
  app.py                    window shell, tab switcher, keyboard dispatch
```

## Note

Developed without a display/Tk available, so changes are validated with
`py_compile`/`pyflakes` and standalone logic tests, not by actually
running the GUI. Smoke-test after pulling changes.

The Beats tab specifically: `torch`/`beat-this` aren't installed in the
dev environment either, so real model inference has never been run, and
the DaVinci Resolve XML/EDL export formats are a best-understanding
implementation, not verified against an actual Resolve import. Do one
manual round-trip in Resolve (place a few markers by hand, export to XML
and EDL, diff against what the Beats tab produces) before relying on
either for real editing.
