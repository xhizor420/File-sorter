"""Library-tab dialog windows: beat markers for Resolve, saved pick sets,
hidden-tag management, the help reference, and the folder picker.
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

from .theme import T, font
from .format import fmt_len
from .files import is_ignored_dir, open_file, open_in_explorer

try:
    import paz_beats
    BEATS_IMPORT_ERROR = ""
except ModuleNotFoundError as _exc:
    paz_beats = None
    BEATS_IMPORT_ERROR = (f"{_exc.name} is missing. Beat Markers needs "
                           f"paz_beats.py on the Python path, plus numpy.")
except Exception as _exc:  # pragma: no cover - defensive
    paz_beats = None
    BEATS_IMPORT_ERROR = f"paz_beats could not load: {_exc}"


class BeatMarkers(ctk.CTkToplevel):
    """
    BPM and beat markers for DaVinci Resolve. That is the whole job.

    Pick a song, press Analyse, save. You get four files that all describe
    the same markers, so whichever route Resolve likes on your machine, the
    grid is identical.
    """

    def __init__(self, parent, tab):
        super().__init__(parent)
        self.tab = tab
        self.song = None
        self.accents = None
        self.busy = False
        self.title("Beat Markers - BPM grid for Resolve")
        self.geometry("760x620")
        self.configure(fg_color=T.BG)
        self.transient(parent)
        self.after(120, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self.beats = None
        self.error = ""
        if paz_beats is None:
            self.error = BEATS_IMPORT_ERROR
        else:
            self.beats = paz_beats

        eng = ctk.CTkFrame(self, fg_color=T.ELEVATED, corner_radius=12,
                            border_width=1, border_color=T.LINE)
        eng.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 6))
        eng.grid_columnconfigure(0, weight=1)
        self.engine_label = ctk.CTkLabel(
            eng, text="", font=font(11), text_color=T.DIM, anchor="w",
            justify="left", wraplength=560)
        self.engine_label.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
        self.engine_btn = ctk.CTkButton(
            eng, text="Copy install command", width=160, height=26,
            corner_radius=6, font=font(10), fg_color=T.BTN,
            hover_color=T.BTN_HOV, text_color=T.ACCENT,
            command=self._copy_install)
        self.engine_btn.grid(row=0, column=1, padx=(0, 12))
        self._refresh_engine()

        card = ctk.CTkFrame(self, fg_color=T.SURFACE, corner_radius=12,
                             border_width=1, border_color=T.LINE)
        card.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        ctk.CTkLabel(card, text="SONG", font=font(10, "bold"),
                     text_color=T.FAINT, anchor="w").pack(fill="x", padx=12,
                                                           pady=(10, 6))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 12))
        row.grid_columnconfigure(0, weight=1)
        self.song_entry = ctk.CTkEntry(
            row, height=34, font=font(11, mono=True), fg_color=T.INPUT,
            border_color=T.LINE, border_width=1, text_color=T.TEXT,
            placeholder_text="the track you are cutting to")
        self.song_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(row, text="Browse", width=80, height=34, corner_radius=7,
                      font=font(11), fg_color=T.BTN, hover_color=T.BTN_HOV,
                      text_color=T.DIM, command=self._browse
                      ).grid(row=0, column=1, padx=(8, 0))

        opts = ctk.CTkFrame(self, fg_color=T.SURFACE, corner_radius=12,
                             border_width=1, border_color=T.LINE)
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        inner = ctk.CTkFrame(opts, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=12)
        ctk.CTkLabel(inner, text="Timeline fps", font=font(11),
                     text_color=T.DIM, width=110, anchor="w"
                     ).grid(row=0, column=0, sticky="w")
        self.fps = ctk.CTkOptionMenu(
            inner, values=["60", "30", "24", "23.976", "25", "50", "120"],
            width=110, height=30, font=font(11), corner_radius=7,
            fg_color=T.INPUT, button_color=T.LINE,
            button_hover_color=T.BTN_HOV, dropdown_fg_color=T.ELEVATED,
            text_color=T.TEXT)
        self.fps.set("60")
        self.fps.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(inner, text="match your Resolve timeline exactly",
                     font=font(10), text_color=T.FAINT
                     ).grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.bars_only = ctk.CTkSwitch(
            inner, text="Bars only  (recommended - real PMVs cut about "
                        "every 1-2 bars)", font=font(11),
            text_color=T.DIM, progress_color=T.ACCENT2, button_color=T.TEXT)
        self.bars_only.select()
        self.bars_only.grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.accents_sw = ctk.CTkSwitch(
            inner, text="Mark HITs, DROPs and BUILDs (where effects go)",
            font=font(11), text_color=T.DIM, progress_color=T.ACCENT,
            button_color=T.TEXT)
        self.accents_sw.select()
        self.accents_sw.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        info = ctk.CTkFrame(self, fg_color="transparent")
        info.grid(row=3, column=0, sticky="ew", padx=16)
        self.summary = ctk.CTkLabel(info, text="No song analysed yet",
                                     font=font(13, "bold"), text_color=T.FAINT,
                                     anchor="w")
        self.summary.pack(fill="x")
        self.bar_canvas = tk.Canvas(info, height=40, bg=T.INPUT,
                                     highlightthickness=0, bd=0)
        self.bar_canvas.pack(fill="x", pady=(6, 0))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=5, column=0, sticky="ew", padx=16, pady=(10, 0))
        self.analyse_btn = ctk.CTkButton(
            actions, text="Analyse song", width=130, height=34,
            corner_radius=7, font=font(11, "bold"), fg_color=T.ACCENT2_DEEP,
            hover_color=T.BTN_HOV, text_color=T.ACCENT2, command=self._analyse)
        self.analyse_btn.pack(side="left", padx=(0, 8))
        self.rb_btn = ctk.CTkButton(
            actions, text="From rekordbox…", width=140, height=34,
            corner_radius=7, font=font(11), fg_color=T.BTN,
            hover_color=T.BTN_HOV, text_color=T.ACCENT2,
            command=self._use_rekordbox)
        self.rb_btn.pack(side="left", padx=(0, 8))
        self.preview_btn = ctk.CTkButton(
            actions, text="Preview grid", width=130, height=34,
            corner_radius=7, font=font(11), fg_color=T.BTN,
            hover_color=T.BTN_HOV, text_color=T.OK, state="disabled",
            command=self._preview)
        self.preview_btn.pack(side="left", padx=(0, 8))
        self.save_btn = ctk.CTkButton(
            actions, text="Save markers", width=140, height=34,
            corner_radius=7, font=font(11, "bold"), fg_color=T.ACCENT_DEEP,
            hover_color=T.BTN_HOV, text_color=T.ACCENT, state="disabled",
            command=self._save)
        self.save_btn.pack(side="left")

        self.log = tk.Text(self, height=13, bg=T.INPUT, fg=T.DIM, bd=0,
                            highlightthickness=0, font=(T.MONO, 9),
                            wrap="word", padx=10, pady=8)
        self.log.grid(row=4, column=0, sticky="nsew", padx=16, pady=(10, 0))
        for name, colour in (("ok", T.OK), ("warn", T.WARN),
                              ("fail", T.FAIL), ("accent", T.ACCENT)):
            self.log.tag_configure(name, foreground=colour)
        self.log.configure(state="disabled")
        self.grid_rowconfigure(4, weight=1)

        if self.error:
            self._log(self.error, T.FAIL)
        else:
            engines = self.beats.available_backends()
            self._log("Choose a song and press Analyse. You will get four "
                       "files describing the same grid - use whichever your "
                       "Resolve is happiest with.", T.DIM)
            self._log(f"Beat engines available: {', '.join(engines)}", T.DIM)
            if engines == ["builtin"]:
                self._log("Optional upgrade for tricky songs: "
                          "pip install beat-this", T.DIM)

    def _log(self, text, colour=None):
        tag = {T.OK: "ok", T.WARN: "warn", T.FAIL: "fail",
               T.ACCENT: "accent"}.get(colour, "")
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def ui(self, fn, *a, **k):
        try:
            self.after(0, lambda: fn(*a, **k))
        except (tk.TclError, RuntimeError):
            pass

    def _refresh_engine(self):
        if self.beats is None:
            self.engine_label.configure(text=self.error, text_color=T.FAIL)
            self.engine_btn.configure(state="disabled")
            return
        rep = self.beats.engine_report()
        self._engine_report = rep
        if rep["is_best_possible"]:
            self.engine_label.configure(
                text=f"Engine: Beat This!  (best available · running on "
                     f"{rep['device']})", text_color=T.OK)
            self.engine_btn.configure(state="disabled", text="Best engine ✓",
                                       text_color=T.OK)
        else:
            self.engine_label.configure(
                text=f"Engine: {rep['best']}  —  the most accurate tracker "
                     f"published (Beat This!, 88.9% F-measure) is not "
                     f"installed. One command in a terminal fixes it, then "
                     f"reopen this window.", text_color=T.WARN)
            self.engine_btn.configure(state="normal")

    def _copy_install(self):
        rep = getattr(self, "_engine_report", None)
        if not rep:
            return
        command = rep["install"]["beat_this"]
        self.clipboard_clear()
        self.clipboard_append(command)
        self._log("\nInstall command copied to the clipboard:", T.ACCENT)
        self._log(f"  {command}", T.DIM)
        self._log("Run it, then close and reopen Beat Markers.", T.DIM)

    def _use_rekordbox(self):
        path = self.song_entry.get().strip()
        if not os.path.isfile(path):
            self._log("Choose the song file first.", T.WARN)
            return
        xml = filedialog.askopenfilename(
            parent=self, title="rekordbox collection XML",
            filetypes=[("rekordbox XML", "*.xml"), ("All files", "*.*")])
        if not xml:
            return
        try:
            song = self.beats.analyse_song_rekordbox(path, xml)
        except Exception as exc:
            self._log(f"rekordbox import failed: {exc}", T.FAIL)
            return
        self.song = song
        self.accents = self.beats.find_accents(song, path)
        self._log("\nGrid taken from rekordbox - this is the one you "
                  "verified by eye, so it is as good as your check was.", T.OK)
        self._ready(song)

    def _browse(self):
        path = filedialog.askopenfilename(
            parent=self, title="Choose the track",
            filetypes=[("Audio", "*.mp3 *.wav *.flac *.m4a *.aac *.ogg *.opus"),
                       ("All files", "*.*")])
        if path:
            self.song_entry.delete(0, tk.END)
            self.song_entry.insert(0, path)

    def _analyse(self):
        if self.busy or self.beats is None:
            if self.beats is None:
                self._log(self.error, T.FAIL)
            return
        path = self.song_entry.get().strip()
        if not os.path.isfile(path):
            self._log("Choose a song file first.", T.WARN)
            return
        self.busy = True
        self.analyse_btn.configure(state="disabled")
        self._log(f"\nAnalysing {os.path.basename(path)}...", T.ACCENT)

        def work():
            try:
                song = self.beats.analyse_song_best(path)
                self.accents = self.beats.find_accents(song, path)
                if not song.bpm or not song.beats:
                    self.ui(self._log, "Could not read a tempo from that file.", T.FAIL)
                    return
                self.song = song
                self.ui(self._ready, song)
            except Exception as exc:
                self.ui(self._log, f"Failed: {exc}", T.FAIL)
            finally:
                self.busy = False
                self.ui(self.analyse_btn.configure, state="normal")

        threading.Thread(target=work, daemon=True).start()

    def _ready(self, song):
        import numpy as np
        gaps = np.diff(song.beats) if len(song.beats) > 1 else np.array([0.0])
        self.summary.configure(
            text=f"{song.bpm:.2f} BPM   ·   {len(song.beats)} beats   ·   "
                 f"{len(song.downbeats)} bars   ·   {fmt_len(song.duration)}",
            text_color=T.TEXT)
        self._log(f"  {song.bpm:.2f} BPM, {len(song.beats)} beats, "
                  f"{len(song.sections)} sections  "
                  f"[engine: {getattr(song, 'engine', 'builtin')}]", T.OK)
        drift = gaps.std() * 1000
        self._log(f"  beat spacing {gaps.mean():.4f}s (drift {drift:.0f} ms)",
                  T.DIM if drift < 25 else T.WARN)
        if drift > 25:
            self._log("  Beats drift a lot here - the tempo may vary. "
                      "Installing a trained tracker usually fixes this: "
                      "pip install beat-this", T.WARN)
        if self.accents:
            self._log(f"  {len(self.accents['hits'])} strong hits · "
                      f"{len(self.accents['drops'])} drops · "
                      f"{len(self.accents['builds'])} builds", T.ACCENT)
            per_min = len(song.downbeats) / max(song.duration / 60, 0.01)
            self._log(f"  bar markers land {per_min:.0f}/min; the PMVs "
                      f"measured cut about 23/min, so expect to use every "
                      f"bar or every other one.", T.DIM)
        self._draw(song)
        self.save_btn.configure(state="normal")
        self.preview_btn.configure(state="normal")
        self._log("  Tip: press Preview grid first - 30 seconds of "
                  "listening tells you if the markers are on the music.", T.DIM)

    def _draw(self, song):
        c = self.bar_canvas
        c.delete("all")
        c.update_idletasks()
        width = max(c.winfo_width(), 200)
        if song.duration <= 0:
            return
        for start, end, energy in song.sections:
            x0 = start / song.duration * width
            x1 = end / song.duration * width
            c.create_rectangle(x0, 0, x1, 40,
                                fill=T.ACCENT_DEEP if energy > 0.66 else T.ELEVATED,
                                outline="")
        for beat in song.downbeats:
            x = beat / song.duration * width
            c.create_line(x, 0, x, 16, fill=T.ACCENT, width=1)

    def _preview(self):
        if not self.song or self.busy:
            return
        import tempfile
        out = os.path.join(tempfile.gettempdir(), "paz_beat_preview.mp4")
        try:
            fps = int(round(float(self.fps.get())))
        except ValueError:
            fps = 60
        use_accents = self.accents if bool(self.accents_sw.get()) else None
        self.busy = True
        self.preview_btn.configure(state="disabled")
        self._log("\nRendering a 30-second preview...", T.ACCENT)

        def work():
            try:
                ok = self.beats.render_preview(
                    self.song, out, accents=use_accents, seconds=30,
                    fps=fps, every_beat=not bool(self.bars_only.get()))
                if ok:
                    self.ui(self._log,
                            "  Low thud = bar · tick = beat · bright click = "
                            "HIT · noise burst = DROP.", T.OK)
                    self.ui(self._log,
                            "  If those land on the music, the grid is right "
                            "and you can Save with confidence.", T.DIM)
                    open_file(out)
                else:
                    self.ui(self._log, "  Preview render failed - ffmpeg may "
                                       "be missing from PATH.", T.WARN)
            except Exception as exc:
                self.ui(self._log, f"  Preview failed: {exc}", T.FAIL)
            finally:
                self.busy = False
                self.ui(self.preview_btn.configure, state="normal")

        threading.Thread(target=work, daemon=True).start()

    def _save(self):
        if not self.song:
            return
        stem = filedialog.asksaveasfilename(
            parent=self, title="Save markers as (name only)",
            initialfile=os.path.splitext(os.path.basename(self.song.path))[0] + "_beats")
        if not stem:
            return
        stem = os.path.splitext(stem)[0]
        try:
            fps = float(self.fps.get())
        except ValueError:
            fps = 60.0
        fps_i = int(round(fps))
        try:
            use_accents = self.accents if bool(self.accents_sw.get()) else None
            files = self.beats.export_all(
                self.song, stem, fps=fps_i,
                every_beat=not bool(self.bars_only.get()), accents=use_accents)
            count = len(self.beats.build_markers(
                self.song, every_beat=not bool(self.bars_only.get()),
                accents=use_accents, fps=fps_i))
        except Exception as exc:
            self._log(f"Could not write: {exc}", T.FAIL)
            return

        self._log(f"\n{count} markers written at {fps_i} fps:", T.OK)
        self._log(f"  1  {os.path.basename(files['resolve_script'])}", T.ACCENT)
        self._log("     BEST. Open your timeline in Resolve, then Workspace >"
                  " Scripts and run this. Nothing is imported, so no media "
                  "can be missing.", T.DIM)
        self._log(f"  2  {os.path.basename(files['beatgrid_xml'])}", T.ACCENT)
        self._log("     File > Import > Timeline. Contains the song and the "
                  "markers, nothing else.", T.DIM)
        self._log(f"  3  {os.path.basename(files['markers_edl'])}", T.ACCENT)
        self._log("     For a timeline you already built: Timeline > Import >"
                  " Timeline Markers from EDL.", T.DIM)
        self._log(f"  4  {os.path.basename(files['markers_csv'])}", T.ACCENT)
        self._log("     Plain timecode list.", T.DIM)
        self._log(f"  Set the Resolve timeline to {fps_i} fps, start "
                  f"timecode 01:00:00:00.", T.WARN)
        self._log("  Red = bar (safe cut) · Blue = beat · Pink = HIT "
                  "(flash/zoom/switch) · Yellow = DROP · Cyan = BUILD "
                  "(riser) · Green = section", T.DIM)
        try:
            open_in_explorer(files["resolve_script"])
        except Exception:
            pass


class PickSetsWindow(ctk.CTkToplevel):
    """Saved shortlists - one per PMV."""

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
        ctk.CTkLabel(self, text="Each set is one PMV's shortlist. Loading a "
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
        ("Picks", "A shortlist for editing. Press P on a clip (or right-click "
         "> Add to Picks), collect as many as you want, then Copy paths or "
         "Export .m3u straight into your editor or player."),
        ("PMV workflow", "Browse the library, press P on the clips you want "
         "(★ Pick page adds a whole page), then Save set and name it - one "
         "saved shortlist per PMV. Use Beat Markers for the BPM grid in "
         "Resolve, then cut by hand against it."),
        ("Pick sets", "Named shortlists, one per PMV. Save set / Load set "
         "sit in the Picks bar, so you can switch between edits without "
         "re-picking."),
        ("Beat markers (song only)", "The draft-friendly path: choose a "
         "song, press Analyse, then export. You get a _beatgrid.xml "
         "containing ONLY the song plus a marker on every bar and beat - "
         "import it as a timeline and no media can be missing, because no "
         "video is referenced. Build your own edit on V1 over the grid."),
        ("♪ Beat Markers", "Ctrl+B. Pick a song, press Analyse, press Save. "
         "You get four files describing the same markers: a Resolve script "
         "(best - run it from Workspace > Scripts with your timeline open, "
         "nothing is imported), a song-only beatgrid.xml, a marker EDL, and "
         "a CSV."),
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
         "collapse tags · Ctrl+C copy name · Ctrl+F search · Ctrl+B beat "
         "markers · F5 sync · Ctrl+O folders · Ctrl+T theater · Ctrl+D "
         "discreet · F12 hide"),
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
