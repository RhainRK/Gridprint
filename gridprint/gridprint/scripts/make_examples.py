"""Create the demo cases in examples/ from whatever reference data is in data/.

Each case hides a true recording time inside a real grid record. Ground truth is written
separately (examples/ground_truth.json) so the app never sees it.

  python scripts/make_examples.py                    # speech-like noise as the voice bed
  python scripts/make_examples.py --voice me.wav     # use your own voice (record ~4 minutes)
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gridprint.audio import load_audio  # noqa: E402
from gridprint.reference import ReferenceSeries, synthetic_grid  # noqa: E402
from gridprint.synth import make_recording, speechlike, to_int16  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--voice", help="your own recording to use as the speech bed")
ap.add_argument("--seed", type=int, default=11)
a = ap.parse_args()

refs = [ReferenceSeries.load(p) for p in sorted((ROOT / "data").glob("*.npz"))]
refs = [r for r in refs if not r.synthetic] or refs
refs = [r for r in refs if r.step_s == 1] or [synthetic_grid(datetime(2025, 1, 1, tzinfo=timezone.utc), 31 * 86400)]
ref = max(refs, key=lambda r: len(r.dev_hz))
rng = np.random.default_rng(a.seed)
SR, DUR = 8000, 210  # 8 kHz keeps the files small; the hum lives below 200 Hz
voice = None
if a.voice:
    voice, vsr = load_audio(a.voice, target_sr=SR)
    if vsr != SR:
        from scipy.signal import resample_poly
        voice = resample_poly(voice, SR, vsr)
london = ZoneInfo("Europe/London")
ex = ROOT / "examples"
ex.mkdir(exist_ok=True)


def pick_time(hour_lo=9, hour_hi=20, length=DUR + 70):
    for _ in range(2000):
        i = int(rng.integers(3600, len(ref.dev_hz) - 3600 - length))
        ts = ref.t0 + i
        if np.isfinite(ref.dev_hz[i:i + length]).all() and hour_lo <= datetime.fromtimestamp(ts, tz=london).hour < hour_hi:
            return i, ts
    raise RuntimeError("no clean window found")


def fmt(ts):
    return datetime.fromtimestamp(ts, tz=london).strftime("%A %d %B %Y at about %H:%M")


def utc(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


from gridprint.forensics import Case  # noqa: E402
from gridprint.reference import iso as _iso  # noqa: E402

cases, truth = [], {}


def analyse(x):
    c = Case(path="-", x=x, sr=SR, sha256="-", references=[ref])
    a = c.analyse_recording()
    m = c.match_grid(_iso(ref.t0), _iso(ref.t_end)) if a.get("mains_hum_detected") and a["grid_nominal_hz"] == 50 else None
    return a, m


def starts_at(m, ts):
    return m and "error" not in m and m["combined_false_match_probability"] < 0.01 and \
        abs(datetime.fromisoformat(m["best_recording_start_utc"].replace("Z", "+00:00")).timestamp() - ts) <= 2


def build(name, make, check, tries=8):
    """Demo cases are checked so the walkthrough shows each feature clearly.
    Accuracy on random recordings is measured separately by scripts/evaluate.py."""
    for attempt in range(tries):
        x, info = make(attempt)
        if check(x, info):
            wavfile.write(ex / name, SR, to_int16(x))
            print(f"  {name}: ok (attempt {attempt + 1})")
            return info
    raise RuntimeError(f"Couldn't make a clear demo for {name}; try a different --seed.")


def make_voice(attempt):
    i, ts = pick_time()
    return make_recording(ref.dev_hz[i:i + DUR + 10], 1, DUR, sr=SR, hum_rel_db=-42, seed=10 + attempt, voice=voice), ts


ts = build("voice-note.wav", make_voice, lambda x, ts: starts_at(analyse(x)[1], ts) and not Case(
    path="-", x=x, sr=SR, sha256="-", references=[ref]).analyse_recording().get("phase_discontinuities"))
cases.append({"file": "voice-note.wav", "title": "A voice note", "claim": f"Voice note recorded in a London flat on {fmt(ts)}."})
truth["voice-note.wav"] = {"true_start_utc": utc(ts), "edited": False, "claim_true": True}

ts2 = build("alibi.wav", make_voice, lambda x, ts: starts_at(analyse(x)[1], ts))
claimed = ts2 - 86400 * 3 if ts2 - 86400 * 3 > ref.t0 + 3600 else ts2 + 86400 * 3
cases.append({"file": "alibi.wav", "title": "An alibi",
              "claim": f"The defendant says this call was recorded on {fmt(claimed)}, proving he was at home."})
truth["alibi.wav"] = {"true_start_utc": utc(ts2), "claimed": fmt(claimed), "claim_true": False}

CUT_AT, CUT = 95, 50


def make_cut(attempt):
    i, ts = pick_time()
    x = make_recording(ref.dev_hz[i:i + DUR + 70], 1, DUR + CUT, sr=SR, hum_rel_db=-40, seed=30 + attempt, voice=voice)
    return np.concatenate([x[:CUT_AT * SR], x[(CUT_AT + CUT) * SR:]]), ts


def cut_found(x, ts):
    _, m = analyse(x)
    gaps = [g for g in (m or {}).get("edits_implied_by_timeline", []) if g.get("seconds_missing")]
    return starts_at(m, ts) and gaps and abs(gaps[0]["seconds_missing"] - CUT) <= 3


ts3 = build("leaked-meeting.wav", make_cut, cut_found)
cases.append({"file": "leaked-meeting.wav", "title": "A leaked meeting", "claim": f"Unedited leaked recording of a meeting on {fmt(ts3)}."})
truth["leaked-meeting.wav"] = {"true_start_utc": utc(ts3), "edited": f"{CUT} s removed at {CUT_AT} s", "claim_true": False}


def make_60(attempt):
    syn60 = synthetic_grid(datetime(2025, 1, 1, tzinfo=timezone.utc), DUR + 60, seed=99 + attempt, nominal=60, grid="US")
    return make_recording(syn60.dev_hz, 1, DUR, sr=SR, nominal=60, hum_rel_db=-40, seed=4 + attempt, voice=voice), None


build("whistleblower.wav", make_60, lambda x, _: analyse(x)[0].get("grid_nominal_hz") == 60)
cases.append({"file": "whistleblower.wav", "title": "A whistleblower",
              "claim": "A whistleblower says she recorded this in her office in Canary Wharf, London."})
truth["whistleblower.wav"] = {"note": "60 Hz hum: recorded on a 60 Hz grid, not in GB", "claim_true": False}


def make_nohum(attempt):
    x = speechlike(DUR * SR, SR, np.random.default_rng(5 + attempt)) if voice is None else np.resize(voice, DUR * SR)
    return 0.6 * x / np.max(np.abs(x)), None


build("ceo-voicemail.wav", make_nohum, lambda x, _: not analyse(x)[0]["mains_hum_detected"])
cases.append({"file": "ceo-voicemail.wav", "title": "A CEO voicemail",
              "claim": "Voicemail from our CEO authorising an urgent transfer, left from his office yesterday."})
truth["ceo-voicemail.wav"] = {"note": "no ENF present (stand-in for a cloned voice)"}

(ex / "claims.json").write_text(json.dumps(cases, indent=2))
(ex / "ground_truth.json").write_text(json.dumps(truth, indent=2))
print(f"Reference: {ref.source}")
for c in cases:
    print(f"  {c['file']}: {c['claim']}")
print("Ground truth is in examples/ground_truth.json (the app never reads it).")
