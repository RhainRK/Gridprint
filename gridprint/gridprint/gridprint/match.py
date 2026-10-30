"""Match a recording's ENF trace against grid reference data.

Statistics borrowed from experimental physics: the peak correlation is converted to a
significance (sigma) against the empirical null of all other alignments, then corrected
for the look-elsewhere effect: searching a whole month gives chance many tries, so a
high peak somewhere is less surprising than the same peak at a pre-registered time.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal
from scipy.stats import norm

from .reference import ReferenceSeries, iso


def _fill_nans(v: np.ndarray) -> np.ndarray:
    v = v.astype(np.float64).copy()
    bad = ~np.isfinite(v)
    if bad.all():
        return np.zeros_like(v)
    if bad.any():
        idx = np.arange(len(v))
        v[bad] = np.interp(idx[bad], idx[~bad], v[~bad])
    return v


def sliding_pearson(ref: np.ndarray, q: np.ndarray, max_gap_frac: float = 0.1) -> np.ndarray:
    """Pearson correlation of q against every alignment in ref. NaN where ref has too many gaps."""
    m = len(q)
    if len(ref) < m:
        return np.array([])
    q = _fill_nans(q)
    qz = (q - q.mean()) / (q.std() + 1e-12)
    valid = np.isfinite(ref)
    r = np.where(valid, ref, 0.0)
    r = np.where(valid, r, np.convolve(r, np.ones(5) / 5, mode="same"))  # soften single gaps
    c = lambda v: np.concatenate([[0.0], np.cumsum(v)])
    c1, c2, cv = c(r), c(r * r), c(valid.astype(float))
    s1, s2, nv = c1[m:] - c1[:-m], c2[m:] - c2[:-m], cv[m:] - cv[:-m]
    mean = s1 / m
    var = np.maximum(s2 / m - mean ** 2, 1e-14)
    num = signal.oaconvolve(r, qz[::-1], mode="valid")  # overlap-add: ~2x faster for long references
    corr = num / (m * np.sqrt(var))
    corr[nv < (1 - max_gap_frac) * m] = np.nan
    return np.clip(corr, -1.0, 1.0)


def _corr_length(z: np.ndarray) -> float:
    """Decorrelation length (samples) of the correlation series: sets the look-elsewhere trials factor."""
    v = z[np.isfinite(z)]
    if len(v) < 50:
        return 1.0
    v = v - v.mean()
    n = min(len(v), 1 << 16)
    ac = signal.fftconvolve(v[:n], v[:n][::-1], mode="full")[n - 1:]
    ac /= ac[0] + 1e-12
    below = np.where(ac < np.exp(-1))[0]
    return float(max(1, below[0])) if below.size else float(n)


@dataclass
class MatchResult:
    best_start_ts: float     # UTC unix time at which the recording started
    peak_corr: float
    sigma: float             # local significance vs empirical null
    false_match_prob: float  # look-elsewhere corrected
    trials: float
    candidates: list[dict]
    corr: np.ndarray
    search_from_ts: float
    ref_step_s: int
    n_points: int
    rmse_mhz: float

    def summary(self) -> dict:
        return {
            "best_start_utc": iso(self.best_start_ts),
            "peak_correlation": round(self.peak_corr, 3),
            "significance_sigma": round(self.sigma, 1),
            "false_match_probability": float(f"{self.false_match_prob:.3g}"),
            "look_elsewhere_trials": int(self.trials),
            "points_compared": self.n_points,
            "reference_resolution_s": self.ref_step_s,
            "residual_rmse_mhz": round(self.rmse_mhz, 2),
            "top_candidates": self.candidates,
        }


def match(trace_t: np.ndarray, trace_dev_hz: np.ndarray, ref: ReferenceSeries,
          start_ts: float, end_ts: float) -> MatchResult:
    """Search for the recording's start time within [start_ts, end_ts]."""
    dur = trace_t[-1] - trace_t[0]
    i0, seg = ref.window(start_ts + trace_t[0], end_ts + trace_t[-1])
    step = ref.step_s
    best = None
    for phase in range(step):                      # coarse references: try each sub-step phase
        sel = np.arange(phase, len(trace_t), step)
        if len(sel) < 8:
            continue
        q = trace_dev_hz[sel]
        corr = sliding_pearson(seg, q)
        if corr.size == 0 or not np.isfinite(corr).any():
            continue
        k = int(np.nanargmax(corr))
        if best is None or corr[k] > best[0]:
            best = (corr[k], k, phase, sel, corr)
    if best is None:
        raise ValueError("Not enough reference data in that window to compare against.")
    peak, k, phase, sel, corr = best
    m = len(sel)
    start = ref.t0 + (i0 + k) * step - trace_t[sel[0]]

    z = np.arctanh(np.clip(corr, -0.999999, 0.999999))
    idx = np.arange(len(z))
    null_mask = np.isfinite(z) & (np.abs(idx - k) > max(m, 30))
    null = z[null_mask]
    if null.size >= 30:
        mu, sd = null.mean(), null.std() + 1e-12
        sigma = (z[k] - mu) / sd
        L = _corr_length(np.where(null_mask, z, np.nan))
        trials = max(1.0, null.size / L)
    else:  # tiny window: fall back to Fisher's analytic null with an effective sample size
        q = trace_dev_hz[sel]
        rho = np.corrcoef(q[:-1], q[1:])[0, 1] if m > 3 else 0.0
        n_eff = max(4.0, m * (1 - rho ** 2) / (1 + rho ** 2))
        sigma = z[k] * np.sqrt(n_eff - 3)
        trials = max(1.0, float(len(z)))
    p_local = norm.sf(sigma)
    p_global = float(-np.expm1(trials * np.log1p(-min(p_local, 1 - 1e-16))))

    # top distinct candidates
    order = np.argsort(-np.nan_to_num(corr, nan=-2))
    cands = []
    for j in order:
        if len(cands) >= 3 or not np.isfinite(corr[j]):
            break
        if all(abs(j - c["_j"]) > max(m // 2, 30) for c in cands):
            cands.append({"_j": int(j), "start_utc": iso(ref.t0 + (i0 + j) * step - trace_t[sel[0]]),
                          "correlation": round(float(corr[j]), 3)})
    for c in cands:
        c.pop("_j")

    q = trace_dev_hz[sel] - np.nanmean(trace_dev_hz[sel])
    r = seg[k:k + m] - np.nanmean(seg[k:k + m])
    rmse = float(np.sqrt(np.nanmean((q - r) ** 2)) * 1000)
    return MatchResult(start, float(peak), float(sigma), p_global, trials, cands, corr,
                       ref.t0 + i0 * step - trace_t[sel[0]], step, m, rmse)


def segment_consistency(trace_t: np.ndarray, trace_dev_hz: np.ndarray, ref: ReferenceSeries,
                        start_ts: float, seg_s: int = 60, hop_s: int = 30) -> list[dict]:
    """Re-match each minute of the recording on its own.

    In an unedited recording every segment lands at its expected offset. If audio was cut
    out, later segments land late by the length of the cut; if it was reordered, segments
    land out of order; if material was inserted from elsewhere, those segments match nothing.
    """
    if ref.step_s != 1:
        return []  # needs 1-second reference data
    dur = int(trace_t[-1] - trace_t[0])
    seg_s = min(seg_s, max(20, dur // 3))
    hop_s = max(10, min(hop_s, seg_s // 2))
    out = []
    search = max(180, dur + 120)
    for a in range(0, max(1, dur - seg_s + 1), hop_s):
        sel = (trace_t - trace_t[0] >= a) & (trace_t - trace_t[0] < a + seg_s)
        q = trace_dev_hz[sel]
        if len(q) < 15 or np.nanstd(q) < 1e-4:
            continue
        t_first = trace_t[sel][0]
        expected = start_ts + t_first
        i0, w = ref.window(expected - search, expected + search + len(q))
        corr = sliding_pearson(w, q)
        if corr.size == 0 or not np.isfinite(corr).any():
            continue
        k = int(np.nanargmax(corr))
        found = ref.t0 + (i0 + k) - 0.0
        delta = float(found - expected)
        k0 = ref.index_of(expected) - i0
        c0 = float(corr[k0]) if 0 <= k0 < len(corr) and np.isfinite(corr[k0]) else float("nan")
        cb = float(corr[k])
        if (abs(delta) <= 3 and cb >= 0.6) or (np.isfinite(c0) and c0 >= 0.75 and cb - c0 < 0.06):
            status = "in place"
        elif cb >= 0.8 and abs(delta) > 3 and (not np.isfinite(c0) or cb - c0 >= 0.08):
            status = "displaced"
        else:
            status = "unverifiable"
        out.append({"from_s": round(float(t_first - trace_t[0]), 1), "to_s": round(float(t_first - trace_t[0] + len(q)), 1),
                    "corr_at_expected": None if not np.isfinite(c0) else round(c0, 2),
                    "best_corr": round(cb, 2), "offset_s": round(delta, 1), "status": status})
    return out
