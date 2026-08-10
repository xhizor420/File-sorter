"""Library-tab dialog windows: saved pick sets, hidden-tag management, the
help reference, and the folder picker.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

from .theme import T, font
from .files import is_ignored_dir


class PickSetsWindow(ctk.CTkToplevel):
    """Saved shortlists, one per project."""

    def __init__(self, parent, tab):
        super().__init__(parent)
        self.tab = tab
        self.title("Pick sets")
        self.geometry("520x520")
        self.configure(fg_color=T.BG)
        self.transient(parent)
        self.after(120, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(self, text="Each set is a saved shortlist. Loading a "
                                "set replaces the current picks.",
                     font=font(11), text_color=T.FAINT, wraplength=470,
                     justify="left", anchor="w"
                     ).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        self.list = ctk.CTkScrollableFrame(
            self, fg_color=T.SURFACE, corner_radius=12, border_width=1,
            border_color=T.LINE, scrollbar_button_color=T.LINE,
            scrollbar_button_hover_color=T.FAINT)
        self.list.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.list.grid_columnconfigure(0, weight=1)
        self.bind("<Escape>", lambda e: self.destroy())
        self._fill()

    def _fill(self):
        for child in self.list.winfo_children():
            child.destroy()
        sets = self.tab.cfg.pick_sets
        if not sets:
            ctk.CTkLabel(self.list, text="No saved sets.", font=font(11),
                         text_color=T.FAINT).grid(row=0, column=0, padx=12,
                                                   pady=12, sticky="w")
            return
        for row, (name, paths) in enumerate(sorted(sets.items())):
            alive = sum(1 for p in paths if os.path.exists(p))
            twins = sum(1 for p in paths if self.tab.premium_twin(p))
            ctk.CTkLabel(self.list,
                         text=f"{name}\n{alive} clips · {twins} in 4K60",
                         font=font(11), text_color=T.TEXT, anchor="w",
                         justify="left").grid(row=row, column=0, sticky="ew",
                                               padx=(12, 4), pady=5)
            ctk.CTkButton(self.list, text="Load", width=62, height=26,
                          corner_radius=6, font=font(10), fg_color=T.BTN,
                          hover_color=T.BTN_HOV, text_color=T.ACCENT,
                          command=lambda n=name: self._load(n)
                          ).grid(row=row, column=1, padx=4, pady=5)
            ctk.CTkButton(self.list, text="Delete", width=62, height=26,
                          corner_radius=6, font=font(10), fg_color=T.BTN,
                          hover_color=T.BTN_HOV, text_color=T.FAIL,
                          command=lambda n=name: self._delete(n)
                          ).grid(row=row, column=2, padx=(4, 10), pady=5)

    def _load(self, name):
        self.tab.apply_pick_set(name)
        self.destroy()

    def _delete(self, name):
        self.tab.delete_pick_set(name)
        self._fill()


class HiddenTagsWindow(ctk.CTkToplevel):
    """Restore tags that were muted from the sidebar."""

    def __init__(self, parent, tab):
        super().__init__(parent)
        self.tab = tab
        self.title("Hidden tags")
        self.geometry("420x520")
        self.configure(fg_color=T.BG)
        self.transient(parent)
        self.after(120, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self, text="Hidden from the sidebar - never deleted, "
                                "always searchable, still shown on clips.",
                     font=font(10), text_color=T.FAINT, wraplength=380,
                     justify="left").grid(row=0, column=0, sticky="ew",
                                          padx=16, pady=(14, 6))
        self.list = ctk.CTkScrollableFrame(
            self, fg_color=T.SURFACE, corner_radius=12, border_width=1,
            border_color=T.LINE, scrollbar_button_color=T.LINE,
            scrollbar_button_hover_color=T.FAINT)
        self.list.grid(row=1, column=0, sticky="nsew", padx=14)
        self.list.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=14, pady=12)
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(footer, text="Restore all", width=100, height=30,
                      corner_radius=7, font=font(11), fg_color=T.BTN,
                      hover_color=T.BTN_HOV, text_color=T.ACCENT,
                      command=self._restore_all).grid(row=0, column=1)
        self._fill()

    def _fill(self):
        for child in self.list.winfo_children():
            child.destroy()
        hidden = sorted(self.tab.cfg.hidden_tags)
        if not hidden:
            ctk.CTkLabel(self.list, text="Nothing hidden.", font=font(11),
                         text_color=T.FAINT).grid(row=0, column=0, padx=12,
                                                   pady=12, sticky="w")
            return
        for row, name in enumerate(hidden):
            ctk.CTkLabel(self.list, text=name, font=font(11),
                         text_color=T.TEXT, anchor="w"
                         ).grid(row=row, column=0, sticky="ew", padx=(12, 4), pady=3)
            ctk.CTkButton(self.list, text="Restore", width=70, height=24,
                          corner_radius=6, font=font(10), fg_color=T.BTN,
                          hover_color=T.BTN_HOV, text_color=T.OK,
                          command=lambda n=name: self._restore(n)
                          ).grid(row=row, column=1, padx=(0, 10), pady=3)

    def _restore(self, name: str):
        self.tab.unhide_tag(name)
        self._fill()

    def _restore_all(self):
        self.tab.cfg.hidden_tags = []
        self.tab.cfg.save()
        self.tab._render_tagpanel()
        self._fill()


class HelpWindow(ctk.CTkToplevel):
    """What every button does, in one place."""

    SECTIONS = (
        ("Sync library", "Compares the chosen folders against the database "
         "and only processes what changed - new files get probed and "
         "thumbnailed, deleted ones are removed. The first build is the slow "
         "one; after that it takes seconds. Ctrl+Shift+R forces a rebuild."),
        ("Fix missing", "One click, no per-clip work: re-probes files with no "
         "duration/resolution, regenerates absent thumbnails, then fetches "
         "tags for every uncached post ID. The number on the button is how "
         "much is left. Runs automatically after a sync if autofetch is on."),
        ("Fetch e621 tags", "Just the tag half of Fix missing: resolves post "
         "IDs (the filename numbers) into artist / character / species / "
         "rating via e621's API. Add your API key in Settings for fewer "
         "unavailable posts."),
        ("Picks", "A shortlist for whatever you're doing next - editing, "
         "reviewing, exporting. Press P on a clip (or right-click > Add to "
         "Picks), collect as many as you want, then Copy paths or Export "
         ".m3u straight into your editor or player."),
        ("Building a shortlist", "Browse the library, press P on the clips "
         "you want (★ Pick page adds a whole page), then Save set and name "
         "it - one saved shortlist per project. Load it again any time from "
         "the Picks bar."),
        ("Pick sets", "Named shortlists. Save set / Load set sit in the "
         "Picks bar, so you can switch between projects without re-picking."),
        ("▲ Score", "The e621 upvote score for that post, next to the fps on "
         "every card. Sort by it with the Top rated chip, or the Score sort."),
        ("4K ✓", "Shown when a 4K/60+ copy of that exact file exists in the "
         "premium folder (or the clip itself is 4K). Search it with is:4k."),
        ("Search", "Terms AND together. -term excludes. Prefixes: artist: "
         "character: species: rating: folder: id: is:. Wildcards: dragon*. "
         "is:untagged, is:noid, is:4k are the useful specials. Click any tag "
         "anywhere to add it; right-click for exclude/hide."),
        ("Viewer size", "The player scales with the window. Theater mode "
         "(Ctrl+T, or the button above the viewer) gives it about half the "
         "window and collapses the tag rail for close inspection."),
        ("Picking up where you left off", "Your search, sort and rating "
         "filter are saved as you go and restored next launch."),
        ("Copying", "Right-click any clip > Copy for file name, name "
         "without extension, full path, folder, post ID, e621 URL, artist "
         "or every tag. Ctrl+C copies the selected clip's file name, "
         "Ctrl+Shift+C the full path."),
        ("Search history", "Every search you press Enter on is remembered. "
         "Up/Down arrows in the search box step through them, the ↺ "
         "button lists them, and ✕ clears the box."),
        ("Keys", "/ search · Enter or Space play/pause · ←→ seek 5s · "
         "P pick · R random · PgUp/PgDn pages · 1-4 tile size · Ctrl+L "
         "collapse tags · Ctrl+C copy name · Ctrl+F search · F5 sync · "
         "Ctrl+O folders · Ctrl+T theater · Ctrl+D discreet · F12 hide"),
    )

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Help")
        self.geometry("560x640")
        self.configure(fg_color=T.BG)
        self.transient(parent)
        self.after(120, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        body = ctk.CTkScrollableFrame(
            self, fg_color=T.SURFACE, corner_radius=12, border_width=1,
            border_color=T.LINE, scrollbar_button_color=T.LINE,
            scrollbar_button_hover_color=T.FAINT)
        body.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)
        body.grid_columnconfigure(0, weight=1)
        row = 0
        for title, text in self.SECTIONS:
            ctk.CTkLabel(body, text=title, font=font(12, "bold"),
                         text_color=T.ACCENT, anchor="w"
                         ).grid(row=row, column=0, sticky="ew", padx=14,
                                pady=(14 if row else 12, 2))
            row += 1
            ctk.CTkLabel(body, text=text, font=font(10), text_color=T.DIM,
                         wraplength=480, justify="left", anchor="w"
                         ).grid(row=row, column=0, sticky="ew", padx=14)
            row += 1
        self.bind("<Escape>", lambda e: self.destroy())


class FoldersWindow(ctk.CTkToplevel):
    """
    Picks exactly what the Library indexes: one root folder (defaults to
    the Convert tab's output folder, since that's almost always what you
    want), and which category subfolders inside it count. Everything
    unticked is invisible to the Library - not scanned, not probed, not
    thumbnailed.
    """

    def __init__(self, parent, tab):
        super().__init__(parent)
        self.tab = tab
        self.cfg = tab.cfg
        self._before = (self.cfg.library_root, sorted(self.cfg.library_subfolders),
                         self.cfg.library_recursive)

        self.title("Library folders")
        self.geometry("640x560")
        self.configure(fg_color=T.BG)
        self.transient(parent)
        self.after(120, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(self, text="LIBRARY ROOT", font=font(10, "bold"),
                     text_color=T.FAINT, anchor="w"
                     ).grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 4))

        rowf = ctk.CTkFrame(self, fg_color="transparent")
        rowf.grid(row=1, column=0, sticky="ew", padx=16)
        rowf.grid_columnconfigure(0, weight=1)
        self.root_entry = ctk.CTkEntry(
            rowf, height=32, font=font(11, mono=True), fg_color=T.INPUT,
            border_color=T.LINE, border_width=1, text_color=T.TEXT)
        self.root_entry.insert(0, self.cfg.effective_library_root())
        self.root_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(rowf, text="Browse", width=76, height=32,
                      corner_radius=7, font=font(10), fg_color=T.BTN,
                      hover_color=T.BTN_HOV, text_color=T.DIM,
                      command=self._browse).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(rowf, text="Rescan", width=70, height=32,
                      corner_radius=7, font=font(10), fg_color=T.BTN,
                      hover_color=T.BTN_HOV, text_color=T.ACCENT2,
                      command=self._reload).grid(row=0, column=2, padx=(8, 0))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=16, pady=(14, 0))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(body, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="CATEGORIES TO INDEX", font=font(10, "bold"),
                     text_color=T.FAINT, anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="All", width=44, height=22, corner_radius=5,
                      font=font(10), fg_color=T.BTN, hover_color=T.BTN_HOV,
                      text_color=T.DIM, command=lambda: self._set_all(True)
                      ).grid(row=0, column=1, padx=(0, 6))
        ctk.CTkButton(head, text="None", width=52, height=22, corner_radius=5,
                      font=font(10), fg_color=T.BTN, hover_color=T.BTN_HOV,
                      text_color=T.DIM, command=lambda: self._set_all(False)
                      ).grid(row=0, column=2)

        self.list = ctk.CTkScrollableFrame(
            body, fg_color=T.SURFACE, corner_radius=12, border_width=1,
            border_color=T.LINE, scrollbar_button_color=T.LINE,
            scrollbar_button_hover_color=T.FAINT)
        self.list.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.list.grid_columnconfigure(0, weight=1)

        self.recursive = ctk.CTkSwitch(
            self, text="Also index folders nested inside these",
            font=font(11), text_color=T.DIM, progress_color=T.ACCENT2,
            button_color=T.TEXT)
        (self.recursive.select() if self.cfg.library_recursive
         else self.recursive.deselect())
        self.recursive.grid(row=3, column=0, sticky="w", padx=20, pady=(12, 0))

        self.note = ctk.CTkLabel(self, text="", font=font(10),
                                  text_color=T.FAINT, anchor="w",
                                  wraplength=580, justify="left")
        self.note.grid(row=4, column=0, sticky="ew", padx=20, pady=(8, 0))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=5, column=0, sticky="ew", padx=16, pady=14)
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(footer, text="Cancel", width=90, height=32,
                      corner_radius=7, font=font(11), fg_color=T.BTN,
                      hover_color=T.BTN_HOV, text_color=T.DIM,
                      command=self.destroy).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(footer, text="Save folders", width=130, height=32,
                      corner_radius=7, font=font(11, "bold"),
                      fg_color=T.ACCENT2_DEEP, hover_color=T.BTN_HOV,
                      text_color=T.ACCENT2, command=self._save
                      ).grid(row=0, column=2)

        self.boxes: dict = {}
        self._reload()

    def _browse(self):
        chosen = filedialog.askdirectory(
            parent=self, initialdir=self.root_entry.get() or "/")
        if chosen:
            self.root_entry.delete(0, tk.END)
            self.root_entry.insert(0, os.path.normpath(chosen))
            self._reload()

    def _reload(self):
        for child in self.list.winfo_children():
            child.destroy()
        self.boxes = {}
        root = self.root_entry.get().strip()
        if not os.path.isdir(root):
            self.note.configure(
                text="That folder does not exist yet. Pick the converted "
                     "library (the Convert tab's output folder).")
            return
        try:
            names = sorted(n for n in os.listdir(root)
                            if os.path.isdir(os.path.join(root, n))
                            and not is_ignored_dir(n))
        except OSError as exc:
            self.note.configure(text=f"Could not read that folder: {exc}")
            return
        wanted = set(self.cfg.library_subfolders)
        for index, name in enumerate(names):
            count = self._count(os.path.join(root, name))
            box = ctk.CTkCheckBox(
                self.list, text=f"{name}     ({count} files)",
                font=font(11), text_color=T.TEXT, fg_color=T.ACCENT2,
                hover_color=T.ACCENT2_HOV, border_color=T.LINE,
                checkbox_width=18, checkbox_height=18)
            if not wanted or name in wanted:
                box.select()
            box.grid(row=index, column=0, sticky="w", padx=12, pady=4)
            self.boxes[name] = box
        self.note.configure(
            text=f"{len(names)} subfolders found. Only ticked ones are "
                 "scanned - the rest cost no time at all.")

    def _count(self, directory: str) -> int:
        ext = self.cfg.library_ext_set
        try:
            with os.scandir(directory) as entries:
                return sum(1 for e in entries if e.is_file()
                           and os.path.splitext(e.name)[1].lower() in ext)
        except OSError:
            return 0

    def _set_all(self, state: bool):
        for box in self.boxes.values():
            box.select() if state else box.deselect()

    def _save(self):
        root = self.root_entry.get().strip()
        self.cfg.library_root = root
        picked = [name for name, box in self.boxes.items() if box.get()]
        self.cfg.library_subfolders = picked
        self.cfg.library_recursive = bool(self.recursive.get())
        changed = (self.cfg.library_root, sorted(self.cfg.library_subfolders),
                   self.cfg.library_recursive) != self._before
        self.tab._folders_saved(changed)
        self.destroy()
