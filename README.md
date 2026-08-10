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
  separate app windows.
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
  startup check and a smaller surface area.
- **Discreet mode, Picks/pick sets, the S/M/L/XL tile-size selector and the
  external "open in VLC" buttons are gone.** They were all tied to the
  Beat-Markers-era PMV workflow (Picks in particular existed to collect
  clips before exporting a marker playlist, which no longer exists). Both
  tabs now have a real built-in player with audio, so there's no need to
  hand off to an external one; gallery tile width is a plain typed number
  in Settings instead of a preset picker; and every label across the app
  is plain, single, general-purpose wording — there's no alternate phrasing
  to toggle.
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
- **Verify library integrity** (right-click **Fix missing**): a full
  ffmpeg decode pass over every clip, catching corrupt or truncated files
  that probing can't see (probing only reads the container header, so a
  file with a broken frame in the middle still "probes" fine). Runs
  several decodes in parallel in the background, is cancellable, and
  reports anything broken with a show-in-folder/delete action. Nothing is
  touched unless you delete a result yourself.
- Three changes aimed specifically at libraries in the five-figure range
  and growing: the media-probe cache is now a bounded LRU (60,000 entries
  by default) instead of a dict that got wiped entirely every 8,000
  probes; each clip's artist/character/species/copyright/lore names are
  unioned once when the library loads instead of being rebuilt from
  scratch on every search keystroke and every clip you select; and both
  cache sizes are now tunable in **Settings → Library → Performance**
  instead of fixed constants, so they can keep growing as your library
  does.
- **Soft tag refresh, and tag fetching that doesn't wait to be asked.** A
  small batch of due-for-recheck posts (recently-posted clips every few
  days, old settled ones every few months) now runs automatically when
  the Library tab opens and after every sync, so scores/tags stay current
  and new files get tagged without pressing anything. Pressing **Fetch
  e621 tags** yourself is now the deliberate, thorough action instead -
  it catches up every post that's due, not just a small batch. The
  ambient batch size is tunable in `Settings → Library`.
- **Ratio quick filter.** Aspect ratio isn't usually an e621 tag, so it's
  handled separately: `is:portrait`, `is:widescreen` and `is:square` are
  computed directly from each clip's resolution. A single Ratio dropdown
  next to Random applies any of them (or clears back to all ratios) in one
  click — a fast way to pull only phone-shaped, only landscape, or only
  square footage when you're picking clips for an edit with a fixed output
  orientation.
- **Grid on both tabs.** The contact sheet (twelve evenly-spaced frames of
  a clip, click one to jump the player there) used to be Convert-only.
  It's now a button in the Library detail panel too.
- **Hover-preview progress bar.** Hovering a queue row, gallery card or
  the Convert timeline shows a thin bar under the preview frame, filled to
  match how far into the clip that frame is — the same cue YouTube shows
  on hover, instead of just a timecode you had to read. It's a strip of
  its own below the frame rather than an overlay on it, so it stays
  visible no matter what's in the video.
- **60fps, checked plainly.** The "snap 59.94 to 60" toggle and the
  separate fps-tolerance setting are gone; anything from 58.5 fps up to
  (not touching) the target is always resampled to exactly 60 - no
  epsilon-sized gap near the boundary for an odd fps reading to slip
  through uncaught - and the sort check is a plain `fps >= minimum fps`
  after that, one behavior instead of two overlapping, independently-
  configurable ones.
- **Fix missing no longer gets permanently stuck.** Its outstanding count
  used to include DB rows whose file had since been deleted from disk (or
  that fail to probe at all) - Fix missing can't do anything about either
  case, so the badge would sit on the same number forever. Dead files are
  now excluded from the count (Sync is what actually clears them out),
  and files that exist but can't be decoded are called out by name in the
  status line instead of silently re-counted every time.
- **Tag-fetch failures are no longer lumped together.** A post e621
  confirms is gone (cached, never retried) and a transient network error
  (not cached, retried next time) used to both show up as one "unavailable"
  number, so a stuck count gave no clue why. The status line after a
  fetch now says which is which.
- **The sidebar's tag groups collapse.** Artists / Characters / Species /
  Series / Lore / Tags in the left sidebar each have their own ▾/▸ toggle
  now, same as the detail panel on the right already had - collapse the
  categories you don't need to cut through a big tag list faster.
- **The Library player's seek bar previews too.** Hovering it now pops
  the same YouTube-style frame preview the gallery cards and Convert's
  timeline already had; it was the one scrub surface in the app that
  didn't.

Everything else is preserved: GPU/CPU encoding with automatic fallback,
frame-rate snapping and CFR, watch mode, the duplicate finder, the
upscale-gap finder, promote-to-pool, e621 tag lookup and caching, the
canvas gallery with hover-scrub, and the contact sheet / scrub-preview
inspector — the contact sheet ("Grid") is now available on both tabs
instead of Convert-only.

## What it's for

**Library** is the home base for browsing what you already have — search by
artist/character/species/rating/tag, see what's tagged vs. not, what has a
4K/60fps edit-ready copy vs. not (`is:4k` / `is:no4k`), what's missing
metadata entirely (`is:untagged`, `is:noid`, or just press **Fix missing**),
and now whether any file is actually corrupt (**Verify library**, above).
This is the tag-driven browsing step for pulling clips to edit with.

**Convert** is where an external downloader (or you, by hand) drops files
into the source folder; watch mode picks up anything new once its size
stops changing, converts it, sorts it by resolution/frame rate, and
**Find upscale gaps** double-checks the result against the 4K/60+ pool —
so at any point Scan tells you exactly what's still queued vs. already
done, and the gap check tells you what's converted but not yet upscaled.
**Promote upscales** moves anything that now meets the bar into the edit
pool.

## Interface

A slim shared header (suite branding) sits above the Convert/Library tab
strip. The tab strip itself recolours to match whichever tab is active
(pink for Convert, violet for Library) instead of staying one flat,
generic control regardless of which tab is showing, and each tab's own
hero panels (Convert's queue table and controls card; Library's gallery
shell, detail card and tag sidebar) carry a matching accent-tinted
border instead of the same neutral grey everywhere - the two tabs read
as differently-identified places, not one layout wearing two labels.
Each tab's own top area is two rows: a browsing row (brand, search,
rating/sort) and, underneath it, an action toolbar - maintenance actions
(Sync, Fix missing, Fetch tags / Scan, Start) on the left, configuration
(Settings, Help) on the right. The standalone Folders button is gone;
**Settings → Library → Change folders…** and `Ctrl+O` both still reach
it, so it didn't need a permanent slot in an already busy row. In the
Library gallery, the chip row is filters only (Untagged, No post ID, 4K,
Non-4K); Random and a Ratio dropdown (Portrait / Widescreen / Square) sit
next to the pager since they're actions, not ways of narrowing the
results, and "Top rated" is a small button beside the sort dropdown
since it's really a sort shortcut. Gallery tile width is a plain number
in **Settings → Library → Display** instead of a size picker in the
toolbar. Hovering a queue row, gallery card or the Convert inspector's
timeline pops up a floating preview with a thin progress bar under the
frame, YouTube-style, showing exactly how far into the clip that frame
sits. The Convert inspector's player has a volume slider next to the
mute button, same as the Library player, and both save volume/mute on
close.

## Setup

Python 3.9+.

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
  theme.py, format.py       palette/fonts/status copy, human-readable formatting
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
  library_windows.py        hidden tags, help, folders, integrity verifier
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
