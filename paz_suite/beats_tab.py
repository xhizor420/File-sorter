"""The Beats tab: neural beat/downbeat detection for a selected clip
(beat-this, https://github.com/CPJKU/beat_this - a trained model, not a
heuristic), exported as DaVinci Resolve markers.

torch/beat-this are an optional, heavy dependency - this module never
imports them directly, only checks beats_engine.BEATS_AVAILABLE, so the
rest of the suite (Convert/Library/Vault) is completely unaffected whether
or not they're installed. When they aren't, this tab shows a "not
installed" screen with a copy-pasteable pip command instead of the app
failing to start.
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import customtkinter as ctk

from . import beats_engine, beats_export
from .files import open_in_explorer
from .format import fmt_len
from .library_windows import HelpWindow
from .media import probe
from .theme import BEATS_LABELS, T, font
from .widgets import Bar, Card, LogView, StatTile

MEDIA_FILETYPES = [
    ("Media files", "*.mp4 *.mov *.mkv *.webm *.m4v *.avi *.mp3 *.wav *.flac *.m4a"),
    ("All files", "*.*"),
]


class BeatsTab(ctk.CTkFrame):

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=T.BG, corner_radius=0)
        self.pack(fill="both", expand=True)

        self.app = app
        self.root = app.root
        self.cfg = app.cfg
        self.toaster = app.toaster

        self.clip_path: str | None = None
        self.clip_name: str = ""
        self.clip_duration: float = 0.0
        self.clip_fps: float = 30.0
        self.result: beats_engine.BeatResult | None = None

        self.busy = False
        self.cancel = threading.Event()
        self._token = 0

        self.grid_columnconfigure(0, weight=1, uniform="cols")
        self.grid_columnconfigure(1, weight=1, uniform="cols")
        self.grid_rowconfigure(1, weight=1)

        self._build()

    # ── copy ─────────────────────────────────────────────────────────────

    def F(self, key: str, **fmt) -> str:
        text = BEATS_LABELS[key]
        return text.format(**fmt) if fmt else text

    # ── thread-safe UI ──────────────────────────────────────────────────

    def ui(self, fn, *args, **kwargs):
        self.after(0, lambda: fn(*args, **kwargs))

    # ── layout ──────────────────────────────────────────────────────────

    def _build(self):
        self._build_topbar()
        if beats_engine.BEATS_AVAILABLE:
            self._build_left()
            self._build_right()
        else:
            self._build_missing_deps()

    def _build_topbar(self):
        bar = ctk.CTkFrame(self, fg_color=T.SURFACE, corner_radius=0, height=58)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=20, pady=10)
        ctk.CTkLabel(left, text="PAZ", font=font(19, "bold"),
                     text_color=T.ACCENT4).pack(side="left")
        ctk.CTkLabel(left, text="Beats", font=font(19), text_color=T.TEXT
                     ).pack(side="left", padx=(5, 0))
        ctk.CTkLabel(left, text=self.F("tagline"), font=font(10, mono=True),
                     text_color=T.FAINT).pack(side="left", padx=(12, 0), pady=(6, 0))

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e", padx=20)
        ctk.CTkButton(right, text="?", width=30, height=30, corner_radius=7,
                      font=font(12, "bold"), fg_color=T.BTN, hover_color=T.BTN_HOV,
                      text_color=T.FAINT, command=self._open_help).pack(side="left")

    # ── "not installed" screen ───────────────────────────────────────────

    def _build_missing_deps(self):
        wrap = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        wrap.grid(row=1, column=0, columnspan=2, sticky="nsew")
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(0, weight=1)

        card = Card(wrap)
        card.grid(row=0, column=0)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=32, pady=28)

        ctk.CTkLabel(inner, text=self.F("no_deps"), font=font(16, "bold"),
                     text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(
            inner, wraplength=440, justify="left", font=font(11), text_color=T.DIM,
            text=("This tab needs PyTorch and beat-this for real neural "
                  "beat/downbeat detection - a trained model evaluated against "
                  "human-annotated benchmarks, not a heuristic. Convert, "
                  "Library and Vault are completely unaffected either way.")
        ).pack(anchor="w", pady=(8, 18))

        self._pip_row(inner, "Recommended:", beats_engine.PIP_HINT)
        self._pip_row(inner, "CPU-only (smaller download, no GPU needed):",
                       beats_engine.PIP_HINT_CPU)

        if beats_engine._IMPORT_ERROR:
            ctk.CTkLabel(
                inner, text=f"Import error: {beats_engine._IMPORT_ERROR}",
                font=font(9, mono=True), text_color=T.FAINT,
                wraplength=440, justify="left"
            ).pack(anchor="w", pady=(14, 0))

    def _pip_row(self, parent, label: str, command: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(row, text=label, font=font(10), text_color=T.FAINT,
                     anchor="w").pack(anchor="w")
        entry_row = ctk.CTkFrame(row, fg_color="transparent")
        entry_row.pack(fill="x", pady=(2, 0))
        entry = ctk.CTkEntry(entry_row, font=font(10, mono=True), height=30,
                              fg_color=T.INPUT, border_color=T.LINE, border_width=1,
                              text_color=T.TEXT)
        entry.insert(0, command)
        entry.configure(state="readonly")
        entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(entry_row, text="Copy", width=60, height=30, corner_radius=6,
                      font=font(10), fg_color=T.BTN, hover_color=T.BTN_HOV,
                      text_color=T.DIM, command=lambda: self._copy(command)
                      ).pack(side="left", padx=(6, 0))

    def _copy(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)

    # ── left panel: clip picker + controls ───────────────────────────────

    def _build_left(self):
        panel = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        panel.grid(row=1, column=0, sticky="nsew", padx=(14, 7), pady=14)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(panel, text="CLIP", font=font(9, "bold"), text_color=T.FAINT,
                     anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 4))

        self.source_seg = ctk.CTkSegmentedButton(
            panel, values=["From Library", "Browse file"],
            command=self._on_source_toggle, font=font(10), height=28,
            corner_radius=6, fg_color=T.INPUT, selected_color=T.ACCENT4_DEEP,
            selected_hover_color=T.ACCENT4_DEEP, unselected_color=T.INPUT,
            unselected_hover_color=T.BTN_HOV, text_color=T.DIM, border_width=1)
        self.source_seg.set("From Library")
        self.source_seg.grid(row=1, column=0, sticky="ew", pady=(0, 6))

        self.library_search = ctk.CTkEntry(
            panel, placeholder_text="Filter by name", height=28, font=font(11),
            corner_radius=6, fg_color=T.INPUT, border_color=T.LINE, border_width=1,
            text_color=T.TEXT)
        self.library_search.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self.library_search.bind("<KeyRelease>", lambda e: self._refresh_library_list())

        self.browse_btn = ctk.CTkButton(
            panel, text="Browse for a file…", height=30, corner_radius=7,
            font=font(11), fg_color=T.BTN, hover_color=T.BTN_HOV,
            text_color=T.TEXT, command=self._browse_file)

        self.library_frame = ctk.CTkScrollableFrame(
            panel, fg_color=T.SURFACE, corner_radius=10, border_width=1,
            border_color=T.ACCENT4_DEEP, height=200)
        self.library_frame.grid(row=4, column=0, sticky="nsew", pady=(0, 8))
        self.library_frame.grid_columnconfigure(0, weight=1)
        self._refresh_library_list()

        self.clip_info_label = ctk.CTkLabel(
            panel, text="No clip selected", font=font(10), text_color=T.DIM,
            anchor="w", justify="left", wraplength=380)
        self.clip_info_label.grid(row=5, column=0, sticky="ew", pady=(0, 8))

        self._build_options(panel)

        controls = ctk.CTkFrame(panel, fg_color="transparent")
        controls.grid(row=7, column=0, sticky="ew", pady=(0, 6))
        self.analyze_btn = ctk.CTkButton(
            controls, text="Analyze", height=32, corner_radius=7,
            font=font(11, "bold"), fg_color=T.ACCENT4_DEEP, hover_color=T.BTN_HOV,
            text_color=T.ACCENT4, command=self._analyze)
        self.analyze_btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(
            controls, text="Cancel", height=32, corner_radius=7, font=font(11),
            fg_color=T.BTN, hover_color=T.BTN_HOV, text_color=T.DIM,
            state="disabled", command=self._cancel)
        self.cancel_btn.pack(side="left", padx=(8, 0))

        self.progress = Bar(panel, color=T.ACCENT4)
        self.progress.grid(row=8, column=0, sticky="ew", pady=(0, 8))

        self.logview = LogView(panel, height=140)
        self.logview.grid(row=9, column=0, sticky="ew")
        self.logview.header_label.configure(text="STATUS")
        self.logview.write(self.F("idle"), "info")

    def _build_options(self, panel) -> None:
        options = ctk.CTkFrame(panel, fg_color="transparent")
        options.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        options.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(options, text="Checkpoint", font=font(10), text_color=T.DIM
                     ).grid(row=0, column=0, sticky="w")
        self.checkpoint_menu = ctk.CTkOptionMenu(
            options, values=list(beats_engine.CHECKPOINTS), width=100, height=26,
            font=font(10), corner_radius=6, fg_color=T.INPUT, button_color=T.LINE,
            button_hover_color=T.BTN_HOV, dropdown_fg_color=T.ELEVATED,
            text_color=T.TEXT, command=self._on_checkpoint_change)
        self.checkpoint_menu.set(
            self.cfg.beats_checkpoint if self.cfg.beats_checkpoint in beats_engine.CHECKPOINTS
            else "final0")
        self.checkpoint_menu.grid(row=0, column=1, sticky="w", padx=(8, 0))

        self.cpu_switch = ctk.CTkSwitch(
            options, text="Force CPU", font=font(10), text_color=T.DIM,
            progress_color=T.ACCENT4, button_color=T.TEXT,
            command=self._on_cpu_switch)
        if self.cfg.beats_force_cpu:
            self.cpu_switch.select()
        self.cpu_switch.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ctk.CTkLabel(options, text="Markers", font=font(10), text_color=T.DIM
                     ).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.marker_seg = ctk.CTkSegmentedButton(
            options, values=["beats", "downbeats", "both"], font=font(10), height=26,
            corner_radius=6, fg_color=T.INPUT, selected_color=T.ACCENT4_DEEP,
            selected_hover_color=T.ACCENT4_DEEP, unselected_color=T.INPUT,
            unselected_hover_color=T.BTN_HOV, text_color=T.DIM, border_width=1,
            command=self._on_marker_set_change)
        self.marker_seg.set(
            self.cfg.beats_marker_set if self.cfg.beats_marker_set in
            ("beats", "downbeats", "both") else "both")
        self.marker_seg.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

    # ── right panel: results + export ────────────────────────────────────

    def _build_right(self):
        panel = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        panel.grid(row=1, column=1, sticky="nsew", padx=(7, 14), pady=14)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)

        stats = ctk.CTkFrame(panel, fg_color="transparent")
        stats.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for i in range(4):
            stats.grid_columnconfigure(i, weight=1, uniform="stats")
        self.tile_bpm = StatTile(stats, "BPM", color=T.ACCENT4)
        self.tile_bpm.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.tile_beats = StatTile(stats, "Beats", color=T.TEXT)
        self.tile_beats.grid(row=0, column=1, sticky="ew", padx=6)
        self.tile_downbeats = StatTile(stats, "Downbeats", color=T.TEXT)
        self.tile_downbeats.grid(row=0, column=2, sticky="ew", padx=6)
        self.tile_duration = StatTile(stats, "Duration", color=T.TEXT)
        self.tile_duration.grid(row=0, column=3, sticky="ew", padx=(6, 0))

        ctk.CTkLabel(panel, text="TIMELINE", font=font(9, "bold"), text_color=T.FAINT,
                     anchor="w").grid(row=1, column=0, sticky="w", pady=(0, 4))
        # A plain tick strip, not a waveform - nothing in this codebase
        # decodes/renders audio waveforms yet, so a real one is future
        # scope, not squeezed into this pass.
        self.timeline = tk.Canvas(panel, height=64, bg=T.INPUT,
                                   highlightthickness=0, bd=0)
        self.timeline.grid(row=2, column=0, sticky="nsew", pady=(0, 12))
        self.timeline.bind("<Configure>", lambda e: self._draw_timeline())

        export = Card(panel, title="Export")
        export.grid(row=3, column=0, sticky="ew")
        inner = ctk.CTkFrame(export, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=(6, 14))
        inner.grid_columnconfigure(0, weight=1)

        self.export_path_entry = ctk.CTkEntry(
            inner, height=28, font=font(10, mono=True), fg_color=T.INPUT,
            border_color=T.LINE, border_width=1, text_color=T.TEXT)
        self.export_path_entry.grid(row=0, column=0, sticky="ew", pady=(6, 6))

        btn_row = ctk.CTkFrame(inner, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew")
        ctk.CTkButton(btn_row, text="Change…", width=90, height=28, corner_radius=6,
                      font=font(10), fg_color=T.BTN, hover_color=T.BTN_HOV,
                      text_color=T.DIM, command=self._change_export_folder
                      ).pack(side="left")
        self.export_btn = ctk.CTkButton(
            btn_row, text="Export 4 files", height=28, corner_radius=6,
            font=font(10, "bold"), fg_color=T.ACCENT4_DEEP, hover_color=T.BTN_HOV,
            text_color=T.ACCENT4, state="disabled", command=self._export)
        self.export_btn.pack(side="left", padx=(8, 0))

    def _draw_timeline(self) -> None:
        c = self.timeline
        c.delete("all")
        if not self.result or not self.result.duration:
            return
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 20:
            return
        duration = self.result.duration
        downbeat_times = {round(t, 3) for t in self.result.downbeats}
        for t in self.result.beats:
            x = 2 + (t / duration) * (w - 4)
            is_down = round(t, 3) in downbeat_times
            top = 6 if is_down else h * 0.4
            c.create_line(x, top, x, h - 6,
                           fill=T.ACCENT4 if is_down else T.DIM,
                           width=2 if is_down else 1)

    # ── clip picking ──────────────────────────────────────────────────────

    def _on_source_toggle(self, value: str) -> None:
        if value == "From Library":
            self.browse_btn.grid_remove()
            self.library_search.grid()
            self.library_frame.grid()
            self._refresh_library_list()
        else:
            self.library_search.grid_remove()
            self.library_frame.grid_remove()
            self.browse_btn.grid(row=2, column=0, sticky="ew", pady=(0, 6))

    def _refresh_library_list(self) -> None:
        for child in self.library_frame.winfo_children():
            child.destroy()
        library = getattr(self.app, "library", None)
        records = library.records if library else []
        query = self.library_search.get().strip().lower()
        if query:
            records = [r for r in records if query in r.name.lower()]
        # Capped rather than rendering a whole five-figure library as buttons.
        records = sorted(records, key=lambda r: r.name.lower())[:200]
        for rec in records:
            ctk.CTkButton(
                self.library_frame, text=rec.name, anchor="w", height=26,
                corner_radius=6, font=font(10), fg_color="transparent",
                hover_color=T.BTN_HOV, text_color=T.DIM,
                command=lambda r=rec: self._pick_library_clip(r)
            ).pack(fill="x", pady=1)

    def _pick_library_clip(self, rec) -> None:
        path = rec.path
        if getattr(rec, "premium_path", "") and self.cfg.player_prefer_premium:
            path = rec.premium_path
        self._set_clip(path, rec.name)

    def _browse_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="Choose an audio or video file",
            filetypes=MEDIA_FILETYPES)
        if path:
            self._set_clip(path, os.path.basename(path))

    def _set_clip(self, path: str, name: str) -> None:
        self.clip_path = path
        self.clip_name = name
        info = probe(path)
        if info and info.duration:
            self.clip_duration = info.duration
            self.clip_fps = info.fps or 30.0
            self.clip_info_label.configure(
                text=f"{name}\n{info.resolution} · {info.fps_text} fps · "
                     f"{fmt_len(info.duration)}")
        else:
            self.clip_duration = 0.0
            self.clip_fps = 30.0
            self.clip_info_label.configure(text=f"{name}\n(could not read file details)")
        self._clear_results()

    # ── analysis ──────────────────────────────────────────────────────────

    def _clear_results(self) -> None:
        self.result = None
        self.tile_bpm.set("--")
        self.tile_beats.set("--")
        self.tile_downbeats.set("--")
        self.tile_duration.set("--")
        self.timeline.delete("all")
        self.export_btn.configure(state="disabled")
        self.progress.reset()

    def _set_picker_enabled(self, enabled: bool) -> None:
        """Blocks switching clips mid-analysis - simpler and safer than
        having a completion callback race a still-running job against a
        newly picked clip's blank state."""
        state = "normal" if enabled else "disabled"
        self.source_seg.configure(state=state)
        self.browse_btn.configure(state=state)
        self.library_search.configure(state=state)
        for child in self.library_frame.winfo_children():
            try:
                child.configure(state=state)
            except Exception:
                pass

    def _analyze(self) -> None:
        if self.busy:
            return
        if not self.clip_path:
            self.logview.write("Pick a clip first.", "warn")
            return
        self.busy = True
        self.cancel = threading.Event()
        cancel = self.cancel
        self._token += 1
        token = self._token

        self.analyze_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self._set_picker_enabled(False)
        self._clear_results()
        self.progress.set(0.02)

        checkpoint = self.checkpoint_menu.get()
        force_cpu = bool(self.cpu_switch.get())
        marker_set = self.marker_seg.get()
        path = self.clip_path

        def on_stage(name: str) -> None:
            self.ui(self._on_stage, name, token)

        def work():
            result = beats_engine.analyze(
                path, checkpoint=checkpoint, force_cpu=force_cpu,
                cancel=cancel, on_stage=on_stage)
            self.ui(self._on_analyzed, result, token, cancel, marker_set)

        threading.Thread(target=work, daemon=True).start()

    def _on_stage(self, stage_name: str, token: int) -> None:
        if token != self._token:
            return
        fractions = {"extracting": 0.15, "loading_model": 0.45, "detecting": 0.75}
        self.progress.set(fractions.get(stage_name, 0.5))
        self.logview.write(self.F(stage_name), "info")

    def _cancel(self) -> None:
        if self.busy:
            self.cancel.set()

    def _on_analyzed(self, result, token: int, cancel: threading.Event,
                      marker_set: str) -> None:
        if token != self._token:
            return
        self.busy = False
        self.analyze_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self._set_picker_enabled(True)

        if cancel.is_set():
            self.progress.reset()
            self.logview.write(self.F("cancelled"), "warn")
            return
        if result is None:
            self.progress.reset()
            self.logview.write(self.F("failed"), "fail")
            return

        self.result = result
        self.progress.set(1.0, color=T.OK)
        self.tile_bpm.set(f"{result.bpm_estimate:.0f}" if result.bpm_estimate else "--")
        self.tile_beats.set(str(len(result.beats)))
        self.tile_downbeats.set(str(len(result.downbeats)))
        self.tile_duration.set(fmt_len(result.duration))
        self._draw_timeline()
        self.export_btn.configure(state="normal")
        self.export_path_entry.delete(0, tk.END)
        self.export_path_entry.insert(0, self._default_export_dir(self.clip_path))
        bpm_text = f"{result.bpm_estimate:.0f}" if result.bpm_estimate else "?"
        self.logview.write(
            self.F("done", b=len(result.beats), d=len(result.downbeats), bpm=bpm_text),
            "ok")

    # ── export ────────────────────────────────────────────────────────────

    def _default_export_dir(self, path: str) -> str:
        stem = os.path.splitext(os.path.basename(path))[0]
        root = self.cfg.beats_export_root or os.path.dirname(path)
        return os.path.join(root, f"{stem}_beats")

    def _change_export_folder(self) -> None:
        chosen = filedialog.askdirectory(
            parent=self.root, title="Choose an export folder",
            initialdir=self.export_path_entry.get() or os.path.expanduser("~"))
        if chosen:
            self.export_path_entry.delete(0, tk.END)
            self.export_path_entry.insert(0, chosen)

    def _export(self) -> None:
        if not self.result:
            return
        dest_dir = self.export_path_entry.get().strip() or self._default_export_dir(self.clip_path)
        try:
            beats_export.export_all(
                self.result, dest_dir, self.clip_name, self.clip_fps,
                marker_set=self.marker_seg.get())
        except OSError as exc:
            self.logview.write(f"{self.F('export_failed')}: {exc}", "fail")
            return
        self.logview.write(self.F("exported", path=dest_dir), "ok")
        self.toaster.show(self.F("exported", path=dest_dir))
        open_in_explorer(dest_dir)

    # ── misc ─────────────────────────────────────────────────────────────

    def _on_checkpoint_change(self, value: str) -> None:
        self.cfg.beats_checkpoint = value
        self.cfg.save()

    def _on_cpu_switch(self) -> None:
        self.cfg.beats_force_cpu = bool(self.cpu_switch.get())
        self.cfg.save()

    def _on_marker_set_change(self, value: str) -> None:
        self.cfg.beats_marker_set = value
        self.cfg.save()

    def _open_help(self) -> None:
        HelpWindow(self.root)

    # ── keyboard / lifecycle (dispatched centrally by the app) ─────────

    @staticmethod
    def is_typing(event) -> bool:
        return isinstance(event.widget, (ctk.CTkEntry, tk.Entry, tk.Text, ttk.Entry))

    def key_analyze(self, event=None) -> None:
        if beats_engine.BEATS_AVAILABLE:
            self._analyze()

    def key_cancel(self, event=None) -> None:
        self._cancel()

    def on_app_close(self) -> bool:
        return True

    def after_settings_saved(self) -> None:
        if not beats_engine.BEATS_AVAILABLE:
            return
        self.cpu_switch.select() if self.cfg.beats_force_cpu else self.cpu_switch.deselect()
        if self.cfg.beats_checkpoint in beats_engine.CHECKPOINTS:
            self.checkpoint_menu.set(self.cfg.beats_checkpoint)
        if self.cfg.beats_marker_set in ("beats", "downbeats", "both"):
            self.marker_seg.set(self.cfg.beats_marker_set)
