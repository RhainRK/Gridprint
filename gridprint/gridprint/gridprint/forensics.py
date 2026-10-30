"""A forensic case: one recording, the analyses run on it, and their results.

Every public method returns plain JSON-able dicts. These are the tools the Nemotron agent
calls, and they are deterministic, so they can be tested without any model.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from .audio import load_audio, sha256_file
from .enf import ENFTrace, detect_grid, extract_enf, phase_jumps_multi
from .match import match, segment_consistency
from .reference import ReferenceSeries, iso, parse_utc

LONDON = ZoneInfo("Europe/London")
CUT_MARGIN_S = 5.0  # ENF frames straddling a cut are contaminated; drop this much either side

GRIDS_60HZ = ("North America (USA, Canada, Mexico), much of Central America, parts of South America "
              "(e.g. Brazil, Colombia, Venezuela, Peru), South Korea, Taiwan, the Philippines, "
              "Saudi Arabia and western Japan")
GRIDS_50HZ = "Great Britain, Ireland, continental Europe, most of Africa, Asia and Oceania, and eastern Japan"


_DSP_CACHE: "OrderedDict[str, tuple]" = OrderedDict()  # sha256 -> (grid, trace, jumps); repeat analyses skip the DSP
_DSP_CACHE_SIZE = 16


def _local(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(LONDON).strftime("%a %d %b %Y, %H:%M:%S %Z")


@dataclass
class Case:
    path: str
    x: np.ndarray = field(repr=False, default=None)
    sr: int = 0
    sha256: str = ""
    grid: dict | None = None
    trace: ENFTrace | None = field(repr=False, default=None)
    jumps: list | None = None
    last_match: dict | None = None
    timeline_cuts: list = field(default_factory=list)
    _history: list = field(default_factory=list, repr=False)
    _match_obj: object = field(repr=False, default=None)
    references: list[ReferenceSeries] = field(default_factory=list, repr=False)

    @classmethod
    def open(cls, path: str | Path, references: list[ReferenceSeries]) -> "Case":
        sha = sha256_file(path)
        x, sr = load_audio(path)
        case = cls(path=str(path), x=x, sr=sr, sha256=sha, references=references)
        if sha in _DSP_CACHE:
            case.grid, case.trace, case.jumps = _DSP_CACHE[sha]
            _DSP_CACHE.move_to_end(sha)
        return case

    # ------------------------------------------------------------------ tool: analyse_recording
    def analyse_recording(self) -> dict:
        if self.grid is None:
            self.grid = detect_grid(self.x, self.sr)
            if self.grid["hum_detected"]:
                self.trace = extract_enf(self.x, self.sr, self.grid["nominal_hz"])
                self.jumps = phase_jumps_multi(self.x, self.sr, self.grid["nominal_hz"], self.trace.harmonics_used)
            else:
                self.jumps = []
            if self.sha256 and self.sha256 != "-":
                _DSP_CACHE[self.sha256] = (self.grid, self.trace, self.jumps)
                while len(_DSP_CACHE) > _DSP_CACHE_SIZE:
                    _DSP_CACHE.popitem(last=False)
        g = self.grid
        out = {
            "file_sha256": self.sha256,
            "duration_s": round(len(self.x) / self.sr, 1),
            "sample_rate_hz": self.sr,
            "mains_hum_detected": g["hum_detected"],
            "hum_snr_db_by_harmonic": g["harmonic_snr_db"],
        }
        if g["hum_detected"]:
            nom = g["nominal_hz"]
            dev = self.trace.deviation_mhz
            out.update({
                "grid_nominal_hz": nom,
                "grids_with_this_frequency": GRIDS_50HZ if nom == 50 else GRIDS_60HZ,
                "harmonics_used": self.trace.harmonics_used,
                "enf_mean_mhz_from_nominal": round(float(np.mean(dev)), 1),
                "enf_range_mhz": [round(float(dev.min()), 1), round(float(dev.max()), 1)],
                "phase_discontinuities": self.jumps,
            })
        else:
            out["note"] = ("No mains hum strong enough to track. Common innocent causes: battery device "
                           "recording outdoors, aggressive noise suppression, heavy compression. It is also "
                           "consistent with fully synthetic audio. On its own this proves nothing either way.")
        return out

    # ------------------------------------------------------------------ tool: list_reference_coverage
    def list_reference_coverage(self) -> dict:
        return {"references": [r.coverage() for r in self.references],
                "live_source": "Elexon FREQ (GB, ~15 s resolution) can be fetched for recent windows if enabled."}

    def _pick_ref(self, start_ts: float, end_ts: float) -> ReferenceSeries | None:
        nom = self.grid["nominal_hz"]
        best, best_overlap = None, 0.0
        for r in self.references:
            if r.nominal != nom:
                continue
            ov = min(end_ts, r.t_end) - max(start_ts, r.t0)
            if ov > best_overlap:
                best, best_overlap = r, ov
        return best

    def _pieces(self) -> list[tuple[float, float]]:
        t = self.trace.t
        cuts = sorted([j["t_s"] for j in self.jumps or []] + [c["t_s"] for c in self.timeline_cuts])
        edges = [t[0]] + cuts + [t[-1]]
        out = []
        for a, b in zip(edges[:-1], edges[1:]):
            a2 = a + (CUT_MARGIN_S if a != t[0] else 0)
            b2 = b - (CUT_MARGIN_S if b != t[-1] else 0)
            if b2 - a2 >= 20:
                out.append((a2, b2))
        return out or [(t[0], t[-1])]

    def _halves_test(self, ref: ReferenceSeries, s: float, e: float) -> dict | None:
        """Backstop for cuts that left no phase step: place each half of the recording on its own.

        If the halves land at times that disagree, audio is missing between them; a change-point
        scan then finds where the join is."""
        t, dev = self.trace.t, self.trace.f - self.trace.nominal
        if t[-1] - t[0] < 80:
            return None
        mid = 0.5 * (t[0] + t[-1])
        A, B = t <= mid, t > mid
        mA, mB = match(t[A], dev[A], ref, s, e), match(t[B], dev[B], ref, s, e)
        dur = t[-1] - t[0]
        if mA.false_match_prob <= mB.false_match_prob:
            anc, oth_sel, first_is_anchor = mA, B, True
        else:
            anc, oth_sel, first_is_anchor = mB, A, False
        lo = max(ref.t0, anc.best_start_ts - dur - 1800)
        hi = min(ref.t_end - dur, anc.best_start_ts + dur + 1800)
        oth = match(t[oth_sel], dev[oth_sel], ref, lo, hi)
        d = oth.best_start_ts - anc.best_start_ts
        if oth.peak_corr < 0.8 or oth.false_match_prob > 1e-3 or abs(d) <= 2:
            return None
        sA, sB = (anc.best_start_ts, oth.best_start_ts) if first_is_anchor else (oth.best_start_ts, anc.best_start_ts)
        rA = np.array([ref.dev_hz[ref.index_of(sA + ti)] if 0 <= ref.index_of(sA + ti) < len(ref.dev_hz) else np.nan for ti in t])
        rB = np.array([ref.dev_hz[ref.index_of(sB + ti)] if 0 <= ref.index_of(sB + ti) < len(ref.dev_hz) else np.nan for ti in t])
        eA, eB = dev - rA, dev - rB
        eA -= np.nanmedian(eA); eB -= np.nanmedian(eB)
        cA = np.concatenate([[0], np.nancumsum(eA ** 2)]); cB = np.concatenate([[0], np.nancumsum(eB ** 2)])
        k = np.arange(10, len(t) - 10)
        cost = cA[k] + (cB[-1] - cB[k])
        c = float(t[k[int(np.argmin(cost))]])
        return {"t_s": round(c, 1), "found_by": "timeline change-point (halves placed at different times)",
                "seconds_missing": round(sB - sA, 1)}

    # ------------------------------------------------------------------ tool: match_grid
    def match_grid(self, start_utc: str, end_utc: str) -> dict:
        self.analyse_recording()
        if not self.grid["hum_detected"]:
            return {"error": "No mains hum in this recording, so there is nothing to match."}
        s, e = parse_utc(start_utc).timestamp(), parse_utc(end_utc).timestamp()
        ref = self._pick_ref(s, e)
        if ref is None:
            return {"error": f"No {self.grid['nominal_hz']} Hz reference data covers {start_utc} to {end_utc}.",
                    "available": [r.coverage() for r in self.references]}
        s, e = max(s, ref.t0), min(e, ref.t_end - (self.trace.t[-1] - self.trace.t[0]))
        if e <= s:
            return {"error": "The search window is shorter than the recording or outside the reference coverage."}
        t, dev = self.trace.t, self.trace.f - self.trace.nominal
        self.timeline_cuts = []  # timeline cuts are specific to this search window

        def anchor_on(pieces):
            """Search the whole window with each long-enough piece; keep the most distinctive."""
            best = None
            for pc in pieces:
                if pc[1] - pc[0] < 45 and len(pieces) > 1:
                    continue
                ps = (t >= pc[0]) & (t <= pc[1])
                mr = match(t[ps], dev[ps], ref, s, e)
                if best is None or mr.false_match_prob < best[1].false_match_prob:
                    best = (pc, mr)
            if best is None:  # every piece is short: fall back to the longest
                pc = max(pieces, key=lambda p: p[1] - p[0])
                ps = (t >= pc[0]) & (t <= pc[1])
                best = (pc, match(t[ps], dev[ps], ref, s, e))
            return best

        pieces = self._pieces()
        longest, anchor = anchor_on(pieces)
        if len(pieces) == 1 and anchor.false_match_prob > 0.01 and not self.timeline_cuts:
            cut = self._halves_test(ref, s, e)
            if cut:
                self.timeline_cuts.append(cut)
                pieces = self._pieces()
                longest, anchor = anchor_on(pieces)

        piece_results = []
        joint = anchor.false_match_prob
        dur = t[-1] - t[0]
        for a, b in pieces:
            ps = (t >= a) & (t <= b)
            if (a, b) == longest:
                mr = anchor
            else:  # local search around the anchor: far fewer trials, so short pieces can still be placed
                mr = match(t[ps], dev[ps], ref, max(ref.t0, anchor.best_start_ts - dur - 1800),
                           min(ref.t_end - (b - a), anchor.best_start_ts + dur + 1800))
                joint *= mr.false_match_prob
            piece_results.append({
                "recording_from_s": round(float(a), 1), "recording_to_s": round(float(b), 1),
                "implied_recording_start_utc": iso(mr.best_start_ts), "_ts": mr.best_start_ts,
                "correlation": round(mr.peak_corr, 3), "significance_sigma": round(mr.sigma, 1),
                "false_match_probability": float(f"{mr.false_match_prob:.3g}"),
                "role": "anchor (searched whole window)" if (a, b) == longest else "placed near anchor",
            })
        gaps = []
        for p, q in zip(piece_results[:-1], piece_results[1:]):
            d = q["_ts"] - p["_ts"]
            if abs(d) > 2:
                gaps.append({"at_recording_s": round(p["recording_to_s"] + CUT_MARGIN_S, 1),
                             "seconds_missing": round(float(d), 1) if d > 0 else None,
                             "seconds_overlapping_or_reordered": round(float(-d), 1) if d < 0 else None})
        # When the recording really started: the first piece's implied start, if it was placed confidently.
        first_start = piece_results[0]["_ts"] if piece_results[0]["correlation"] >= 0.6 else anchor.best_start_ts
        for p in piece_results:
            p.pop("_ts")

        segs = segment_consistency(t, dev, ref, first_start)
        all_cuts = (self.jumps or []) + self.timeline_cuts
        for sg in segs:  # a segment containing a detected cut can't be judged on its own
            if any(sg["from_s"] - CUT_MARGIN_S <= j["t_s"] <= sg["to_s"] + CUT_MARGIN_S for j in all_cuts):
                sg["status"] = "spans a cut"
        res = {
            "best_recording_start_utc": iso(first_start),
            "best_recording_start_london": _local(first_start),
            "combined_false_match_probability": float(f"{joint:.3g}"),
            "searched_utc": [iso(s), iso(e)],
            "anchor_piece_s": [round(float(longest[0]), 1), round(float(longest[1]), 1)],
            "anchor": anchor.summary(),
            "combined_note": ("Probability that chance alone would place every piece where it was placed "
                              "(anchor searched across the window, other pieces searched near the anchor; assumes independence)."),
            "pieces": piece_results,
            "edits_implied_by_timeline": gaps,
            "segment_check": segs,
            "reference": ref.coverage(),
        }
        if ref.synthetic:
            res["warning"] = "Reference is SYNTHETIC test data. Do not treat this result as evidence."
        # Keep the most convincing search as the case's working answer (used by check_edits and the plots).
        self._history.append((joint, res, (anchor, ref, longest), list(self.timeline_cuts)))
        _, self.last_match, self._match_obj, self.timeline_cuts = min(self._history, key=lambda h: h[0])
        return res

    # ------------------------------------------------------------------ tool: check_edits
    def check_edits(self) -> dict:
        self.analyse_recording()
        if not self.grid["hum_detected"]:
            return {"result": "not possible", "reason": "no mains hum to test for continuity"}
        out = {"phase_discontinuities": self.jumps,
               "method": "Hum phase should evolve smoothly in an unedited recording; a step means audio was cut and joined."}
        if self.timeline_cuts:
            out["cuts_found_from_timeline"] = self.timeline_cuts
        if self.last_match:
            out["timeline_gaps"] = self.last_match["edits_implied_by_timeline"]
            out["segments"] = self.last_match["segment_check"]
            displaced = [s for s in self.last_match["segment_check"] if s["status"] == "displaced"]
            out["displaced_segments"] = len(displaced)
        susp = bool(self.jumps) or bool(self.timeline_cuts) or bool(out.get("timeline_gaps")) or bool(out.get("displaced_segments"))
        out["edit_suspected"] = susp
        return out
