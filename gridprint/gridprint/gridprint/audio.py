"""Audio loading. WAV via scipy; anything else (mp3, m4a, ogg, opus, webm) via ffmpeg."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import numpy as np
from scipy.io import wavfile


def _to_mono_float(x: np.ndarray) -> np.ndarray:
    if x.dtype.kind == "i":
        x = x.astype(np.float64) / np.iinfo(x.dtype).max
    elif x.dtype.kind == "u":
        x = (x.astype(np.float64) - 128.0) / 128.0
    else:
        x = x.astype(np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1)
    return x


def load_audio(path: str | Path, target_sr: int | None = None) -> tuple[np.ndarray, int]:
    """Return (mono float64 samples, sample rate)."""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        try:
            sr, x = wavfile.read(path)
            return _to_mono_float(x), int(sr)
        except ValueError:
            pass  # odd WAV flavour: fall through to ffmpeg
    if shutil.which("ffmpeg") is None:
        raise ValueError("Reading this kind of file needs ffmpeg, which isn’t installed on this computer. "
                         "Use a WAV file, or install ffmpeg (Windows: winget install ffmpeg).")
    sr = target_sr or 16000
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    try:
        raw = subprocess.run(cmd, check=True, capture_output=True, timeout=120).stdout
    except subprocess.CalledProcessError:
        raise ValueError("That file couldn’t be read as audio. Try a WAV, MP3 or M4A file.") from None
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32767.0
    return x, sr


def sha256_file(path: str | Path) -> str:
    """Chain-of-custody hash of the exact file analysed."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
