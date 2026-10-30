"""End-to-end tests on synthetic grid data. No model or network needed: python -m pytest -q"""
from datetime import datetime, timezone

import numpy as np
import pytest

from gridprint.forensics import Case
from gridprint.reference import iso, synthetic_grid
from gridprint.synth import make_recording, speechlike

SR = 8000


@pytest.fixture(scope="module")
def ref():
    return synthetic_grid(datetime(2025, 1, 1, tzinfo=timezone.utc), 3 * 86400, seed=3)


def _case(x, ref):
    return Case(path="test", x=x, sr=SR, sha256="-", references=[ref])


def test_recovers_exact_start(ref):
    i = 100_000
    x = make_recording(ref.dev_hz[i:i + 260], 1, 240, sr=SR, hum_rel_db=-40, seed=1)
    c = _case(x, ref)
    m = c.match_grid(iso(ref.t0), iso(ref.t_end))
    assert m["best_recording_start_utc"] == iso(ref.t0 + i)
    assert m["combined_false_match_probability"] < 1e-3
    assert not c.check_edits()["edit_suspected"]


def test_finds_cut_and_its_length(ref):
    i, cut_at, cut = 150_000, 100, 45
    x = make_recording(ref.dev_hz[i:i + 320], 1, 285, sr=SR, hum_rel_db=-40, seed=4)
    x = np.concatenate([x[:cut_at * SR], x[(cut_at + cut) * SR:]])
    c = _case(x, ref)
    m = c.match_grid(iso(ref.t0), iso(ref.t_end))
    assert m["best_recording_start_utc"] == iso(ref.t0 + i)
    gaps = m["edits_implied_by_timeline"]
    assert gaps and abs(gaps[0]["seconds_missing"] - cut) <= 2
    assert c.check_edits()["edit_suspected"]


def test_identifies_60hz_grid(ref):
    us = synthetic_grid(datetime(2025, 1, 1, tzinfo=timezone.utc), 300, seed=9, nominal=60, grid="US")
    x = make_recording(us.dev_hz, 1, 200, sr=SR, nominal=60, hum_rel_db=-40, seed=2)
    a = _case(x, ref).analyse_recording()
    assert a["grid_nominal_hz"] == 60 and "North America" in a["grids_with_this_frequency"]


def test_no_hum_is_inconclusive_not_fake(ref):
    x = speechlike(120 * SR, SR, np.random.default_rng(0))
    a = _case(x, ref).analyse_recording()
    assert a["mains_hum_detected"] is False
    assert "proves nothing" in a["note"]
