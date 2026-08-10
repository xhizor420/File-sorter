"""Shared visual identity: the "Heat" palette, fonts and paw-print branding.

Both tabs render from this one module, so the pair look like one suite
instead of two apps that happen to share a colour scheme.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk


class T:
    BG        = "#0B0711"    # near-black plum
    SURFACE   = "#161021"    # cards
    ELEVATED  = "#1F1631"    # raised cards / menus
    INPUT     = "#0F0917"    # wells: viewer, log, fields
    LINE      = "#2F2247"    # hairlines
    LINE_SOFT = "#1D1430"

    ACCENT      = "#FF3D9E"  # hot pink — Convert identity
    ACCENT_HOV  = "#FF7AC1"
    ACCENT_DEEP = "#441233"
    ACCENT2      = "#9A6BFF"  # violet — Library identity
    ACCENT2_HOV  = "#B48CFF"
    ACCENT2_DEEP = "#241542"

    OK        = "#53E0AE"
    OK_DEEP   = "#0E2B22"
    WARN      = "#FFC24D"
    WARN_DEEP = "#332409"
    FAIL      = "#FF5C6E"
    FAIL_DEEP = "#33101B"

    TEXT  = "#F5EFF9"
    DIM   = "#AB9AC2"
    FAINT = "#6E5C88"

    BTN        = "#231838"
    BTN_HOV    = "#30204D"
    BTN_GO     = "#E01F84"
    BTN_GO_H   = "#FF3D9E"
    BTN_STOP   = "#8A1538"
    BTN_STOP_H = "#B01C49"

    ROW      = "#140E1E"
    ROW_ALT  = "#181126"
    ROW_SEL  = "#41173A"

    CARD_SEL = "#41173A"

    RATING = {"e": "#FF5C6E", "q": "#FFC24D", "s": "#53E0AE"}

    UI   = "Segoe UI"
    MONO = "Cascadia Mono"


def font(size: int = 12, weight: str = "normal", mono: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family=T.MONO if mono else T.UI, size=size, weight=weight)


# (dx, dy, rx, ry) per element in a unit square; pad first, then three toes.
_PAW_PARTS = (
    (0.50, 0.66, 0.30, 0.26),
    (0.22, 0.28, 0.13, 0.16),
    (0.50, 0.18, 0.13, 0.17),
    (0.78, 0.28, 0.13, 0.16),
)


def draw_paw(canvas: tk.Canvas, cx: float, cy: float, size: float,
             fill: str, tags: str = "paw") -> None:
    """Stamp one paw print on a tk.Canvas, centred on (cx, cy)."""
    for dx, dy, rx, ry in _PAW_PARTS:
        x = cx + (dx - 0.5) * size
        y = cy + (dy - 0.5) * size
        canvas.create_oval(x - rx * size, y - ry * size,
                            x + rx * size, y + ry * size,
                            fill=fill, outline="", tags=tags)


def paw_photo(size: int, color: str) -> "ImageTk.PhotoImage":
    """A crisp anti-aliased paw as a PhotoImage (drawn 4x and downsampled)."""
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rgb = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    for dx, dy, rx, ry in _PAW_PARTS:
        x, y = dx * big, dy * big
        draw.ellipse((x - rx * big, y - ry * big, x + rx * big, y + ry * big),
                      fill=rgb + (255,))
    img = img.resize((size, size), Image.LANCZOS)
    return ImageTk.PhotoImage(img)


# Flavor text: (neutral, spicy) pairs. Each tab picks a side through its own
# F() method; discreet mode always forces the neutral column.
CONVERT_LABELS = {
    "scan":       ("Scan folders",        "Sniff around"),
    "start":      ("Start",               "Pounce"),
    "stop":       ("Stop",                "Heel"),
    "pause":      ("Pause",               "Stay"),
    "resume":     ("Resume",              "Go on"),
    "watch":      ("Watch mode",          "Ears up (watch)"),
    "promote":    ("Promote upscales",    "Promote to the den"),
    "dupes":      ("Find duplicates",     "Sniff out dupes"),
    "gaps":       ("Find upscale gaps",   "Sniff out the un-upscaled"),
    "gapping":    ("Checking for gaps",   "Sniffing for gaps"),
    "grid":       ("Grid",                "Grid"),
    "inspector":  ("INSPECTOR",           "PEEP BOOTH"),
    "log":        ("LOG",                 "DEN LOG"),
    "tagline":    ("clip pipeline",       "the yiff pipeline"),
    "idle":       ("Idle",                "Idle · tail curled"),
    "watching":   ("Watching",            "Ears up · watching"),
    "encoding":   ("Encoding",            "Encoding · zoomies"),
    "sorting":    ("Sorting",             "Sorting the pile"),
    "stopping":   ("Stopping",            "Heel!"),
    "paused":     ("Paused after this file", "Staying (paused after this file)"),
    "finished":   ("Finished",            "All fluffed"),
    "stopped":    ("Stopped",             "Stopped"),
    "checkup":    ("Checking upscales",   "Sniffing the upscale pile"),
    "pick":       ("Select a file to inspect it",
                    "Pick a clip and take a peek"),
    "empty":      ("Nothing queued",      "The den is tidy - nothing new"),
    "nothing_msg": ("Nothing to convert. Everything here is already "
                     "converted, or the source folders are empty.",
                     "Nothing new to convert - the den has already had "
                     "all of this. Drop fresh clips in the source folders."),
    "run_start":  ("Starting {n} files on {w} worker{s}",
                    "Pounce! {n} clips on {w} worker{s}"),
    "run_done":   ("Run finished: {d} converted · {f} failed · {srt} sorted",
                    "{d} fresh clips fluffed · {f} misbehaved · {srt} sorted"),
    "watch_new":  ("Watch: {n} new file{s} settled",
                    "Fresh meat: {n} new clip{s} settled"),
    "fetch_tags": ("Fetch e621 tags",     "Fetch the lore"),
    "fetching":   ("Fetching e621 tags",  "Sniffing e621 for lore"),
    "fetch_done": ("e621: {n} tagged · {m} unavailable",
                    "Lore secured: {n} tagged · {m} played shy"),
}

LIBRARY_LABELS = {
    "tagline":    ("local library search",  "the lore library"),
    "sync":       ("Sync library",          "Sync the den"),
    "fetch":      ("Fetch e621 tags",       "Fetch the lore"),
    "idle":       ("Ready",                 "Ready · tail curled"),
    "scanning":   ("Scanning folders",      "Sniffing the folders"),
    "indexing":   ("Indexing",              "Cataloguing the pile"),
    "synced":     ("Library synced",        "Den synced"),
    "fetching":   ("Fetching tags",         "Sniffing e621 for lore"),
    "empty_db":   ("No library yet. Press Sync to build it - the first "
                    "build probes every file, later runs only touch changes.",
                    "The den is unfurnished. Press Sync to build it - the "
                    "first build sniffs every file, later runs only touch "
                    "what changed."),
    "no_results": ("No clips match this search.",
                    "Nothing in the den matches. Loosen the leash a little."),
}
