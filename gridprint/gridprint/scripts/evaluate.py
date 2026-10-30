"""Measure Gridprint's accuracy on the reference data in data/ (use real NESO data for the write-up).

For each trial: pick a random true start time, synthesise a recording with that window's real
grid hum buried under speech-band noise, then search the WHOLE reference for it.
Half the trials also have a random chunk cut out, to measure edit detection.

  python scripts/evaluate.py --trials 40
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gridprint.enf import extract_enf, phase_jumps_multi, detect_grid  # noqa: E402
from gridprint.forensics import Case  # noqa: E402
from gridprint.reference import ReferenceSeries, iso, synthetic_grid  # noqa: E402
from gridprint.synth import make_recording  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--trials", type=int, default=30)
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()

refs = [ReferenceSeries.load(p) for p in sorted((ROOT / "data").glob("*.npz"))]
refs = [r for r in refs if not r.synthetic] or refs
refs = [r for r in refs if r.step_s == 1] or [synthetic_grid(datetime(2025, 1, 1, tzinfo=timezone.utc), 31 * 86400)]
ref = max(refs, key=lambda r: len(r.dev_hz))
rng = np.random.default_rng(a.seed)
SR = 8000
print(f"Reference: {ref.source}, {iso(ref.t0)} to {iso(ref.t_end)} ({len(ref.dev_hz) / 86400:.1f} days)\n")
print(f"{'dur s':>6} {'hum dB':>7} {'cut s':>6} | {'found':>5} {'err s':>6} {'sigma':>6} {'p_false':>9} | {'edit flagged':>12}")

rows = []
for n in range(a.trials):
    dur = int(rng.choice([60, 120, 180, 300]))
    hum_db = float(rng.choice([-30, -40, -50]))
    cut = int(rng.choice([0, 0, 20, 45])) if dur >= 120 else 0
    for _ in range(500):
        i = int(rng.integers(3600, len(ref.dev_hz) - 3600 - dur - cut - 30))
        if np.isfinite(ref.dev_hz[i:i + dur + cut + 30]).all():
            break
    true_ts = ref.t0 + i
    x = make_recording(ref.dev_hz[i:i + dur + cut + 20], 1, dur + cut, sr=SR, hum_rel_db=hum_db, seed=n)
    if cut:
        at = int(rng.integers(30, dur - 30))
        x = np.concatenate([x[:at * SR], x[(at + cut) * SR:]])
    case = Case(path=f"trial{n}", x=x, sr=SR, sha256="-", references=[ref])
    case.analyse_recording()
    if not case.grid["hum_detected"]:
        rows.append((dur, hum_db, cut, False, None, None, None, False))
        continue
    m = case.match_grid(iso(ref.t0), iso(ref.t_end))
    if "error" in m:
        rows.append((dur, hum_db, cut, False, None, None, None, False))
        continue
    err = datetime.fromisoformat(m["best_recording_start_utc"].replace("Z", "+00:00")).timestamp() - true_ts
    ok = abs(err) <= 2
    flagged = case.check_edits()["edit_suspected"]
    rows.append((dur, hum_db, cut, ok, err, m["anchor"]["significance_sigma"], m["combined_false_match_probability"], flagged))
    r = rows[-1]
    print(f"{dur:>6} {hum_db:>7.0f} {cut:>6} | {'yes' if ok else 'NO':>5} {err:>6.0f} {r[5]:>6.1f} {r[6]:>9.2g} | {'yes' if flagged else 'no':>12}")

found = [r for r in rows if r[3]]
print(f"\nTimestamp recovered to within 2 s: {len(found)}/{len(rows)}")
for d in sorted({r[0] for r in rows}):
    sub = [r for r in rows if r[0] == d]
    print(f"  {d:>3} s recordings: {sum(r[3] for r in sub)}/{len(sub)}")
cut_rows = [r for r in rows if r[2]]
clean_rows = [r for r in rows if not r[2]]
if cut_rows:
    print(f"Edits detected: {sum(r[7] for r in cut_rows)}/{len(cut_rows)} cut recordings")
if clean_rows:
    print(f"False edit alarms: {sum(r[7] for r in clean_rows)}/{len(clean_rows)} clean recordings")
confident = [r for r in rows if r[6] is not None and r[6] < 0.01]
if confident:
    print(f"Calibration: of results reported with false-match probability < 0.01, "
          f"{sum(r[3] for r in confident)}/{len(confident)} were correct")
wrong = [r for r in rows if r[6] is not None and not r[3]]
if wrong:
    print(f"Every wrong answer's reported false-match probability: {[round(r[6], 2) for r in wrong]}")
