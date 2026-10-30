"""Grid frequency reference data.

Sources
- NESO (GB system operator) historic system frequency, 1-second resolution, monthly CSVs.
  NESO Open Data Licence. https://www.neso.energy/data-portal/system-frequency-data
- Elexon Insights API FREQ, ~15-second spot readings, near-live, no API key.
  https://data.elexon.co.uk/bmrs/api/v1/system/frequency
- A synthetic generator (Ornstein-Uhlenbeck model of grid frequency) for tests only.
"""
from __future__ import annotations

import io
import json
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy import signal

NESO_PACKAGE = "https://api.neso.energy/api/3/action/datapackage_show?id=system-frequency-data"
ELEXON_FREQ = "https://data.elexon.co.uk/bmrs/api/v1/system/frequency"
MISSING = -32768


@dataclass
class ReferenceSeries:
    grid: str            # e.g. "GB"
    nominal: int         # 50 or 60
    t0: int              # unix seconds (UTC) of first sample
    step_s: int          # sample spacing in seconds
    dev_hz: np.ndarray   # frequency minus nominal, float64, NaN for gaps
    source: str
    synthetic: bool = False

    @property
    def t_end(self) -> int:
        return self.t0 + self.step_s * (len(self.dev_hz) - 1)

    def index_of(self, ts: float) -> int:
        return int(round((ts - self.t0) / self.step_s))

    def time_of(self, idx: int) -> datetime:
        return datetime.fromtimestamp(self.t0 + idx * self.step_s, tz=timezone.utc)

    def window(self, start_ts: float, end_ts: float) -> tuple[int, np.ndarray]:
        i0 = max(0, self.index_of(start_ts))
        i1 = min(len(self.dev_hz), self.index_of(end_ts) + 1)
        return i0, self.dev_hz[i0:i1]

    def coverage(self) -> dict:
        valid = np.isfinite(self.dev_hz)
        return {
            "grid": self.grid, "nominal_hz": self.nominal, "source": self.source,
            "synthetic": self.synthetic, "resolution_s": self.step_s,
            "from_utc": iso(self.t0), "to_utc": iso(self.t_end),
            "valid_fraction": round(float(valid.mean()), 4),
        }

    # ---- persistence: compact int16 millihertz, a month of 1 s data is ~3 MB compressed
    def save(self, path: str | Path) -> None:
        mhz = np.where(np.isfinite(self.dev_hz), np.round(self.dev_hz * 1000), MISSING).astype(np.int16)
        np.savez_compressed(path, mhz=mhz, meta=json.dumps({
            "grid": self.grid, "nominal": self.nominal, "t0": self.t0, "step_s": self.step_s,
            "source": self.source, "synthetic": self.synthetic}))

    @classmethod
    def load(cls, path: str | Path) -> "ReferenceSeries":
        z = np.load(path, allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        mhz = z["mhz"].astype(np.float64)
        mhz[z["mhz"] == MISSING] = np.nan
        return cls(meta["grid"], meta["nominal"], meta["t0"], meta["step_s"], mhz / 1000.0,
                   meta["source"], meta.get("synthetic", False))


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s: str) -> datetime:
    s = s.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- NESO (GB, 1 s)

def neso_month_urls() -> dict[str, str]:
    """Map 'YYYY-M' -> download URL, discovered from NESO's CKAN data package.

    File names have changed over the years (f-2019-1.csv, fnew-2025-1.csv, ...), so match the year
    and month in the URL or the resource's name, in any of the common spellings."""
    import re
    with urllib.request.urlopen(NESO_PACKAGE, timeout=60) as r:
        pkg = json.load(r)
    months = ["january", "february", "march", "april", "may", "june", "july",
              "august", "september", "october", "november", "december"]
    found: dict[str, str] = {}

    def consider(url: str, text: str):
        if not url.startswith("http") or not re.search(r"\.(csv|zip)(\?|$)", url.lower()):
            return
        hay = f"{url} {text}".lower()
        m = re.search(r"(20\d\d)[-_ ](\d{1,2})(?!\d)", hay)
        if m:
            found.setdefault(f"{int(m.group(1))}-{int(m.group(2))}", url)
            return
        for i, name in enumerate(months, 1):
            m = re.search(name + r"[-_ ]?(20\d\d)", hay)
            if m:
                found.setdefault(f"{int(m.group(1))}-{i}", url)
                return

    def walk(o):
        if isinstance(o, dict):
            url = o.get("path") or o.get("url") or ""
            if isinstance(url, str) and url:
                consider(url, " ".join(str(o.get(k, "")) for k in ("name", "title", "description")))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(pkg)
    return found


def neso_from_csv_bytes(raw: bytes, name: str = "neso") -> ReferenceSeries:
    """Parse a NESO system-frequency CSV.

    Columns are found by content, not position (portal exports sometimes add an id column), and
    readings outside 47-53 Hz are treated as glitches: the GB grid has never run that far off 50 Hz."""
    import pandas as pd
    if raw[:2] == b"PK":  # some months are zipped
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            raw = zf.read(next(n for n in zf.namelist() if n.lower().endswith(".csv")))
    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    head = df.head(200)
    tcol = fcol = None
    for c in df.columns:
        if tcol is None and not pd.api.types.is_numeric_dtype(head[c]):  # pandas 3 stores text as "str", not object
            if pd.to_datetime(head[c], errors="coerce", utc=True).notna().mean() > 0.9:
                tcol = c
                continue
        if fcol is None:
            v = pd.to_numeric(head[c], errors="coerce")
            if v.notna().mean() > 0.9 and 45 < v.median() < 65:
                fcol = c
    if tcol is None or fcol is None:
        raise ValueError(f"Couldn't find time and frequency columns in {name} (columns: {list(df.columns)}).")
    ts = pd.to_datetime(df[tcol], utc=True, errors="coerce")
    f = pd.to_numeric(df[fcol], errors="coerce")
    nominal = 50 if f.median() < 55 else 60
    f = f.where((f > nominal - 3) & (f < nominal + 3))
    s = pd.Series(f.values, index=ts)
    s = s[s.index.notna()].dropna()
    s = s[~s.index.duplicated()].sort_index()
    if len(s) < 3600:
        raise ValueError(f"{name} holds less than an hour of usable frequency data.")
    s = s.resample("1s").mean()  # regular one-second grid; gaps become NaN
    t0 = int(s.index[0].timestamp())
    return ReferenceSeries("GB" if nominal == 50 else "60 Hz grid", nominal, t0, 1,
                           s.values.astype(np.float64) - nominal, f"NESO historic frequency ({name})")


def load_csv_file(path: str | Path) -> ReferenceSeries:
    """Manual fallback: a CSV (or zip) downloaded by hand from the NESO data portal."""
    path = Path(path)
    return neso_from_csv_bytes(path.read_bytes(), path.stem)


def fetch_neso_month(year: int, month: int) -> ReferenceSeries:
    url = neso_month_urls().get(f"{year}-{month}")
    if not url:
        raise LookupError(f"Couldn't find NESO's file for {year}-{month:02d} automatically. Download it from "
                          "https://www.neso.energy/data-portal/system-frequency-data and run: "
                          "python scripts/build_reference.py --csv path/to/the-file.csv")
    with urllib.request.urlopen(url, timeout=300) as r:
        raw = r.read()
    return neso_from_csv_bytes(raw, f"{year}-{month:02d}")


# --------------------------------------------------------------------------- Elexon (GB, ~15 s, near-live)

def fetch_elexon(start: datetime, end: datetime) -> ReferenceSeries:
    """Near-live GB frequency. Coarser (spot values every ~15 s) but covers today."""
    rows = []
    cur = start
    while cur < end:  # the API serves bounded windows; walk in 1-hour chunks
        nxt = min(end, cur + timedelta(hours=1))
        q = f"{ELEXON_FREQ}?from={cur.strftime('%Y-%m-%dT%H:%M:%SZ')}&to={nxt.strftime('%Y-%m-%dT%H:%M:%SZ')}&format=json"
        with urllib.request.urlopen(q, timeout=60) as r:
            data = json.load(r)
        rows += data.get("data", data if isinstance(data, list) else [])
        cur = nxt
    if not rows:
        raise LookupError("Elexon returned no frequency data for that window.")
    pts = sorted({int(parse_utc(r["measurementTime"]).timestamp()): float(r["frequency"]) for r in rows}.items())
    t = np.array([p[0] for p in pts]); f = np.array([p[1] for p in pts])
    grid_t = np.arange(t[0], t[-1] + 1, 15)
    dev = np.interp(grid_t, t, f - 50.0)
    return ReferenceSeries("GB", 50, int(grid_t[0]), 15, dev, "Elexon Insights FREQ (near-live, 15 s)")


# --------------------------------------------------------------------------- synthetic (tests only)

def synthetic_grid(start: datetime, seconds: int, seed: int = 7, nominal: int = 50, grid: str = "GB") -> ReferenceSeries:
    """Two-timescale Ornstein-Uhlenbeck process plus on-the-hour dispatch dips.

    Matches the gross statistics of GB frequency (std ~50-70 mHz, minutes-long excursions).
    It is a stand-in for tests; real analyses must use NESO or Elexon data.
    """
    rng = np.random.default_rng(seed)

    def ou(tau, sigma):
        a = np.exp(-1.0 / tau)
        e = rng.normal(0, sigma * np.sqrt(1 - a * a), seconds)
        return signal.lfilter([1.0], [1.0, -a], e)

    t0 = int(start.timestamp())
    tt = np.arange(seconds) + t0
    dev = ou(25, 0.035) + ou(400, 0.04)
    sec_in_hour = tt % 3600
    dev -= 0.04 * np.exp(-sec_in_hour / 90.0) * (sec_in_hour < 600)   # hourly schedule changes
    dev += rng.normal(0, 0.0015, seconds)                              # measurement noise
    return ReferenceSeries(grid, nominal, t0, 1, dev, "synthetic OU model (TEST DATA, not a real grid)", synthetic=True)
