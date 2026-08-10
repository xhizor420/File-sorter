"""Media probing, frame extraction and thumbnailing — one ffprobe/ffmpeg
layer shared by the encoder, the scrub preview, the gallery and the
duplicate finder.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter

from .config import THUMB_DIR
from .files import NO_WINDOW


# ─────────────────────────────────────────────────────────────────────────
#  Probing
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class MediaInfo:
    width: int = 0
    height: int = 0
    fps: float = 0.0
    rfps: float = 0.0            # container frame rate; differs when VFR
    duration: float = 0.0
    size: int = 0
    vcodec: str = ""
    acodec: str = ""
    bitrate: int = 0

    @property
    def has_audio(self) -> bool:
        return bool(self.acodec)

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}" if self.width else "--"

    @property
    def vfr(self) -> bool:
        return bool(self.fps and self.rfps and abs(self.fps - self.rfps) > 0.5)

    @property
    def fps_text(self) -> str:
        return f"{self.fps:.2f}".rstrip("0").rstrip(".") if self.fps else "--"


_probe_cache: dict = {}
_probe_lock = threading.Lock()


def _parse_rate(value: str) -> float:
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den = float(den)
            return float(num) / den if den else 0.0
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def probe(path: str, use_cache: bool = True) -> MediaInfo | None:
    """One ffprobe call for everything we need. Cached on path + mtime + size."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (os.path.normcase(path), st.st_mtime_ns, st.st_size)

    if use_cache:
        with _probe_lock:
            hit = _probe_cache.get(key)
        if hit is not None:
            return hit

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=45, creationflags=NO_WINDOW,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None

    info = MediaInfo(size=st.st_size)
    fmt = data.get("format", {})
    try:
        info.duration = float(fmt.get("duration", 0) or 0)
    except (TypeError, ValueError):
        info.duration = 0.0
    try:
        info.bitrate = int(fmt.get("bit_rate", 0) or 0)
    except (TypeError, ValueError):
        info.bitrate = 0

    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and not info.width:
            info.width = int(stream.get("width", 0) or 0)
            info.height = int(stream.get("height", 0) or 0)
            info.vcodec = stream.get("codec_name", "")
            avg = _parse_rate(stream.get("avg_frame_rate", "0/0"))
            container = _parse_rate(stream.get("r_frame_rate", "0/0"))
            info.fps = avg or container
            info.rfps = container
            if not info.duration:
                try:
                    info.duration = float(stream.get("duration", 0) or 0)
                except (TypeError, ValueError):
                    pass
        elif kind == "audio" and not info.acodec:
            info.acodec = stream.get("codec_name", "")

    with _probe_lock:
        _probe_cache[key] = info
        if len(_probe_cache) > 8000:
            _probe_cache.clear()
    return info


def check_dependencies() -> list:
    return [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]


def has_ffplay() -> bool:
    return shutil.which("ffplay") is not None


_encoder_cache: set | None = None


def available_encoders() -> set:
    """Ask ffmpeg once which encoders this build actually has."""
    global _encoder_cache
    if _encoder_cache is not None:
        return _encoder_cache
    names = set()
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20, creationflags=NO_WINDOW,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and len(parts[0]) == 6:
                names.add(parts[1])
    except (OSError, subprocess.SubprocessError):
        pass
    _encoder_cache = names
    return names


# ─────────────────────────────────────────────────────────────────────────
#  Frame compositing
# ─────────────────────────────────────────────────────────────────────────

def fit_frame(image, box_w: int, box_h: int, mode: str = "contain",
              blur: bool = False):
    """
    Return an image of exactly box_w x box_h.

    "contain" scales the frame to fit entirely inside the tile, filling the
    leftover letterbox with a blurred, zoomed copy of the same frame instead
    of dead black - portrait, ultrawide and square clips all land intact.
    "cover" crops to fill for anyone who prefers edge-to-edge tiles.
    """
    box_w = max(int(box_w), 1)
    box_h = max(int(box_h), 1)
    source = image.convert("RGB")
    src_w, src_h = source.size
    if not src_w or not src_h:
        return Image.new("RGB", (box_w, box_h), (15, 9, 23))

    scale_cover = max(box_w / src_w, box_h / src_h)
    if mode == "cover":
        wide = source.resize((max(int(src_w * scale_cover), box_w),
                               max(int(src_h * scale_cover), box_h)),
                              Image.LANCZOS)
        left = (wide.width - box_w) // 2
        top = (wide.height - box_h) // 2
        canvas = wide.crop((left, top, left + box_w, top + box_h))
        return canvas.filter(ImageFilter.GaussianBlur(9)) if blur else canvas

    backdrop = source.resize((max(int(src_w * scale_cover), box_w),
                               max(int(src_h * scale_cover), box_h)),
                              Image.BILINEAR)
    left = (backdrop.width - box_w) // 2
    top = (backdrop.height - box_h) // 2
    canvas = backdrop.crop((left, top, left + box_w, top + box_h))
    canvas = canvas.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.55))

    scale_fit = min(box_w / src_w, box_h / src_h)
    inner = source.resize((max(int(src_w * scale_fit), 1),
                            max(int(src_h * scale_fit), 1)), Image.LANCZOS)
    canvas.paste(inner, ((box_w - inner.width) // 2, (box_h - inner.height) // 2))
    return canvas.filter(ImageFilter.GaussianBlur(9)) if blur else canvas


def round_corners(image, radius: int, bg: str = "#161021"):
    """Give a frame softly rounded corners against the gallery surface."""
    rgb = tuple(int(bg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
    base = Image.new("RGB", image.size, rgb)
    base.paste(image, (0, 0), mask)
    return base


def read_exact(stream, count: int, alive) -> bytes | None:
    """
    Read exactly `count` bytes from a pipe.

    A pipe hands back whatever happens to be buffered - typically 64 KB -
    so a single read() of a 300 KB video frame ALWAYS comes up short. Loop
    until the frame is whole, or the stream genuinely ends.
    """
    chunks = []
    remaining = count
    while remaining > 0:
        if not alive():
            return None
        try:
            piece = stream.read(remaining)
        except (OSError, ValueError):
            return None
        if not piece:
            return None
        chunks.append(piece)
        remaining -= len(piece)
    return b"".join(chunks)


# ─────────────────────────────────────────────────────────────────────────
#  Thumbnail / frame cache
# ─────────────────────────────────────────────────────────────────────────

class ThumbCache:
    """On-disk JPEG cache so scrubbing back over a frame is instant.

    Shared by the Convert inspector, the gallery's hover-scrub, the contact
    sheet and the duplicate finder - one cache instead of two nearly
    identical ones.
    """

    def __init__(self, limit: int = 4000, subdir: str = "paz_frames"):
        self.root = os.path.join(tempfile.gettempdir(), subdir)
        self.limit = limit
        self._lock = threading.Lock()
        self._count = 0
        try:
            os.makedirs(self.root, exist_ok=True)
        except OSError:
            self.root = ""

    def _key(self, path: str, pos: float, width: int) -> str:
        try:
            st = os.stat(path)
            stamp = f"{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            stamp = "0"
        raw = f"{os.path.normcase(path)}|{stamp}|{pos:.2f}|{width}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest() + ".jpg"

    def frame(self, path: str, pos: float, width: int = 640) -> bytes | None:
        """Return JPEG bytes for the frame at `pos` seconds, extracting if needed."""
        if not os.path.exists(path):
            return None

        cached = None
        if self.root:
            cached = os.path.join(self.root, self._key(path, pos, width))
            if os.path.exists(cached):
                try:
                    with open(cached, "rb") as fh:
                        return fh.read()
                except OSError:
                    pass

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
        os.close(tmp_fd)
        try:
            attempts = []
            if pos > 0.05:
                attempts.append(["ffmpeg", "-y", "-ss", f"{pos:.3f}", "-i", path])
                attempts.append(["ffmpeg", "-y", "-i", path, "-ss", f"{pos:.3f}"])
            attempts.append(["ffmpeg", "-y", "-i", path])

            for head in attempts:
                cmd = head + [
                    "-frames:v", "1",
                    "-vf", f"scale={width}:-2:flags=bicubic",
                    "-q:v", "3", "-f", "image2", tmp_path,
                ]
                try:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=20,
                                   creationflags=NO_WINDOW)
                except (OSError, subprocess.SubprocessError):
                    continue
                if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                    with open(tmp_path, "rb") as fh:
                        data = fh.read()
                    if cached:
                        self._store(cached, data)
                    return data
            return None
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def _store(self, dest: str, data: bytes) -> None:
        try:
            with open(dest, "wb") as fh:
                fh.write(data)
        except OSError:
            return
        with self._lock:
            self._count += 1
            if self._count > self.limit:
                self._count = 0
                self.trim()

    def trim(self) -> None:
        """Drop the oldest half when the cache grows past the limit."""
        try:
            entries = [(os.path.getmtime(os.path.join(self.root, n)),
                        os.path.join(self.root, n))
                       for n in os.listdir(self.root)]
        except OSError:
            return
        if len(entries) <= self.limit:
            return
        entries.sort()
        for _, path in entries[: len(entries) // 2]:
            try:
                os.remove(path)
            except OSError:
                pass


# ─────────────────────────────────────────────────────────────────────────
#  Gallery thumbnails (persistent, one per library clip)
# ─────────────────────────────────────────────────────────────────────────

def thumb_key(path: str) -> str:
    return hashlib.sha1(os.path.normcase(path).encode("utf-8")).hexdigest() + ".jpg"


def _looks_blank(path: str) -> bool:
    """True for a frame that is essentially one flat colour (fade / black)."""
    try:
        with Image.open(path) as image:
            small = image.convert("L").resize((32, 32), Image.BILINEAR)
        pixels = list(small.getdata())
        low = min(pixels)
        high = max(pixels)
        mean = sum(pixels) / len(pixels)
        return (high - low) < 18 or mean < 12
    except Exception:
        return False


def make_thumb(path: str, duration: float, width: int) -> bool:
    """
    Write a representative gallery thumbnail into the persistent thumb dir.

    Several clips opened on a fade or a title card, which produced black
    tiles. Candidate positions are tried in turn and a frame that is
    basically one flat colour is rejected, so the grid shows the actual
    content rather than the intro.
    """
    try:
        os.makedirs(THUMB_DIR, exist_ok=True)
    except OSError:
        return False
    dest = os.path.join(THUMB_DIR, thumb_key(path))

    spots = [duration * f for f in (0.35, 0.55, 0.20, 0.72, 0.05)] if duration > 1 else [0.0]

    fallback_written = False
    for index, pos in enumerate(spots):
        heads = []
        if pos > 0.05:
            heads.append(["ffmpeg", "-y", "-ss", f"{pos:.3f}", "-i", path])
        heads.append(["ffmpeg", "-y", "-i", path])
        for head in heads:
            cmd = head + ["-frames:v", "1",
                          "-vf", f"scale={width}:-2:flags=bicubic",
                          "-q:v", "3", "-f", "image2", dest]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=25,
                               creationflags=NO_WINDOW)
            except (OSError, subprocess.SubprocessError):
                continue
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                fallback_written = True
                if index == len(spots) - 1 or not _looks_blank(dest):
                    return True
                break
    return fallback_written


# ─────────────────────────────────────────────────────────────────────────
#  Perceptual hashing (duplicate finder)
# ─────────────────────────────────────────────────────────────────────────

def dhash(image) -> int:
    """64-bit difference hash for near-duplicate detection."""
    gray = image.convert("L").resize((9, 8), Image.LANCZOS)
    pixels = list(gray.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")
