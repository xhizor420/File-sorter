"""The embedded clip player for the Library tab.

Idle it shows the selected clip's thumbnail. Press Play and ffmpeg starts
decoding raw RGB frames straight into the same canvas - seek bar, loop,
speed and frame-accurate scrubbing, all without opening a second window. A
bounded queue keeps memory flat: when paused, ffmpeg blocks on its own pipe
and simply waits.

A second ffplay process plays the audio track alongside the silent
video-frame pipe, both started at the same position - not frame-accurate
lip sync, but real sound instead of none. Needs ffplay on PATH (ships with
ffmpeg); falls back to a muted, disabled volume control otherwise.
"""

from __future__ import annotations

import io
import os
import queue
import shutil
import subprocess
import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageFilter, ImageTk

from .theme import T, font
from .format import fmt_clock, fmt_len
from .files import NO_WINDOW, open_file
from .config import THUMB_DIR
from .media import fit_frame, read_exact, thumb_key

HAS_FFPLAY = shutil.which("ffplay") is not None


class InlinePlayer:
    # Starting geometry only. The player resizes with the window - on a 4K
    # screen a fixed small canvas is unreadably small next to 4K stills.
    VIEW_W, VIEW_H = 424, 238

    def __init__(self, parent, tab):
        self.tab = tab
        self.rec = None
        self.alive = True
        self.playing = False
        self.speed = 1.0
        self.loop = tab.cfg.player_loop
        self.position = 0.0
        self.duration = 0.0
        self.fps = 30.0
        self.stream_fps = 30.0
        self.proc = None
        self._token = 0
        self._queue: queue.Queue = queue.Queue(maxsize=6)
        self._after = None
        self._photo = None
        self._dragging = False
        self._waited = 0
        self._decoded = 0
        self.audio_proc = None
        self._volume_job = None
        self.volume = max(0, min(int(tab.cfg.player_volume), 100))
        self.muted = bool(tab.cfg.player_muted) or not HAS_FFPLAY
        self.VIEW_W = int(self.VIEW_W)
        self.VIEW_H = int(self.VIEW_H)
        self.frame_bytes = self.VIEW_W * self.VIEW_H * 3
        self._resize_job = None

        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.canvas = tk.Canvas(self.frame, width=self.VIEW_W, height=self.VIEW_H,
                                 bg=T.INPUT, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", lambda e: self.toggle())

        self.bar = tk.Canvas(self.frame, height=20, bg=T.SURFACE,
                              highlightthickness=0, bd=0,
                              cursor="sb_h_double_arrow", width=self.VIEW_W)
        self.bar.pack(fill="x", pady=(4, 2))
        self.bar.bind("<Configure>", lambda e: self._draw_bar())
        self.bar.bind("<Button-1>", self._bar_press)
        self.bar.bind("<B1-Motion>", self._bar_press)
        self.bar.bind("<ButtonRelease-1>", self._bar_release)

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
                              T.ACCENT if self.loop else T.DIM)
        self.speed_menu = ctk.CTkOptionMenu(
            controls, values=["0.5x", "1x", "1.5x", "2x"], width=64, height=26,
            font=font(10), corner_radius=6, fg_color=T.INPUT,
            button_color=T.LINE, button_hover_color=T.BTN_HOV,
            dropdown_fg_color=T.ELEVATED, text_color=T.TEXT,
            command=lambda v: setattr(self, "speed", float(v.rstrip("x"))))
        self.speed_menu.set("1x")
        self.speed_menu.pack(side="left", padx=(0, 5))
        self.clock = ctk.CTkLabel(controls, text="", font=font(9, mono=True),
                                   text_color=T.DIM)
        self.clock.pack(side="left", padx=(4, 0))
        ctk.CTkButton(controls, text="↗ VLC", width=56, height=26,
                      corner_radius=6, font=font(10), fg_color=T.BTN,
                      hover_color=T.BTN_HOV, text_color=T.ACCENT2,
                      command=lambda: open_file(self.rec.path)
                      if self.rec else None).pack(side="right")

        self.volume_slider = ctk.CTkSlider(
            controls, from_=0, to=100, number_of_steps=100, width=64,
            height=14, button_color=T.ACCENT, button_hover_color=T.ACCENT_HOV,
            progress_color=T.ACCENT, fg_color=T.LINE,
            command=self._on_volume_drag)
        self.volume_slider.set(0 if self.muted else self.volume)
        self.volume_slider.pack(side="right", padx=(2, 8))
        self.mute_btn = ctk.CTkButton(
            controls, text="🔇" if self.muted else "🔊",
            width=28, height=26, corner_radius=6, font=font(11),
            fg_color="transparent", hover_color=T.BTN_HOV,
            text_color=T.FAINT if self.muted else T.TEXT,
            state="normal" if HAS_FFPLAY else "disabled",
            command=self.toggle_mute)
        self.mute_btn.pack(side="right", padx=(4, 0))
        if not HAS_FFPLAY:
            self.volume_slider.configure(state="disabled")

        self._show_idle_text("Select a clip")

    def set_size(self, width: int, height: int = 0) -> None:
        width = max(int(width) // 2 * 2, 240)
        height = int(height) if height else int(width * 9 / 16) // 2 * 2
        height = max(height, 135)
        if width == self.VIEW_W and height == self.VIEW_H:
            return
        was_playing = self.playing
        position = self.position
        self.stop()
        self.VIEW_W, self.VIEW_H = width, height
        self.frame_bytes = width * height * 3
        self.canvas.configure(width=width, height=height)
        self.bar.configure(width=width)
        self.position = position
        if self.rec is not None:
            if was_playing:
                self.play()
            else:
                self._show_thumb(self.rec)
        self._draw_bar()

    def show_rec(self, rec) -> None:
        """Selection changed: stop whatever is playing, show the new thumb."""
        self.stop()
        self.rec = rec
        self.position = 0.0
        if rec is None:
            self._show_idle_text("Select a clip")
            self.clock.configure(text="")
            self._draw_bar()
            return
        self.duration = rec.duration
        self.fps = min(max(rec.fps or 30.0, 1.0), 60.0)
        self.clock.configure(text=f"0:00.0 / {fmt_len(self.duration)}")
        self._draw_bar()
        self._show_thumb(rec)

    def _show_idle_text(self, text: str):
        self.canvas.delete("all")
        self.canvas.create_text(self.VIEW_W // 2, self.VIEW_H // 2,
                                 text=text, fill=T.FAINT, font=(T.UI, 11))

    def _show_thumb(self, rec):
        try:
            with open(os.path.join(THUMB_DIR, thumb_key(rec.path)), "rb") as fh:
                image = Image.open(io.BytesIO(fh.read()))
            image = fit_frame(image, self.VIEW_W, self.VIEW_H,
                               self.tab.cfg.thumb_fit, blur=self.tab.cfg.discreet)
            self._photo = ImageTk.PhotoImage(image)
            self.canvas.delete("all")
            self.canvas.create_image(self.VIEW_W // 2, self.VIEW_H // 2,
                                      image=self._photo, anchor="center")
            self.canvas.create_text(self.VIEW_W // 2, self.VIEW_H - 14,
                                     text="▶ play", fill=T.TEXT, font=(T.UI, 9))
        except Exception:
            self._show_idle_text("no thumbnail")

    def _spawn(self, position: float):
        self._kill()
        self._spawn_audio(position)
        self._token += 1
        token = self._token
        self._queue = queue.Queue(maxsize=6)
        rate = max(min(self.fps, 30.0), 1.0)
        self.stream_fps = rate
        vf = (f"scale={self.VIEW_W}:{self.VIEW_H}:"
              f"force_original_aspect_ratio=decrease,"
              f"pad={self.VIEW_W}:{self.VIEW_H}:(ow-iw)/2:(oh-ih)/2,"
              f"fps={rate:.3f}")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        if position > 0.05:
            cmd += ["-ss", f"{position:.3f}"]
        cmd += ["-i", self.rec.path, "-an", "-sn", "-vf", vf,
                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        self._decoded = 0
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=0, creationflags=NO_WINDOW)
        except OSError:
            self.proc = None
            self._fail("ffmpeg not found - install it and add it to PATH")
            return False
        proc = self.proc

        def alive():
            return token == self._token

        def reader():
            frames = 0
            try:
                while alive():
                    chunk = read_exact(proc.stdout, self.frame_bytes, alive)
                    if chunk is None:
                        break
                    frames += 1
                    while alive():
                        try:
                            self._queue.put(chunk, timeout=0.2)
                            break
                        except queue.Full:
                            continue
            except (OSError, ValueError):
                pass
            finally:
                if alive():
                    try:
                        self._queue.put(None, timeout=0.5)
                        self._decoded = frames
                    except queue.Full:
                        pass

        import threading
        threading.Thread(target=reader, daemon=True).start()
        return True

    def _spawn_audio(self, position: float):
        self._kill_audio()
        if not HAS_FFPLAY or self.muted or self.volume <= 0:
            return
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
               "-vn", "-volume", str(self.volume)]
        if position > 0.05:
            cmd += ["-ss", f"{position:.3f}"]
        cmd += ["-i", self.rec.path]
        try:
            self.audio_proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        except OSError:
            self.audio_proc = None

    def _kill_audio(self):
        if self.audio_proc is not None:
            try:
                self.audio_proc.terminate()
                self.audio_proc.wait(timeout=1)
            except (OSError, subprocess.SubprocessError):
                try:
                    self.audio_proc.kill()
                except OSError:
                    pass
            self.audio_proc = None

    def toggle_mute(self):
        self.muted = not self.muted
        self.tab.cfg.player_muted = self.muted
        self.tab.cfg.save()
        self.mute_btn.configure(
            text="🔇" if self.muted else "🔊",
            text_color=T.FAINT if self.muted else T.TEXT)
        self.volume_slider.set(0 if self.muted else self.volume)
        if self.muted:
            self._kill_audio()
        elif self.playing:
            self._spawn_audio(self.position)

    def _on_volume_drag(self, value):
        self.volume = max(0, min(int(round(float(value))), 100))
        if self.muted and self.volume > 0:
            self.muted = False
            self.mute_btn.configure(text="🔊", text_color=T.TEXT)
        if self._volume_job is not None:
            try:
                self.canvas.after_cancel(self._volume_job)
            except ValueError:
                pass
        self._volume_job = self.canvas.after(220, self._commit_volume)

    def _commit_volume(self):
        self._volume_job = None
        self.tab.cfg.player_volume = self.volume
        self.tab.cfg.player_muted = self.muted
        self.tab.cfg.save()
        if self.volume <= 0:
            self._kill_audio()
        elif self.playing and not self.muted:
            self._spawn_audio(self.position)

    def _kill(self):
        self._kill_audio()
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1)
            except (OSError, subprocess.SubprocessError):
                try:
                    self.proc.kill()
                except OSError:
                    pass
            self.proc = None

    def _fail(self, message: str):
        self.playing = False
        self.play_btn.configure(text="▶ Play")
        self.canvas.delete("all")
        self.canvas.create_text(self.VIEW_W // 2, self.VIEW_H // 2,
                                 text=message, fill=T.FAIL, font=(T.UI, 10),
                                 width=self.VIEW_W - 40)
        self.tab.set_status(message, T.FAIL)

    def play(self):
        if self.rec is None:
            return
        if not os.path.exists(self.rec.path):
            self._fail("That file is no longer on disk.")
            return
        if self.proc is None:
            if self._spawn(self.position) is False:
                return
        elif self.audio_proc is None:
            self._spawn_audio(self.position)
        self.playing = True
        self.play_btn.configure(text="⏸ Pause")
        self._waited = 0
        self._schedule()

    def pause(self):
        self.playing = False
        self.play_btn.configure(text="▶ Play")
        self._kill_audio()
        self._cancel_tick()

    def stop(self):
        self.pause()
        self._token += 1
        self._kill()

    def toggle(self):
        self.pause() if self.playing else self.play()

    def toggle_loop(self):
        self.loop = not self.loop
        self.tab.cfg.player_loop = self.loop
        self.loop_btn.configure(text_color=T.ACCENT if self.loop else T.DIM)

    def nudge(self, seconds: float):
        if self.rec:
            self.seek(self.position + seconds)

    def seek(self, seconds: float):
        if self.rec is None:
            return
        limit = max(self.duration - 0.1, 0.0) if self.duration else seconds
        self.position = max(0.0, min(seconds, limit))
        self._draw_bar()
        self._waited = 0
        self._spawn(self.position)
        if self.playing:
            self._cancel_tick()
            self._schedule()

    def _cancel_tick(self):
        if self._after is not None:
            try:
                self.canvas.after_cancel(self._after)
            except ValueError:
                pass
            self._after = None

    def _schedule(self):
        if not self.playing:
            return
        interval = int(1000 / max(self.stream_fps * self.speed, 1))
        self._after = self.canvas.after(max(interval, 10), self._tick)

    def _tick(self):
        self._after = None
        if not self.playing:
            return
        try:
            chunk = self._queue.get_nowait()
        except queue.Empty:
            self._waited += 1
            if self._waited == 12:
                self.clock.configure(text="buffering…")
            if self._waited > 400:
                self._fail("Could not decode this file - try the VLC button")
                return
            self._schedule()
            return
        self._waited = 0
        if chunk is None:
            if self._decoded == 0:
                self._fail("No video stream could be decoded from this file")
                return
            if self.loop and self.rec is not None:
                self.position = 0.0
                self._spawn(0.0)
                self._schedule()
            else:
                self.pause()
                self.position = 0.0
                self._draw_bar()
            return
        self._blit(chunk)
        self.position += 1.0 / self.stream_fps
        if self.duration and self.position > self.duration:
            self.position = self.duration
        self._draw_bar()
        self.clock.configure(text=f"{fmt_clock(self.position)} / {fmt_len(self.duration)}")
        self._schedule()

    def _blit(self, chunk: bytes):
        try:
            image = Image.frombytes("RGB", (self.VIEW_W, self.VIEW_H), chunk)
            if self.tab.cfg.discreet:
                image = image.filter(ImageFilter.GaussianBlur(max(self.VIEW_W // 22, 10)))
            self._photo = ImageTk.PhotoImage(image)
        except Exception:
            return
        self.canvas.delete("all")
        self.canvas.create_image(self.VIEW_W // 2, self.VIEW_H // 2,
                                  image=self._photo, anchor="center")

    def _draw_bar(self):
        c = self.bar
        c.delete("all")
        width = c.winfo_width()
        if width < 20:
            return
        y = 10
        c.create_line(2, y, width - 2, y, fill=T.LINE, width=4, capstyle="round")
        if self.duration <= 0:
            return
        frac = max(0.0, min(self.position / self.duration, 1.0))
        px = 2 + frac * (width - 4)
        c.create_line(2, y, px, y, fill=T.ACCENT, width=4, capstyle="round")
        c.create_oval(px - 5, y - 5, px + 5, y + 5, fill=T.ACCENT_HOV, outline="")

    def _bar_press(self, event):
        if self.rec is None or self.duration <= 0:
            return
        self._dragging = True
        width = max(self.bar.winfo_width() - 4, 1)
        frac = max(0.0, min((event.x - 2) / width, 1.0))
        self.position = frac * self.duration
        self._draw_bar()
        self.clock.configure(text=f"{fmt_clock(self.position)} / {fmt_len(self.duration)}")

    def _bar_release(self, _event):
        if self._dragging:
            self._dragging = False
            self.seek(self.position)
