"""Turn tool calls and statistics into short plain-English lines for the interface."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def fluke(p: float | None) -> str:
    """A false-match probability as odds a non-specialist can read."""
    if p is None:
        return "Not measured"
    if p >= 0.2:
        return "High"
    if p < 1e-12:
        return "Under 1 in a trillion"
    n = 1 / p
    for limit, div, unit in ((1e9, 1e9, " billion"), (1e6, 1e6, " million")):
        if n >= limit:
            return f"1 in {n / div:.0f}{unit}"
    return f"1 in {round(n, -2 if n >= 1000 else 0):,.0f}"


def london(ts: float, fmt: str = "%a %d %b %Y, %H:%M:%S") -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(LONDON).strftime(fmt)


def _utc_to_london(s: str, fmt: str = "%d %b, %H:%M") -> str:
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.astimezone(LONDON).strftime(fmt)
    except (ValueError, AttributeError):
        return s


def mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def title(tool: str, args: dict) -> str:
    if tool == "analyse_recording":
        return "Listened for mains hum"
    if tool == "list_reference_coverage":
        return "Checked which grid records are available"
    if tool == "match_grid":
        a, b = args.get("start_utc", ""), args.get("end_utc", "")
        return f"Searched the grid record, {_utc_to_london(a)} to {_utc_to_london(b)}"
    if tool == "check_edits":
        return "Checked for cuts"
    if tool == "web_search":
        return f"Searched the web for “{args.get('query', '')}”"
    if tool == "report":
        return "Wrote the report"
    return tool.replace("_", " ").capitalize()


def detail(tool: str, args: dict, r) -> str:
    if isinstance(r, dict) and r.get("error"):
        return r["error"]
    if tool == "analyse_recording":
        if not r.get("mains_hum_detected"):
            return "No hum strong enough to track."
        best = max(r["hum_snr_db_by_harmonic"].values())
        cuts = len(r.get("phase_discontinuities") or [])
        extra = f", and {cuts} possible cut{'s' if cuts != 1 else ''}" if cuts else ""
        return f"Found a {r['grid_nominal_hz']} Hz hum, {best:.0f} dB above the noise{extra}."
    if tool == "list_reference_coverage":
        refs = r.get("references", [])
        if not refs:
            return "No grid records loaded."
        c = refs[0]
        kind = "synthetic test data" if c["synthetic"] else f"{c['resolution_s']}-second data"
        return f"{c['grid']} grid, {_utc_to_london(c['from_utc'], '%d %b %Y')} to {_utc_to_london(c['to_utc'], '%d %b %Y')}, {kind}."
    if tool == "match_grid":
        return (f"Best fit {r['best_recording_start_london']}. "
                f"Chance of a coincidence: {fluke(r['combined_false_match_probability']).lower()}.")
    if tool == "check_edits":
        if r.get("result") == "not possible":
            return "Not possible without hum."
        gaps = [g for g in r.get("timeline_gaps") or [] if g.get("seconds_missing")]
        if gaps:
            g = gaps[0]
            return f"About {g['seconds_missing']:.0f} s missing at {mmss(g['at_recording_s'])}."
        if r.get("phase_discontinuities"):
            return f"Hum phase jumps at {mmss(r['phase_discontinuities'][0]['t_s'])}."
        return "None found. The hum runs continuously."
    if tool == "web_search":
        if isinstance(r, list) and r:
            return r[0].get("title", "")[:90]
        return "Nothing useful found."
    if tool == "report":
        return str(r)
    return ""
