"""Beat/downbeat detection: audio extraction, the optional beat-this model,
and the blocking analyze() entry point. Pure logic - no widgets - so it can
be exercised (with the model call mocked) without a live Tk display.

torch and beat-this are a genuinely heavy, optional install (a few hundred
MB to ~2GB depending on CPU/GPU build) that the rest of the suite has no
need for, so the import happens once, here, guarded - BEATS_AVAILABLE tells
every other module (chiefly beats_tab.py) whether the real thing is usable
without any of them ever importing torch themselves. Convert/Library/Vault
are completely unaffected whether or not this succeeds.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field

from .config import BEATS_AUDIO_CACHE_DIR, BEATS_MODEL_DIR
from .files import NO_WINDOW
from .media import probe

# Route beat-this's own checkpoint download through our cache dir instead of
# the user's shared ~/.cache/torch - same "everything lives under one
# config root" convention as THUMB_DIR/DB_PATH. setdefault(), not a plain
# assignment, so a TORCH_HOME the user has already set for other tools
# isn't silently overridden.
os.environ.setdefault("TORCH_HOME", BEATS_MODEL_DIR)

try:
    import torch
    from beat_this.inference import File2Beats
    BEATS_AVAILABLE = True
    _IMPORT_ERROR = ""
except Exception as exc:  # noqa: BLE001 - deliberately broad: a partially
    # broken torch install (e.g. a mismatched CUDA DLL) can raise things
    # other than ModuleNotFoundError, and none of them should propagate up
    # through app.py's tab construction and take the whole app down.
    torch = None
    File2Beats = None
    BEATS_AVAILABLE = False
    _IMPORT_ERROR = str(exc)

PIP_HINT = f"{sys.executable} -m pip install torch beat-this"
PIP_HINT_CPU = (f"{sys.executable} -m pip install torch "
                "--index-url https://download.pytorch.org/whl/cpu && "
                f"{sys.executable} -m pip install beat-this")

CHECKPOINTS = ("final0", "small0")


@dataclass
class BeatResult:
    beats: list = field(default_factory=list)        # seconds
    downbeats: list = field(default_factory=list)     # seconds
    duration: float = 0.0
    checkpoint: str = ""
    device: str = ""
    source_path: str = ""
    bpm_estimate: float | None = None


# ── audio extraction ────────────────────────────────────────────────────
#
# No audio-only extraction exists elsewhere in the codebase (media.py only
# pulls still frames). beat-this resamples everything to 22kHz mono
# internally regardless of input, so extracting straight to that avoids
# wasted work inside the model and keeps the cache small. Cached on disk
# keyed by path+mtime+size (the same scheme ThumbCache uses) so re-running
# analysis on a clip you've already looked at never re-extracts.

def _audio_cache_key(path: str) -> str:
    try:
        st = os.stat(path)
        stamp = f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        stamp = "0"
    raw = f"{os.path.normcase(os.path.abspath(path))}|{stamp}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest() + ".wav"


def extract_audio_wav(path: str) -> str | None:
    """Mono/22050Hz/16-bit PCM WAV, cached on disk. None on any failure -
    extraction never raises, matching media.py's ThumbCache convention
    (subprocess.run wrapped in try/except, missing input just means None)."""
    if not path or not os.path.exists(path):
        return None
    try:
        os.makedirs(BEATS_AUDIO_CACHE_DIR, exist_ok=True)
    except OSError:
        return None
    dest = os.path.join(BEATS_AUDIO_CACHE_DIR, _audio_cache_key(path))
    if os.path.exists(dest):
        return dest
    tmp = dest + ".part"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", path,
           "-vn", "-ac", "1", "-ar", "22050", "-sample_fmt", "s16",
           "-f", "wav", tmp]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=180, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass
    if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        try:
            # Atomic rename so a reader never sees a half-written file -
            # same habit as ThumbCache's on-disk cache writes.
            os.replace(tmp, dest)
            return dest
        except OSError:
            return None
    try:
        os.remove(tmp)
    except OSError:
        pass
    return None


# ── model ────────────────────────────────────────────────────────────────

_model_lock = threading.Lock()
_model_cache: dict = {}   # (checkpoint, device) -> File2Beats instance


def _get_model(checkpoint: str, device: str):
    """Construct-once, reuse-after. The first call for a given
    (checkpoint, device) pair may trigger beat-this's own checkpoint
    download (~78MB for final0, ~8MB for small0); every call after that in
    this process is instant. Held under one lock rather than per-key
    double-checked locking - analysis is already serialized by the tab's
    own busy-guard, so the extra contention this could theoretically cause
    never actually happens in practice."""
    key = (checkpoint, device)
    with _model_lock:
        model = _model_cache.get(key)
        if model is None:
            model = File2Beats(checkpoint_path=checkpoint, device=device)
            _model_cache[key] = model
        return model


def _estimate_bpm(beats: list) -> float | None:
    """Median of consecutive beat-interval-derived BPMs - a convenience
    number for display, not a separate model call."""
    diffs = sorted(b - a for a, b in zip(beats, beats[1:]) if b > a)
    if not diffs:
        return None
    mid = len(diffs) // 2
    median = diffs[mid] if len(diffs) % 2 else (diffs[mid - 1] + diffs[mid]) / 2
    return 60.0 / median if median > 0 else None


# ── analysis ─────────────────────────────────────────────────────────────

def analyze(path: str, checkpoint: str = "final0", force_cpu: bool = False,
            cancel: threading.Event | None = None, on_stage=None) -> BeatResult | None:
    """Blocking - always call this off the UI thread. Returns None on a
    missing dependency, extraction failure, cancellation, or any inference
    error; never raises to the caller.

    `cancel` is checked between phases (before extraction, before model
    load, before inference) so a Cancel press stops the job promptly
    between steps - it cannot interrupt a checkpoint download or the
    inference call itself mid-flight, since neither exposes a cooperative
    cancel hook. That's a real limitation, not hidden: the caller should
    show an honest "downloading model..." status so the wait is expected.

    `on_stage`, if given, is called synchronously (from this thread, so a
    UI caller should marshal it back to the main thread itself) with one of
    "extracting" / "loading_model" / "detecting" right before that phase
    starts - real stage boundaries, not a guessed/animated progress bar.
    """
    def stage(name: str) -> None:
        if on_stage is not None:
            on_stage(name)

    if not BEATS_AVAILABLE:
        return None
    if cancel is not None and cancel.is_set():
        return None

    stage("extracting")
    wav_path = extract_audio_wav(path)
    if wav_path is None:
        return None
    if cancel is not None and cancel.is_set():
        return None

    device = "cpu" if (force_cpu or not torch.cuda.is_available()) else "cuda"
    stage("loading_model")
    try:
        model = _get_model(checkpoint, device)
    except Exception:
        return None
    if cancel is not None and cancel.is_set():
        return None

    stage("detecting")
    try:
        raw_beats, raw_downbeats = model(wav_path)
    except Exception:
        return None

    beats = [float(b) for b in raw_beats]
    downbeats = [float(d) for d in raw_downbeats]
    info = probe(path)
    duration = info.duration if (info and info.duration) else (beats[-1] if beats else 0.0)

    return BeatResult(beats=beats, downbeats=downbeats, duration=duration,
                       checkpoint=checkpoint, device=device, source_path=path,
                       bpm_estimate=_estimate_bpm(beats))


def check_beats_dependencies() -> list:
    """Mirrors media.check_dependencies()'s shape. Audio extraction needs
    ffmpeg - already required by the rest of the app, so this should
    always pass, but confirming it here (like Convert's own environment
    check) catches a broken PATH before it becomes a confusing failure
    deep inside an analysis run."""
    return [t for t in ("ffmpeg",) if shutil.which(t) is None]
