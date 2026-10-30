"""Download GB grid frequency reference data into data/.

  python scripts/build_reference.py --months 2025-01            # real NESO 1 s data (recommended)
  python scripts/build_reference.py --elexon-hours 6            # last N hours, near-live, 15 s
  python scripts/build_reference.py --csv f-2025-1.csv          # a CSV you downloaded from the NESO page yourself
  python scripts/build_reference.py --synthetic                 # offline test data only
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gridprint.reference import fetch_elexon, fetch_neso_month, load_csv_file, synthetic_grid  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"

ap = argparse.ArgumentParser()
ap.add_argument("--months", nargs="*", default=[], help="YYYY-MM, e.g. 2025-01")
ap.add_argument("--elexon-hours", type=float, default=0)
ap.add_argument("--csv", nargs="*", default=[], help="NESO CSV or zip files downloaded by hand")
ap.add_argument("--synthetic", action="store_true")
a = ap.parse_args()
DATA.mkdir(exist_ok=True)

for ym in a.months:
    y, m = map(int, ym.split("-"))
    print(f"Fetching NESO {y}-{m:02d} (a month of 1 s data, ~50-100 MB)...")
    ref = fetch_neso_month(y, m)
    out = DATA / f"gb-neso-{y}-{m:02d}.npz"
    ref.save(out)
    print(f"  saved {out.name}: {ref.coverage()}  ({out.stat().st_size / 1e6:.1f} MB)")

for path in a.csv:
    ref = load_csv_file(path)
    out = DATA / f"gb-neso-{Path(path).stem}.npz"
    ref.save(out)
    print(f"saved {out.name}: {ref.coverage()}  ({out.stat().st_size / 1e6:.1f} MB)")

if a.elexon_hours:
    end = datetime.now(timezone.utc).replace(microsecond=0)
    ref = fetch_elexon(end - timedelta(hours=a.elexon_hours), end)
    out = DATA / f"gb-elexon-{end:%Y%m%dT%H%M}.npz"
    ref.save(out)
    print(f"saved {out.name}: {ref.coverage()}")

if a.synthetic:
    ref = synthetic_grid(datetime(2025, 1, 1, tzinfo=timezone.utc), 31 * 86400)
    out = DATA / "synthetic-2025-01.npz"
    ref.save(out)
    print(f"saved {out.name} (TEST DATA ONLY)")
