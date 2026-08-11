"""Beat/downbeat detection: audio extraction, the beat_this CLI, and the
blocking analyze() entry point. Pure logic - no widgets - so it can be
exercised (with the subprocess call stubbed) without a live Tk display.

This shells out to the `beat_this` command-line tool (from `pip install
beat-this`, https://github.com/CPJKU/beat_this) rather than importing its
Python API in-process. Two real reasons, not just style:

1. The README's Python-API example (`beats, downbeats = file2beats(...)`)
   and the package's own source (`inference.py`'s File2File.__call__,
   which unpacks the same call as `downbeats, beats = ...`) disagree with
   each other on argument order. Getting that backwards here would mean
   every real downbeat gets mislabeled as a plain beat and vice versa -
   exactly the kind of silent, hard-to-notice accuracy bug this feature
   exists to avoid. The CLI sidesteps the ambiguity entirely: it writes a
   plain `.beats` file via the package's own save_beat_tsv(), whose
   documented, unambiguous format ("time in seconds, tab, beat number -
   1 means downbeat") is verified directly against that function's source.
2. A subprocess can actually be killed. An in-process model call cannot be
   cancelled once started - Cancel would only ever mean "stop caring about
   the result," not "stop the work." Shelling out means Cancel can
   terminate the real OS process outright, mid-run, the same way the rest
   of this app's ffmpeg calls are already spawned and can be killed.

Also means this module never needs to `import torch` itself - installing
`beat-this` (which depends on torch) is enough, and BEATS_AVAILABLE is
just "is the beat_this command on PATH", the same shutil.which() check
already used for ffmpeg/ffprobe elsewhere in this codebase. Convert,
Library and Vault are completely unaffected either way.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

from .config import BEATS_AUDIO_CACHE_DIR, BEATS_MODEL_DIR
from .files import NO_WINDOW
from .media import probe

# Route beat-this's own checkpoint download through our cache dir instead of
# the user's shared ~/.cache/torch - same "everything lives under one
# config root" convention as THUMB_DIR/DB_PATH. setdefault(), not a plain
# assignment, so a TORCH_HOME the user has already set for other tools
# isn't silently overridden. Subprocesses inherit the parent's environment
# by default, so the beat_this CLI picks this up too.
os.environ.setdefault("TORCH_HOME", BEATS_MODEL_DIR)

BEATS_AVAILABLE = shutil.which("beat_this") is not None

# beat-this pulls torch in as its own dependency, so naming it separately
# here isn't required - it's still worth doing for the CPU-only path,
# where installing the small CPU wheel *first* stops beat-this's own
# install from pulling the much larger default CUDA build.
PIP_HINT = f"{sys.executable} -m pip install beat-this"
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


# ── beat_this CLI ────────────────────────────────────────────────────────

def _beats_cache_key(path: str, checkpoint: str, device: str) -> str:
    try:
        st = os.stat(path)
        stamp = f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        stamp = "0"
    raw = f"{os.path.normcase(os.path.abspath(path))}|{stamp}|{checkpoint}|{device}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest() + ".beats"


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def _run_beat_this(wav_path: str, out_path: str, checkpoint: str, force_cpu: bool,
                    cancel: threading.Event | None, timeout: float = 1800.0) -> bool:
    """Runs `beat_this <wav> -o <out> --model <checkpoint> --gpu <n>` as a
    real subprocess and polls for it to finish, checking `cancel` every
    200ms so a Cancel press can actually kill the process - a genuine
    abort, not just discarding a result we'd get anyway. `--gpu 0`
    (default) is safe on a machine with no GPU: beat_this's own device
    selection falls back to CPU automatically when CUDA isn't available,
    so this only forces CPU explicitly (`--gpu -1`) when the caller asked
    for it, rather than needing to detect GPU presence itself."""
    cmd = ["beat_this", wav_path, "--output", out_path, "--model", checkpoint,
           "--gpu", "-1" if force_cpu else "0"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except OSError:
        return False
    start = time.monotonic()
    while True:
        code = proc.poll()
        if code is not None:
            return code == 0 and os.path.exists(out_path)
        if cancel is not None and cancel.is_set():
            _terminate(proc)
            return False
        if time.monotonic() - start > timeout:
            _terminate(proc)
            return False
        time.sleep(0.2)


def _parse_beats_tsv(path: str):
    """Each line is "<time in seconds>\\t<beat number>" - beat number 1
    means that beat is a downbeat, per save_beat_tsv()'s own documented
    format. Returns (beats, downbeats), both lists of seconds."""
    beats: list = []
    downbeats: list = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.strip().split("\t")
                if len(parts) < 2:
                    continue
                try:
                    t = float(parts[0])
                    number = int(float(parts[1]))
                except ValueError:
                    continue
                beats.append(t)
                if number == 1:
                    downbeats.append(t)
    except OSError:
        return [], []
    return beats, downbeats


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
    missing dependency, extraction failure, cancellation, or any detection
    error; never raises to the caller.

    `cancel` is checked before extraction starts, and polled throughout
    the beat_this subprocess call itself - unlike a hypothetical in-process
    model call, this one can genuinely be killed mid-run.

    `on_stage`, if given, is called synchronously (from this thread, so a
    UI caller should marshal it back to the main thread itself) with one of
    "extracting" / "detecting" right before that phase starts. There's no
    separate "loading model" stage to report anymore - the CLI call does
    load + inference together as one opaque step from here.

    Both the extracted WAV and the detected beats/downbeats are cached on
    disk (the latter keyed by path+mtime+size+checkpoint+device), so
    re-analyzing the same clip with the same settings is instant after the
    first run - no repeat ffmpeg extraction or repeat beat_this call.
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

    device = "cpu" if force_cpu else "gpu"
    try:
        os.makedirs(BEATS_AUDIO_CACHE_DIR, exist_ok=True)
    except OSError:
        return None
    out_path = os.path.join(BEATS_AUDIO_CACHE_DIR,
                             _beats_cache_key(path, checkpoint, device))

    stage("detecting")
    if not os.path.exists(out_path):
        if not _run_beat_this(wav_path, out_path, checkpoint, force_cpu, cancel):
            return None
    if cancel is not None and cancel.is_set():
        return None

    beats, downbeats = _parse_beats_tsv(out_path)
    if not beats:
        return None

    info = probe(path)
    duration = info.duration if (info and info.duration) else beats[-1]

    return BeatResult(beats=beats, downbeats=downbeats, duration=duration,
                       checkpoint=checkpoint, device=device, source_path=path,
                       bpm_estimate=_estimate_bpm(beats))


def check_beats_dependencies() -> list:
    """Mirrors media.check_dependencies()'s shape. ffmpeg is already
    required by the rest of the app (should always pass, but confirming it
    here catches a broken PATH before it becomes a confusing failure deep
    inside an analysis run); beat_this is the optional piece."""
    return [t for t in ("ffmpeg", "beat_this") if shutil.which(t) is None]
