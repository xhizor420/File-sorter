"""Turns a BeatResult into the four files DaVinci Resolve can pull beat
markers from. Pure logic - no widgets, no dependency on torch/beat-this
itself, just plain data in and files out - so all four generators can be
exercised with a hand-built BeatResult and no real model call.

Confidence levels differ across the four formats, and that's worth being
upfront about rather than presenting all four as equally solid:

  - generate_resolve_script(): grounded in DaVinci's documented scripting
    API (Timeline:AddMarker(frameId, color, name, note, duration), frameId
    relative to timeline start). This one should just work.
  - generate_csv(): our own plain data format, no import-format risk at all.
  - generate_xml_timeline() / generate_edl(): best-current-understanding of
    what DaVinci's XML-timeline and EDL-marker importers expect (FCP7
    'xmeml' sequence-level <marker> elements; CMX3600 '* LOC:' locator
    comments), not verified against a real Resolve import from this
    environment. Do one manual round-trip in Resolve - place a few markers
    by hand, export to XML and EDL, diff against what these generators
    produce - before relying on either in a real edit.
"""

from __future__ import annotations

import os

BEAT_COLOR = "Yellow"
DOWNBEAT_COLOR = "Red"


def _events(result, marker_set: str = "both") -> list:
    """(time_seconds, label, color) tuples, one per beat time - never one
    entry per beat AND a separate one per downbeat, since every downbeat is
    also a beat (the first of its bar): a naive concat of both lists would
    double up a marker at the exact same instant for every single downbeat,
    not just an occasional collision.

    marker_set="downbeats": downbeats only, all labeled/colored as such.
    marker_set="beats": every beat, undifferentiated (downbeats included,
    but not called out specially).
    marker_set="both" (default): every beat, with the ones that are also
    downbeats relabeled/recolored instead of appearing twice.
    """
    if marker_set == "downbeats":
        return [(t, f"Downbeat {i}", DOWNBEAT_COLOR)
                for i, t in enumerate(result.downbeats, start=1)]

    downbeat_times = {round(d, 3) for d in result.downbeats}
    events = []
    beat_i = 0
    down_i = 0
    for t in result.beats:
        beat_i += 1
        if marker_set == "both" and round(t, 3) in downbeat_times:
            down_i += 1
            events.append((t, f"Downbeat {down_i}", DOWNBEAT_COLOR))
        else:
            events.append((t, f"Beat {beat_i}", BEAT_COLOR))
    return events


def seconds_to_timecode(seconds: float, fps: float) -> str:
    """Non-drop-frame HH:MM:SS:FF. Drop-frame (29.97/59.94 DF) isn't
    implemented - this kind of beat-cut work is normally done at whole
    frame rates (23.976/24/25/30/50/60) where NDF is standard; add DF math
    later only if a real project actually needs it."""
    fps_i = max(int(round(fps)), 1)
    total_frames = max(int(round(max(seconds, 0.0) * fps_i)), 0)
    frames = total_frames % fps_i
    total_seconds = total_frames // fps_i
    secs = total_seconds % 60
    total_minutes = total_seconds // 60
    mins = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{mins:02d}:{secs:02d}:{frames:02d}"


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


# ── (a) DaVinci Resolve Python script ───────────────────────────────────

def generate_resolve_script(events: list, fps: float, clip_name: str,
                             dest_path: str) -> None:
    """A standalone .py, run via Resolve's Workspace > Scripts - the
    "safest" of the four exports since it references no external media
    file, so there's nothing that can go missing/relink. Deliberately does
    NOT clear existing markers first: it only adds, and reports how many
    AddMarker calls failed (frame collisions) rather than silently wiping
    whatever was already on the timeline."""
    lines = [
        '"""Adds beat/downbeat markers to the CURRENT timeline in DaVinci',
        'Resolve.',
        "",
        f"Generated for: {clip_name}",
        "",
        "Run from Resolve: Workspace > Scripts > (this file). Markers are",
        "added at frame offsets relative to the TIMELINE's start (DaVinci's",
        "own Timeline:AddMarker API works this way, not relative to any",
        "particular clip) - place the clip at the head of a new timeline",
        "before running this.",
        "",
        "Existing markers are left alone. A frame that already has a",
        "marker will show up as \"failed\" in the printed summary rather",
        "than being silently overwritten.",
        '"""',
        "",
        "import DaVinciResolveScript as dvr",
        "",
        f"FPS = {fps!r}",
        "MARKERS = [",
    ]
    for t, label, color in events:
        frame = int(round(t * fps))
        note = f"{t:.3f}s"
        lines.append(f"    ({frame}, {color!r}, {label!r}, {note!r}),")
    lines += [
        "]",
        "",
        "",
        "def main():",
        '    resolve = dvr.scriptapp("Resolve")',
        "    if resolve is None:",
        '        print("Could not connect to Resolve - run this from '
        'Workspace > Scripts.")',
        "        return",
        "    project = resolve.GetProjectManager().GetCurrentProject()",
        "    timeline = project.GetCurrentTimeline() if project else None",
        "    if timeline is None:",
        '        print("No current timeline - open one first.")',
        "        return",
        "    added, failed = 0, 0",
        "    for frame, color, name, note in MARKERS:",
        "        if timeline.AddMarker(frame, color, name, note, 1):",
        "            added += 1",
        "        else:",
        "            failed += 1",
        '    print(f"Added {added} markers ({failed} failed - likely '
        'frame collisions with existing markers).")',
        "",
        "",
        'if __name__ == "__main__":',
        "    main()",
    ]
    with open(dest_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ── (b) FCP7 XML timeline, sequence-level markers ───────────────────────

def generate_xml_timeline(events: list, fps: float, clip_name: str,
                           duration: float, dest_path: str) -> None:
    """A beatgrid timeline: nothing but markers, no media to relink. See
    module docstring for the confidence caveat on this format."""
    fps_i = max(int(round(fps)), 1)
    total_frames = max(int(round(duration * fps_i)), 1)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE xmeml>",
        '<xmeml version="4">',
        "  <sequence>",
        f"    <name>{_xml_escape(clip_name)} beats</name>",
        f"    <duration>{total_frames}</duration>",
        "    <rate>",
        f"      <timebase>{fps_i}</timebase>",
        "      <ntsc>FALSE</ntsc>",
        "    </rate>",
        "    <media>",
        "      <video></video>",
        "      <audio></audio>",
        "    </media>",
    ]
    for t, label, _color in events:
        frame = int(round(t * fps_i))
        lines += [
            "    <marker>",
            f"      <name>{_xml_escape(label)}</name>",
            f"      <in>{frame}</in>",
            f"      <out>{frame}</out>",
            "      <comment></comment>",
            "    </marker>",
        ]
    lines += ["  </sequence>", "</xmeml>"]
    with open(dest_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ── (c) CMX3600 EDL with LOC locator comments ───────────────────────────

def generate_edl(events: list, fps: float, clip_name: str, dest_path: str) -> None:
    """A dummy 1-frame black-slug event per marker, each followed by a
    '* LOC:' locator comment carrying the real timecode/color/name - the
    same convention Avid locators use, which Resolve's "Timeline Markers
    from EDL" import is understood to read. See module docstring for the
    confidence caveat on this format."""
    zero_tc = seconds_to_timecode(0.0, fps)
    one_frame_tc = seconds_to_timecode(1.0 / max(fps, 1.0), fps)
    lines = [f"TITLE: {clip_name} beats", "FCM: NON-DROP FRAME", ""]
    for i, (t, label, color) in enumerate(events, start=1):
        tc = seconds_to_timecode(t, fps)
        lines.append(
            f"{i:03d}  BL       V     C        "
            f"{zero_tc} {one_frame_tc} {zero_tc} {one_frame_tc}")
        lines.append(f"* LOC: {tc} {color.upper()}  {label}")
    with open(dest_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ── (d) plain CSV ────────────────────────────────────────────────────────

def generate_csv(events: list, fps: float, dest_path: str) -> None:
    """Our own data, no import-format risk: index, type, seconds,
    timecode."""
    lines = ["index,type,seconds,timecode"]
    for i, (t, label, _color) in enumerate(events, start=1):
        kind = "downbeat" if label.lower().startswith("downbeat") else "beat"
        lines.append(f"{i},{kind},{t:.3f},{seconds_to_timecode(t, fps)}")
    with open(dest_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def export_all(result, dest_dir: str, clip_name: str, fps: float,
                marker_set: str = "both") -> dict:
    """Writes all four files into dest_dir (created if missing). Returns
    {"script": path, "xml": path, "edl": path, "csv": path}."""
    os.makedirs(dest_dir, exist_ok=True)
    events = _events(result, marker_set)
    paths = {
        "script": os.path.join(dest_dir, "resolve_script.py"),
        "xml": os.path.join(dest_dir, "beatgrid_timeline.xml"),
        "edl": os.path.join(dest_dir, "beat_markers.edl"),
        "csv": os.path.join(dest_dir, "beats.csv"),
    }
    generate_resolve_script(events, fps, clip_name, paths["script"])
    generate_xml_timeline(events, fps, clip_name, result.duration, paths["xml"])
    generate_edl(events, fps, clip_name, paths["edl"])
    generate_csv(events, fps, paths["csv"])
    return paths
