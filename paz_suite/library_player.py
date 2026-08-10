"""The embedded clip player for the Library tab.

Idle it shows the selected clip's thumbnail. Press Play and the shared
:class:`~paz_suite.player_engine.ClipPlayer` engine takes over — seek bar,
loop, speed and frame-accurate scrubbing, with real audio through a second
ffplay process. This module only builds the UI chrome (buttons, seek bar,
clock, volume) and wires it to the engine.
"""

from __future__ import annotations

import io
import os
import threading
import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageTk

from .theme import T, font
from .format import fmt_clock, fmt_len
from .config import THUMB_DIR
from .media import fit_frame, thumb_key
from .player_engine import ClipPlayer, HAS_FFPLAY


class InlinePlayer:
    # Starting geometry only. The player resizes with the window - on a 4K
    # screen a fixed small canvas is unreadably small next to 4K stills.
    VIEW_W, VIEW_H = 424, 238

    def __init__(self, parent, tab):
        self.tab = tab
        self.rec = None
        self._dragging = False
        self._peek_after = None
        self._peek_token = 0

        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.canvas = tk.Canvas(self.frame, width=self.VIEW_W, height=self.VIEW_H,
                                 bg=T.INPUT, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", lambda e: self.toggle())

        self.engine = ClipPlayer(
            self.canvas, self.VIEW_W, self.VIEW_H,
            on_tick=self._on_tick, on_state=self._on_state, on_fail=self._on_fail)
        self.engine.loop = tab.cfg.player_loop
        self.engine.volume = max(0, min(int(tab.cfg.player_volume), 100))
        self.engine.muted = bool(tab.cfg.player_muted) or not HAS_FFPLAY

        self.bar = tk.Canvas(self.frame, height=20, bg=T.SURFACE,
                              highlightthickness=0, bd=0,
                              cursor="sb_h_double_arrow", width=self.VIEW_W)
        self.bar.pack(fill="x", pady=(4, 2))
        self.bar.bind("<Configure>", lambda e: self._draw_bar())
        self.bar.bind("<Button-1>", self._bar_press)
        self.bar.bind("<B1-Motion>", self._bar_press)
        self.bar.bind("<ButtonRelease-1>", self._bar_release)
        self.bar.bind("<Motion>", self._bar_hover)
        self.bar.bind("<Leave>", self._bar_leave)

        controls = ctk.CTkFrame(self.frame, fg_color="transparent")
        controls.pack(fill="x")

        def cbtn(text, cmd, width=42, color=T.DIM):
            b = ctk.CTkButton(controls, text=text, width=width, height=26,
                               corner_radius=6, font=font(10), fg_color=T.BTN,
                               hover_color=T.BTN_HOV, text_color=color,
                               command=cmd)
            b.pack(side="left", padx=(0, 5))
            return b

        self.play_btn = cbtn("▶ Play", self.toggle, 72, T.ACCENT)
        cbtn("-5s", lambda: self.nudge(-5), 40)
        cbtn("+5s", lambda: self.nudge(5), 40)
        self.loop_btn = cbtn("Loop", self.toggle_loop, 48,
                              T.ACCENT if self.engine.loop else T.DIM)
        self.speed_menu = ctk.CTkOptionMenu(
            controls, values=["0.5x", "1x", "1.5x", "2x"], width=64, height=26,
            font=font(10), corner_radius=6, fg_color=T.INPUT,
            button_color=T.LINE, button_hover_color=T.BTN_HOV,
            dropdown_fg_color=T.ELEVATED, text_color=T.TEXT,
            command=lambda v: setattr(self.engine, "speed", float(v.rstrip("x"))))
        self.speed_menu.set("1x")
        self.speed_menu.pack(side="left", padx=(0, 5))
        self.clock = ctk.CTkLabel(controls, text="", font=font(9, mono=True),
                                   text_color=T.DIM)
        self.clock.pack(side="left", padx=(4, 0))

        self.volume_slider = ctk.CTkSlider(
            controls, from_=0, to=100, number_of_steps=100, width=64,
            height=14, button_color=T.ACCENT, button_hover_color=T.ACCENT_HOV,
            progress_color=T.ACCENT, fg_color=T.LINE,
            command=self._on_volume_drag)
        self.volume_slider.set(0 if self.engine.muted else self.engine.volume)
        self.volume_slider.pack(side="right", padx=(2, 8))
        self.mute_btn = ctk.CTkButton(
            controls, text="🔇" if self.engine.muted else "🔊",
            width=28, height=26, corner_radius=6, font=font(11),
            fg_color="transparent", hover_color=T.BTN_HOV,
            text_color=T.FAINT if self.engine.muted else T.TEXT,
            state="normal" if HAS_FFPLAY else "disabled",
            command=self.toggle_mute)
        self.mute_btn.pack(side="right", padx=(4, 0))
        if not HAS_FFPLAY:
            self.volume_slider.configure(state="disabled")

        self._volume_job = None
        self._show_idle_text("Select a clip")

    # ── sizing ──────────────────────────────────────────────────────────

    def set_size(self, width: int, height: int = 0) -> None:
        width = max(int(width) // 2 * 2, 240)
        height = int(height) if height else int(width * 9 / 16) // 2 * 2
        height = max(height, 135)
        self.bar.configure(width=width)
        if self.rec is None:
            self.canvas.configure(width=width, height=height)
        self.engine.set_size(width, height)
        if self.rec is not None and not self.engine.playing:
            self._show_thumb(self.rec)
        self._draw_bar()

    # ── content switching ───────────────────────────────────────────────

    def show_rec(self, rec) -> None:
        """Selection changed: stop whatever is playing, show the new thumb."""
        self.engine.stop()
        self._peek_hide()
        self.rec = rec
        if rec is None:
            self.engine.clear()
            self._show_idle_text("Select a clip")
            self.clock.configure(text="")
            self._draw_bar()
            return
        self.engine.load(rec.path, rec.duration, rec.fps or 30.0)
        self.clock.configure(text=f"0:00.0 / {fmt_len(self.engine.duration)}")
        self._draw_bar()
        self._show_thumb(rec)

    def _show_idle_text(self, text: str):
        self.canvas.delete("all")
        self.canvas.create_text(self.engine.view_w // 2, self.engine.view_h // 2,
                                text=text, fill=T.FAINT, font=(T.UI, 11))

    def _show_thumb(self, rec):
        try:
            with open(os.path.join(THUMB_DIR, thumb_key(rec.path)), "rb") as fh:
                image = Image.open(io.BytesIO(fh.read()))
            image = fit_frame(image, self.engine.view_w, self.engine.view_h,
                              self.tab.cfg.thumb_fit)
            photo = ImageTk.PhotoImage(image)
            self.canvas.delete("all")
            self.canvas.create_image(self.engine.view_w // 2, self.engine.view_h // 2,
                                     image=photo, anchor="center")
            self.canvas.image = photo   # keep a reference
            self.canvas.create_text(self.engine.view_w // 2, self.engine.view_h - 14,
                                    text="▶ play", fill=T.TEXT, font=(T.UI, 9))
        except Exception:
            self._show_idle_text("no thumbnail")

    # ── transport ───────────────────────────────────────────────────────

    @property
    def playing(self) -> bool:
        return self.engine.playing

    @property
    def position(self) -> float:
        return self.engine.position

    def play(self) -> None:
        if self.rec is None:
            return
        self.engine.play()

    def pause(self) -> None:
        self.engine.pause()

    def toggle(self) -> None:
        if self.rec is None:
            return
        self.engine.toggle()

    def toggle_loop(self) -> None:
        loop = self.engine.toggle_loop()
        self.tab.cfg.player_loop = loop
        self.loop_btn.configure(text_color=T.ACCENT if loop else T.DIM)

    def nudge(self, seconds: float) -> None:
        if self.rec:
            self.engine.nudge(seconds)
            self._draw_bar()

    def toggle_mute(self) -> None:
        muted = self.engine.toggle_mute()
        self.tab.cfg.player_muted = muted
        self.tab.cfg.save()
        self.mute_btn.configure(text="🔇" if muted else "🔊",
                                text_color=T.FAINT if muted else T.TEXT)
        self.volume_slider.set(0 if muted else self.engine.volume)

    def _on_volume_drag(self, value):
        volume = max(0, min(int(round(float(value))), 100))
        if self.engine.muted and volume > 0:
            self.mute_btn.configure(text="🔊", text_color=T.TEXT)
        if self._volume_job is not None:
            try:
                self.canvas.after_cancel(self._volume_job)
            except ValueError:
                pass
        self._volume_job = self.canvas.after(220, lambda: self._commit_volume(volume))

    def _commit_volume(self, volume: int):
        self._volume_job = None
        self.engine.set_volume(volume)
        self.tab.cfg.player_volume = self.engine.volume
        self.tab.cfg.player_muted = self.engine.muted
        self.tab.cfg.save()

    # ── engine callbacks ────────────────────────────────────────────────

    def _on_state(self, playing: bool) -> None:
        self.play_btn.configure(text="⏸ Pause" if playing else "▶ Play")

    def _on_tick(self, position: float) -> None:
        self._draw_bar()
        self.clock.configure(text=f"{fmt_clock(position)} / {fmt_len(self.engine.duration)}")

    def _on_fail(self, message: str) -> None:
        self.tab.set_status(message, T.FAIL)

    # ── seek bar ────────────────────────────────────────────────────────

    def _draw_bar(self):
        c = self.bar
        c.delete("all")
        width = c.winfo_width()
        if width < 20:
            return
        y = 10
        c.create_line(2, y, width - 2, y, fill=T.LINE, width=4, capstyle="round")
        if self.engine.duration <= 0:
            return
        frac = max(0.0, min(self.engine.position / self.engine.duration, 1.0))
        px = 2 + frac * (width - 4)
        c.create_line(2, y, px, y, fill=T.ACCENT, width=4, capstyle="round")
        c.create_oval(px - 5, y - 5, px + 5, y + 5, fill=T.ACCENT_HOV, outline="")

    def _bar_press(self, event):
        if self.rec is None or self.engine.duration <= 0:
            return
        self._peek_hide()
        self._dragging = True
        width = max(self.bar.winfo_width() - 4, 1)
        frac = max(0.0, min((event.x - 2) / width, 1.0))
        self.engine.position = frac * self.engine.duration
        self._draw_bar()
        self.clock.configure(
            text=f"{fmt_clock(self.engine.position)} / {fmt_len(self.engine.duration)}")

    def _bar_release(self, _event):
        if self._dragging:
            self._dragging = False
            self.engine.seek(self.engine.position)

    # ── hover preview (same YouTube-style scrub bubble as the gallery) ────

    def _bar_hover(self, event):
        if self._dragging or self.rec is None or self.engine.duration <= 0:
            return
        if self._peek_after is not None:
            try:
                self.bar.after_cancel(self._peek_after)
            except ValueError:
                pass
        self._peek_after = self.bar.after(
            90, lambda: self._peek_fetch(event.x, event.x_root, event.y_root))

    def _bar_leave(self, _event=None):
        self._peek_hide()

    def _peek_fetch(self, x: int, x_root: int, y_root: int) -> None:
        self._peek_after = None
        rec = self.rec
        if rec is None or self.engine.duration <= 0:
            return
        width = max(self.bar.winfo_width() - 4, 1)
        frac = max(0.0, min((x - 2) / width, 1.0))
        moment = frac * self.engine.duration
        self._peek_token += 1
        token = self._peek_token

        def work():
            data = self.tab.frames.frame(rec.path, moment, self.tab.peek.W)
            if token != self._peek_token:
                return
            self.bar.after(0, lambda: self._peek_show(data, moment, token, x_root, y_root))

        threading.Thread(target=work, daemon=True).start()

    def _peek_show(self, data, moment: float, token: int, x_root: int, y_root: int) -> None:
        if token != self._peek_token or self.rec is None or self._dragging:
            return
        fraction = (moment / self.engine.duration) if self.engine.duration else None
        self.tab.peek.show_frame(data, self.rec.name, fmt_clock(moment), x_root, y_root,
                                 fraction=fraction)

    def _peek_hide(self) -> None:
        self._peek_token += 1
        if self._peek_after is not None:
            try:
                self.bar.after_cancel(self._peek_after)
            except ValueError:
                pass
            self._peek_after = None
        self.tab.peek.hide()
