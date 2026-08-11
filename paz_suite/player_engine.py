"""Reusable clip-playback engine — the piece both tabs' players share.

Decodes raw RGB frames from ffmpeg into a caller-supplied canvas while a
second ffplay process plays the audio track alongside it, both started at
the same position (not frame-accurate lip sync, but real sound instead of
none). A bounded queue keeps memory flat: when paused, ffmpeg blocks on its
own pipe and simply waits.

This module owns none of the surrounding UI (buttons, seek bar, clock) —
callers wire it up via small callbacks (`on_tick`, `on_state`, `on_fail`,
`on_eof`) and read `.playing` / `.position` / `.duration` as needed. That
split is what lets the Convert tab's inspector and the Library tab's
viewer both get real play/pause/seek/volume without duplicating the
ffmpeg-pipe plumbing.
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading

from PIL import Image, ImageTk

from .files import NO_WINDOW
from .media import read_exact, has_ffplay

HAS_FFPLAY = has_ffplay()


class ClipPlayer:

    def __init__(self, canvas, width: int, height: int,
                 on_tick=None, on_state=None, on_fail=None, on_eof=None):
        self.canvas = canvas
        self.view_w = max(int(width) // 2 * 2, 240)
        self.view_h = max(int(height), 135)
        self.frame_bytes = self.view_w * self.view_h * 3
        self.on_tick = on_tick          # called(position) after each frame
        self.on_state = on_state        # called(playing: bool)
        self.on_fail = on_fail          # called(message)
        self.on_eof = on_eof            # called() — clip ended, not looping

        self.path: str | None = None
        self.duration = 0.0
        self.fps = 30.0
        self.stream_fps = 30.0
        self.position = 0.0
        self.playing = False
        self.speed = 1.0
        self.loop = True
        self.volume = 80
        self.muted = not HAS_FFPLAY

        self.proc = None
        self.audio_proc = None
        self._token = 0
        self._queue: queue.Queue = queue.Queue(maxsize=6)
        self._after = None
        self._photo = None
        self._canvas_item = None
        self._waited = 0
        self._decoded = 0

    # ── content ──────────────────────────────────────────────────────────

    def load(self, path: str, duration: float, fps: float) -> None:
        self.stop()
        self.path = path
        self.duration = max(duration, 0.0)
        self.fps = min(max(fps or 30.0, 1.0), 60.0)
        self.position = 0.0

    def clear(self) -> None:
        self.stop()
        self.path = None
        self.duration = 0.0
        self.position = 0.0
        self.canvas.delete("all")
        self._photo = None
        self._canvas_item = None

    def set_size(self, width: int, height: int) -> None:
        width = max(int(width) // 2 * 2, 240)
        height = max(int(height), 135)
        if width == self.view_w and height == self.view_h:
            return
        was_playing = self.playing
        position = self.position
        self.stop()
        self.view_w, self.view_h = width, height
        self.frame_bytes = width * height * 3
        self.canvas.configure(width=width, height=height)
        self.position = position
        if self.path and was_playing:
            self.play()

    # ── transport ────────────────────────────────────────────────────────

    def play(self) -> None:
        if not self.path:
            return
        if not os.path.exists(self.path):
            self._fail("That file is no longer on disk.")
            return
        if self.proc is None:
            if self._spawn(self.position) is False:
                return
        elif self.audio_proc is None:
            self._spawn_audio(self.position)
        self.playing = True
        if self.on_state:
            self.on_state(True)
        self._waited = 0
        self._schedule()

    def pause(self) -> None:
        self.playing = False
        if self.on_state:
            self.on_state(False)
        self._kill_audio()
        self._cancel_tick()

    def stop(self) -> None:
        self.pause()
        self._token += 1
        self._kill()

    def toggle(self) -> None:
        self.pause() if self.playing else self.play()

    def toggle_loop(self) -> bool:
        self.loop = not self.loop
        return self.loop

    def nudge(self, seconds: float) -> None:
        if self.path:
            self.seek(self.position + seconds)

    def seek(self, seconds: float) -> None:
        if not self.path:
            return
        limit = max(self.duration - 0.1, 0.0) if self.duration else seconds
        self.position = max(0.0, min(seconds, limit))
        was_playing = self.playing
        self._spawn(self.position)
        if was_playing:
            self.playing = True
            self._cancel_tick()
            self._schedule()

    def toggle_mute(self) -> bool:
        self.muted = not self.muted
        if self.muted:
            self._kill_audio()
        elif self.playing:
            self._spawn_audio(self.position)
        return self.muted

    def set_volume(self, value: int) -> None:
        self.volume = max(0, min(int(value), 100))
        if self.muted and self.volume > 0:
            self.muted = False
        if self.volume <= 0:
            self._kill_audio()
        elif self.playing and not self.muted:
            self._spawn_audio(self.position)

    # ── decoding ────────────────────────────────────────────────────────

    def _spawn(self, position: float):
        self._kill()
        self._spawn_audio(position)
        self._token += 1
        token = self._token
        self._queue = queue.Queue(maxsize=6)
        # The whole point of the pool is 4K/60 - capping decode below the
        # source rate here was making 60fps footage play back at half its
        # actual smoothness. self.fps is already clamped to 60 in load().
        rate = max(min(self.fps, 60.0), 1.0)
        self.stream_fps = rate
        vf = (f"scale={self.view_w}:{self.view_h}:"
              f"force_original_aspect_ratio=decrease,"
              f"pad={self.view_w}:{self.view_h}:(ow-iw)/2:(oh-ih)/2,"
              f"fps={rate:.3f}")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        if position > 0.05:
            cmd += ["-ss", f"{position:.3f}"]
        cmd += ["-i", self.path, "-an", "-sn", "-vf", vf,
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

        threading.Thread(target=reader, daemon=True).start()
        return True

    def _spawn_audio(self, position: float):
        self._kill_audio()
        if not HAS_FFPLAY or self.muted or self.volume <= 0 or not self.path:
            return
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
               "-vn", "-volume", str(self.volume)]
        if position > 0.05:
            cmd += ["-ss", f"{position:.3f}"]
        cmd += ["-i", self.path]
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
        if self.on_state:
            self.on_state(False)
        self.canvas.delete("all")
        self._photo = None
        self._canvas_item = None
        self.canvas.create_text(self.view_w // 2, self.view_h // 2,
                                text=message, fill="#FF5C6E", font=("Segoe UI", 10),
                                width=self.view_w - 40)
        if self.on_fail:
            self.on_fail(message)

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
            if self._waited > 400:
                self._fail("Could not decode this file")
                return
            self._schedule()
            return
        self._waited = 0
        if chunk is None:
            if self._decoded == 0:
                self._fail("No video stream could be decoded from this file")
                return
            if self.loop and self.path is not None:
                self.position = 0.0
                self._spawn(0.0)
                self._schedule()
            else:
                self.pause()
                self.position = 0.0
                if self.on_tick:
                    self.on_tick(self.position)
                if self.on_eof:
                    self.on_eof()
            return
        self._blit(chunk)
        self.position += 1.0 / self.stream_fps
        if self.duration and self.position > self.duration:
            self.position = self.duration
        if self.on_tick:
            self.on_tick(self.position)
        self._schedule()

    def _blit(self, chunk: bytes):
        try:
            image = Image.frombytes("RGB", (self.view_w, self.view_h), chunk)
        except Exception:
            return
        # Recreating the PhotoImage and canvas item every frame (the old
        # delete("all") + create_image approach) is the single biggest cost
        # in this loop at 60fps - Tk has to re-register a whole new image
        # each time. Painting into one persistent PhotoImage via .paste()
        # and reusing one canvas item is dramatically cheaper, and is what
        # actually makes 60fps playback keep up instead of falling behind.
        if (self._photo is None or self._canvas_item is None
                or self._photo.width() != self.view_w
                or self._photo.height() != self.view_h):
            self._photo = ImageTk.PhotoImage(image)
            self.canvas.delete("all")
            self._canvas_item = self.canvas.create_image(
                self.view_w // 2, self.view_h // 2,
                image=self._photo, anchor="center")
        else:
            self._photo.paste(image)
