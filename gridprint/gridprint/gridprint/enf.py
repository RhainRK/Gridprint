"""Electrical Network Frequency (ENF) extraction.

Mains-powered equipment leaks a faint hum at the grid frequency (50 Hz in GB/Europe,
60 Hz in North America) and its harmonics into nearby recordings. The grid frequency
wanders by tens of millihertz from second to second, and that wander is the same
everywhere on one synchronous grid. Track it in a recording and you have a timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import gcd

import numpy as np
from scipy import signal

ANALYSIS_FS = 1000  # Hz; plenty for harmonics up to 180 Hz


def decimate(x: np.ndarray, sr: int, target: int = ANALYSIS_FS) -> tuple[np.ndarray, int]:
    if sr == target:
        return x, sr
    g = gcd(int(sr), target)
    return signal.resample_poly(x, target // g, int(sr) // g), target


# --------------------------------------------------------------------------- grid detection

def _band_snr_db(f: np.ndarray, p: np.ndarray, fc: float, peak_hw: float = 0.4, floor_hw: float = 3.0) -> float:
    peak = p[(f > fc - peak_hw) & (f < fc + peak_hw)]
    floor = p[(f > fc - floor_hw) & (f < fc + floor_hw) & ((f < fc - 1.0) | (f > fc + 1.0))]
    if peak.size == 0 or floor.size == 0:
        return float("-inf")
    return float(10 * np.log10(peak.max() / (np.median(floor) + 1e-30)))


def detect_grid(x: np.ndarray, sr: int) -> dict:
    """Which grid (50 or 60 Hz) left its hum here, and how strongly, per harmonic."""
    y, fs = decimate(x, sr)
    nper = min(len(y), 16 * fs)
    f, p = signal.welch(y, fs=fs, nperseg=nper, noverlap=nper // 2)
    out = {}
    for nominal in (50, 60):
        out[nominal] = {k: _band_snr_db(f, p, k * nominal) for k in (1, 2, 3)}
    best50 = max(out[50].values())
    best60 = max(out[60].values())
    nominal = 50 if best50 >= best60 else 60
    best = max(best50, best60)
    return {
        "nominal_hz": nominal if best >= 6.0 else None,
        "harmonic_snr_db": {str(k): round(v, 1) for k, v in out[nominal].items()},
        "other_grid_snr_db": {str(k): round(v, 1) for k, v in out[110 - nominal].items()},
        "best_snr_db": round(best, 1),
        "hum_detected": best >= 6.0,
    }


# --------------------------------------------------------------------------- frequency tracking

@dataclass
class ENFTrace:
    t: np.ndarray                 # seconds from start of recording (frame centres)
    f: np.ndarray                 # estimated grid frequency, Hz
    snr_db: np.ndarray            # per-frame quality
    nominal: int
    harmonics_used: list[int]
    harmonic_median_snr_db: dict
    hop_s: float
    frame_s: float
    spec: np.ndarray | None = field(default=None, repr=False)        # best-harmonic spectrogram
    spec_freqs: np.ndarray | None = field(default=None, repr=False)
    spec_harmonic: int = 1

    @property
    def deviation_mhz(self) -> np.ndarray:
        return (self.f - self.nominal) * 1000.0


def _quad_interp(spec: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Sub-bin peak offset via parabolic interpolation on log power."""
    i = np.clip(idx, 1, spec.shape[1] - 2)
    rows = np.arange(spec.shape[0])
    a = np.log(spec[rows, i - 1] + 1e-30)
    b = np.log(spec[rows, i] + 1e-30)
    c = np.log(spec[rows, i + 1] + 1e-30)
    denom = a - 2 * b + c
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.where(np.abs(denom) > 1e-12, 0.5 * (a - c) / denom, 0.0)
    return i + np.clip(d, -0.5, 0.5)


def extract_enf(
    x: np.ndarray,
    sr: int,
    nominal: int,
    harmonics: tuple[int, ...] = (1, 2, 3),
    frame_s: float = 8.0,
    hop_s: float = 1.0,
    min_harmonic_snr_db: float = 4.0,
) -> ENFTrace:
    y, fs = decimate(x, sr)
    frame, hop = int(frame_s * fs), int(hop_s * fs)
    if len(y) < frame + hop:
        raise ValueError("Recording is too short: ENF analysis needs at least ~10 seconds of audio.")
    win = signal.windows.hann(frame, sym=False)
    starts = np.arange(0, len(y) - frame + 1, hop)
    t = (starts + frame / 2) / fs
    m = 241

    per_h_f, per_h_snr, specs = {}, {}, {}
    for k in harmonics:
        hw = 0.6 * k
        f1, f2 = k * nominal - hw, k * nominal + hw
        zf = signal.ZoomFFT(frame, [f1, f2], m=m, fs=fs, endpoint=True)
        freqs = np.linspace(f1, f2, m)
        est = np.empty(len(starts))
        snr = np.empty(len(starts))
        spec_all = np.empty((len(starts), m), dtype=np.float32)
        for c0 in range(0, len(starts), 128):  # chunk to bound memory on long files
            sl = starts[c0:c0 + 128]
            frames = np.stack([y[s:s + frame] for s in sl]) * win
            sp = np.abs(zf(frames, axis=-1)) ** 2
            idx = sp.argmax(axis=1)
            fi = _quad_interp(sp, idx)
            est[c0:c0 + len(sl)] = (f1 + fi * (f2 - f1) / (m - 1)) / k
            snr[c0:c0 + len(sl)] = 10 * np.log10(sp.max(axis=1) / (np.median(sp, axis=1) + 1e-30))
            spec_all[c0:c0 + len(sl)] = sp
        per_h_f[k], per_h_snr[k], specs[k] = est, snr, (spec_all, freqs)

    med_snr = {k: float(np.median(per_h_snr[k])) for k in harmonics}
    used = [k for k in harmonics if med_snr[k] >= min_harmonic_snr_db] or [max(med_snr, key=med_snr.get)]

    F = np.stack([per_h_f[k] for k in used])                 # (H, T)
    W = np.stack([10 ** (per_h_snr[k] / 10) for k in used])  # linear SNR weights
    if len(used) > 1:  # drop a harmonic's estimate in frames where it disagrees with the others
        med = np.median(F, axis=0)
        W = np.where(np.abs(F - med) > 0.02, 0.0, W)
        W[:, W.sum(axis=0) == 0] = 1.0
    f_comb = (W * F).sum(axis=0) / W.sum(axis=0)
    f_comb = signal.medfilt(f_comb, 3)
    frame_snr = np.max(np.stack([per_h_snr[k] for k in used]), axis=0)

    best_k = max(used, key=lambda k: med_snr[k])
    return ENFTrace(
        t=t, f=f_comb, snr_db=frame_snr, nominal=nominal, harmonics_used=used,
        harmonic_median_snr_db={str(k): round(v, 1) for k, v in med_snr.items()},
        hop_s=hop_s, frame_s=frame_s,
        spec=specs[best_k][0], spec_freqs=specs[best_k][1], spec_harmonic=best_k,
    )


# --------------------------------------------------------------------------- phase discontinuities

def phase_jumps(
    x: np.ndarray, sr: int, nominal: int, harmonic: int = 1,
    gap_s: float = 0.6, win_s: float = 1.5, z_thresh: float = 5.0, min_step_rad: float = 0.5, edge_s: float = 3.0,
) -> list[dict]:
    """Find abrupt jumps in the phase of the hum: the signature of a cut or splice.

    A continuous recording carries a hum whose phase evolves smoothly. Cutting audio out
    and joining the ends almost always leaves a step in that phase. This check needs no
    grid reference data at all.
    """
    y, fs = decimate(x, sr)
    fc = harmonic * nominal
    hw = 0.9 * harmonic
    sos = signal.butter(4, [fc - hw, fc + hw], btype="band", fs=fs, output="sos")
    a = signal.hilbert(signal.sosfiltfilt(sos, y))
    t = np.arange(len(a)) / fs
    ph = np.unwrap(np.angle(a)) - 2 * np.pi * fc * t
    env = np.abs(a)

    dec = 50  # 20 Hz phase samples
    n = len(ph) // dec
    p = ph[: n * dec].reshape(n, dec).mean(axis=1)
    e = env[: n * dec].reshape(n, dec).mean(axis=1)
    tp = (np.arange(n) + 0.5) * dec / fs
    rate = fs / dec
    g, w = int(gap_s * rate), int(win_s * rate)
    if n < 2 * (g + w) + 10:
        return []

    # Linear fits left and right of each point (closed form via cumulative sums), evaluated at the point.
    idx = np.arange(n, dtype=np.float64)
    c = lambda v: np.concatenate([[0.0], np.cumsum(v)])
    S1, Sx, Sy, Sxx, Sxy = c(np.ones(n)), c(idx), c(p), c(idx * idx), c(idx * p)

    def fit_at(lo, hi, at):
        nn = S1[hi] - S1[lo]; sx = Sx[hi] - Sx[lo]; sy = Sy[hi] - Sy[lo]
        sxx = Sxx[hi] - Sxx[lo]; sxy = Sxy[hi] - Sxy[lo]
        den = nn * sxx - sx * sx
        b = (nn * sxy - sx * sy) / den
        a0 = (sy - b * sx) / nn
        return a0 + b * at

    i = np.arange(g + w, n - g - w)
    left = fit_at(i - g - w, i - g, i)
    right = fit_at(i + g, i + g + w, i)
    step = np.angle(np.exp(1j * (right - left)))  # wrap to (-pi, pi]
    mad = np.median(np.abs(step - np.median(step))) * 1.4826 + 1e-9
    z = np.abs(step - np.median(step)) / mad
    ok = (e[i] > 0.3 * np.median(e)) & (tp[i] > edge_s) & (tp[i] < tp[-1] - edge_s)  # ignore file edges

    cand = np.where((z > z_thresh) & (np.abs(step) > min_step_rad) & ok)[0]
    jumps, last = [], -1e9
    for j in cand[np.argsort(-z[cand])]:  # non-maximum suppression within 3 s
        tj = tp[i[j]]
        if all(abs(tj - q["t_s"]) > 3.0 for q in jumps):
            jumps.append({"t_s": round(float(tj), 2), "step_rad": round(float(step[j]), 2), "z": round(float(z[j]), 1)})
    return sorted(jumps, key=lambda q: q["t_s"])


def phase_jumps_multi(x: np.ndarray, sr: int, nominal: int, harmonics: list[int], radius_s: float = 2.5,
                      single_z: float = 9.0) -> list[dict]:
    """Run the phase check on every usable harmonic and merge detections that agree in time.

    A real cut shows up on several harmonics at once; noise rarely does. A detection counts if
    two or more harmonics agree (each z >= 5), or one harmonic is overwhelming (z >= 9).
    Time comes from the lowest harmonic (narrowest filter, sharpest localisation)."""
    det = []
    for k in sorted(harmonics):
        for j in phase_jumps(x, sr, nominal, k):
            det.append({**j, "harmonic": k})
    clusters: list[list[dict]] = []
    for d in sorted(det, key=lambda q: q["t_s"]):
        if clusters and d["t_s"] - clusters[-1][-1]["t_s"] <= radius_s:
            clusters[-1].append(d)
        else:
            clusters.append([d])
    out = []
    for c in clusters:
        ks = sorted({d["harmonic"] for d in c})
        low = min(c, key=lambda d: (d["harmonic"], -d["z"]))
        if len(ks) < 2 and max(d["z"] for d in c) < single_z:
            continue  # seen on one harmonic only and not overwhelming: treat as noise
        out.append({"t_s": low["t_s"], "harmonics_agreeing": ks, "max_z": max(d["z"] for d in c)})
    return out
