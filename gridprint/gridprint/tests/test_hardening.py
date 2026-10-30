"""Robustness: messy grid data, awkward claims, bad uploads, security headers."""
import io

import numpy as np
import pandas as pd
import scipy.io.wavfile as wf
from fastapi.testclient import TestClient

import server
from gridprint.agent import Analyst
from gridprint.reference import neso_from_csv_bytes


def _neso_csv(extra_id=True, glitch=True):
    t = pd.date_range("2025-01-01", periods=7200, freq="1s", tz="UTC")
    f = 50 + 0.05 * np.sin(np.arange(7200) / 300)
    if glitch:
        f[100] = 0.0      # logger glitch
        f[200] = 99.9
    df = pd.DataFrame({"dtm": t.strftime("%Y-%m-%d %H:%M:%S"), "f": f})
    if extra_id:
        df.insert(0, "_id", range(len(df)))
    return df.to_csv(index=False).encode()


def test_neso_parser_finds_columns_and_drops_glitches():
    ref = neso_from_csv_bytes(_neso_csv(), "test")
    assert ref.nominal == 50 and ref.step_s == 1 and len(ref.dev_hz) == 7200
    assert np.isnan(ref.dev_hz[100]) and np.isnan(ref.dev_hz[200])
    assert np.nanmax(np.abs(ref.dev_hz)) < 0.06


def _facts(claim, found="2025-01-16T11:25:31Z", edited=False):
    a = {"mains_hum_detected": True, "grid_nominal_hz": 50}
    m = {"combined_false_match_probability": 1e-6, "best_recording_start_utc": found, "edits_implied_by_timeline": []}
    return Analyst._offline_verdict(claim, a, m, {"edit_suspected": edited})


def test_offline_verdict_reads_common_date_formats():
    assert _facts("recorded on 16 January 2025").startswith("Consistent")
    assert _facts("recorded January 16, 2025").startswith("Consistent")
    assert _facts("recorded on 13/01/2025").startswith("Inconsistent")   # UK day/month/year
    assert _facts("recorded on 16 January 2025 at 18:00").startswith("Inconsistent")
    assert _facts("unedited, 16 January 2025", edited=True).startswith("Inconsistent")


def test_security_headers_allow_hugging_face_framing_only():
    h = TestClient(server.app).get("/").headers
    assert h["x-content-type-options"] == "nosniff"
    csp = h["content-security-policy"]
    assert "frame-ancestors 'self' https://huggingface.co" in csp and "script-src 'self'" in csp


def test_bad_uploads_get_plain_messages():
    c = TestClient(server.app)
    buf = io.BytesIO()
    wf.write(buf, 8000, (np.random.randn(8000 * 5) * 1000).astype(np.int16))
    short = c.post("/api/analyse", files={"file": ("a.wav", buf.getvalue(), "audio/wav")}, data={"claim": "x"})
    assert "at least 15 seconds" in short.text
    junk = c.post("/api/analyse", files={"file": ("a.mp3", b"nonsense" * 50, "audio/mpeg")}, data={"claim": "x"})
    assert "couldn" in junk.text and "Traceback" not in junk.text
    assert c.post("/api/analyse", files={"file": ("a.wav", b"", "audio/wav")}).status_code == 400
