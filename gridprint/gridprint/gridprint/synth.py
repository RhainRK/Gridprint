"""Build test recordings with known ground truth.

This is the standard evaluation set-up in the ENF literature: take the grid's recorded
frequency for a known window, synthesise the hum it would have produced, and bury it in
speech-band noise (or your own voice) at a realistic level. The detector never sees the
ground truth.
"""
from __future__ import annotations

import numpy as np
from scipy import signal


def speechlike(n: int, sr: int, rng: np.random.Generator) -> np.ndarray:
    """Babble: pink-ish noise in the speech band, gated by a syllable-rate envelope with pauses."""
    w = rng.normal(size=n)
    sos = signal.butter(4, [250, min(3800, sr / 2 - 100)], btype="band", fs=sr, output="sos")
    b = signal.sosfilt(sos, w)
    env = np.abs(signal.sosfilt(signal.butter(2, 4, fs=sr, output="sos"), rng.normal(size=n)))
    env /= env.max() + 1e-12
    gate = np.repeat(rng.random(n // sr + 2) > 0.2, sr)[:n]  # occasional one-second pauses
    gate = signal.sosfiltfilt(signal.butter(2, 3, fs=sr, output="sos"), gate.astype(float))
    return b * env * np.clip(gate, 0, 1)


def hum_from_reference(dev_hz: np.ndarray, step_s: int, n: int, sr: int, nominal: int,
                       amps=(0.3, 1.0, 0.6), rng: np.random.Generator | None = None) -> np.ndarray:
    rng = rng or np.random.default_rng(0)
    t = np.arange(n) / sr
    tr = np.arange(len(dev_hz)) * step_s
    f = nominal + np.interp(t, tr, dev_hz)
    phase = 2 * np.pi * np.cumsum(f) / sr
    return sum(a * np.sin((k + 1) * phase + rng.uniform(0, 2 * np.pi)) for k, a in enumerate(amps))


def make_recording(dev_hz: np.ndarray, step_s: int, seconds: float, sr: int = 16000, nominal: int = 50,
                   hum_rel_db: float = -26.0, seed: int = 0, voice: np.ndarray | None = None,
                   highpass_hz: float = 70.0) -> np.ndarray:
    """dev_hz must start at the recording's true start time and cover `seconds`."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    speech = speechlike(n, sr, rng) if voice is None else np.resize(voice, n)
    hum = hum_from_reference(dev_hz, step_s, n, sr, nominal, rng=rng)
    rms = lambda v: np.sqrt(np.mean(v ** 2)) + 1e-12
    x = speech / rms(speech) + 10 ** (hum_rel_db / 20) * hum / rms(hum)
    x += rng.normal(0, 10 ** (-38 / 20), n)  # room/mic noise floor
    x = signal.sosfilt(signal.butter(2, highpass_hz, btype="high", fs=sr, output="sos"), x)  # phone-style HPF
    return 0.6 * x / np.max(np.abs(x))


def to_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(x * 32767, -32768, 32767).astype(np.int16)
