"""Gridprint web server.  Run:  python server.py   (then open http://127.0.0.1:7860)"""
from __future__ import annotations

import collections
import json
import os
import queue
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from gridprint import narrate
from gridprint.agent import Analyst
from gridprint.forensics import Case
from gridprint.reference import ReferenceSeries, parse_utc, synthetic_grid

VERSION = "1.0.0"
ROOT = Path(__file__).parent
WEB = ROOT / "web"
MAX_UPLOAD = 60 * 1024 * 1024          # bytes
MIN_SECONDS, MAX_SECONDS = 15, 30 * 60  # ENF needs time to say anything; very long files would hog the CPU
MAX_CLAIM = 1000                        # characters
MAX_CONCURRENT = int(os.environ.get("GRIDPRINT_MAX_CONCURRENT", "2"))  # free hosting has two CPU cores
_slots = threading.Semaphore(MAX_CONCURRENT)
TITLES = {"voice-note": "A voice note", "alibi": "An alibi", "leaked-meeting": "A leaked meeting",
          "whistleblower": "A whistleblower", "ceo-voicemail": "A CEO voicemail"}


def load_references() -> list[ReferenceSeries]:
    refs = [ReferenceSeries.load(p) for p in sorted((ROOT / "data").glob("*.npz"))]
    if any(not r.synthetic for r in refs):  # real grid data present: never mix in test data
        refs = [r for r in refs if not r.synthetic]
    return refs or [synthetic_grid(datetime(2025, 1, 1, tzinfo=timezone.utc), 31 * 86400)]


REFS = load_references()

# --------------------------------------------------------------------------- spending guard
# Hard limits on how often the public demo calls Nemotron, so nobody can run down your credits.
# Over a limit, the analysis still runs in full; only the report is written by the built-in template.
AI_DAILY_LIMIT = int(os.environ.get("GRIDPRINT_AI_DAILY_LIMIT", "40"))
AI_PER_VISITOR_PER_HOUR = int(os.environ.get("GRIDPRINT_AI_PER_VISITOR_PER_HOUR", "6"))
_ai_lock = threading.Lock()
_ai_day = {"date": "", "count": 0}
_ai_visitors: dict[str, collections.deque] = {}


def ai_allowed(visitor: str) -> tuple[bool, str]:
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    with _ai_lock:
        if _ai_day["date"] != today:
            _ai_day.update(date=today, count=0)
        if _ai_day["count"] >= AI_DAILY_LIMIT:
            return False, "the demo’s daily AI limit has been reached"
        q = _ai_visitors.setdefault(visitor, collections.deque())
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= AI_PER_VISITOR_PER_HOUR:
            return False, "you’ve reached this hour’s AI limit"
        q.append(now)
        _ai_day["count"] += 1
        return True, ""
app = FastAPI(title="Gridprint", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)

# Security headers. Framing is allowed only from Hugging Face, which embeds Spaces in an iframe.
_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"content-security-policy",
     b"default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
     b"font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; media-src 'self' blob:; "
     b"connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
     b"frame-ancestors 'self' https://huggingface.co https://*.hf.space"),
]


class SecurityHeaders:
    """Plain ASGI middleware, so streamed responses pass straight through unbuffered."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + _HEADERS
            await send(message)
        await self.inner(scope, receive, send_with_headers)


app.add_middleware(SecurityHeaders)
app.mount("/web", StaticFiles(directory=WEB), name="web")
if (ROOT / "examples").exists():
    app.mount("/examples", StaticFiles(directory=ROOT / "examples"), name="examples")


# --------------------------------------------------------------------------- payload for the interface

def _thin(n: int, limit: int = 900) -> np.ndarray:
    return np.arange(n) if n <= limit else np.linspace(0, n - 1, limit).astype(int)


def parse_verdict(md: str) -> dict:
    m = re.search(r"##\s*Verdict\s*\n+(.+?)(?:\n\s*##|\Z)", md, re.S)
    text = re.sub(r"[*_`]", "", m.group(1)).strip() if m else ""
    for kind, label in (("inconsistent", "Inconsistent with the claim"), ("consistent", "Consistent with the claim"),
                        ("inconclusive", "Inconclusive")):
        if text.lower().startswith(label.lower()):
            return {"kind": kind, "label": label, "reason": text[len(label):].lstrip(" .:;,-–—\n").strip()}
    first, _, rest = text.partition(". ")
    return {"kind": "unknown", "label": first or "Analysis complete", "reason": rest}


def evidence_trail(case: Case, analyst: Analyst, a: dict, claim: str) -> str:
    """Method and data notes appended to the downloadable report, so another analyst can check the work."""
    searches = [f"{s['args'].get('start_utc')} to {s['args'].get('end_utc')}" for s in analyst.trace if s["tool"] == "match_grid"]
    lines = [
        "## Method and evidence trail",
        f"- File SHA-256: {case.sha256}",
        f"- Duration: {a['duration_s']} s at {a['sample_rate_hz']} Hz",
        f"- Mains hum: {'detected at ' + str(a['grid_nominal_hz']) + ' Hz' if a.get('mains_hum_detected') else 'not detected'}"
        + (f"; harmonics used {a['harmonics_used']}" if a.get("harmonics_used") else ""),
        f"- Claim tested: {claim or '(none given)'}",
        f"- Grid reference: " + "; ".join(f"{r.source}, {r.coverage()['from_utc']} to {r.coverage()['to_utc']}, "
                                          f"{r.step_s} s resolution" for r in REFS),
        f"- Searches run (UTC): {'; '.join(searches) if searches else 'none'}",
        "- Method: ENF extracted per second from the mains harmonics (chirp-z spectra, SNR-weighted); matched by "
        "sliding correlation; significance against all other alignments with a look-elsewhere correction; "
        "edits detected from hum phase jumps and from placing each part of the recording on the grid timeline.",
        f"- Report written by: {analyst.model + ' on Nebius Token Factory' if analyst.llm else 'built-in template (offline mode)'}",
        f"- Software: Gridprint {VERSION}. Analysed {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.",
        "- Limits: ENF needs audible-to-the-microphone mains hum; missing hum is not evidence of fakery. "
        "Gridprint is an investigative aid, not an accredited forensic examination.",
    ]
    return "\n".join(lines)


def build_payload(case: Case, analyst: Analyst, report: str, claim: str = "", elapsed: float = 0.0) -> dict:
    a = case.analyse_recording()
    m = case.last_match
    tr = case.trace
    out = {"verdict": parse_verdict(report), "report_md": report, "hum": None, "match": None,
           "claim": claim, "mode": "agent" if analyst.llm else "offline", "elapsed_s": round(elapsed, 1),
           "report_download": f"# Gridprint report\n\n{report}\n\n{evidence_trail(case, analyst, a, claim)}\n"}

    # headline statistics
    stats = {"grid": "No hum", "grid_note": "Nothing to match", "when": "Undated", "when_note": "No hum to date it",
             "odds": "Not measured", "odds_note": "Chance of a coincidence", "cuts": "Can’t check", "cuts_note": "Needs mains hum"}
    if a.get("mains_hum_detected"):
        nom = a["grid_nominal_hz"]
        stats["grid"] = f"{nom} Hz"
        stats["grid_note"] = "Britain and Europe’s frequency" if nom == 50 else "North America’s frequency, not Britain’s"
        edits = case.check_edits()
        gaps = [g for g in edits.get("timeline_gaps") or [] if g.get("seconds_missing")]
        if gaps:
            stats["cuts"], stats["cuts_note"] = f"{gaps[0]['seconds_missing']:.0f} s", f"Missing at {narrate.mmss(gaps[0]['at_recording_s'])}"
        elif edits.get("phase_discontinuities"):
            j = edits["phase_discontinuities"]
            stats["cuts"], stats["cuts_note"] = str(len(j)), f"First at {narrate.mmss(j[0]['t_s'])}"
        else:
            stats["cuts"], stats["cuts_note"] = "None", "The hum runs unbroken"
        stats["when"], stats["when_note"] = "Undated", "No matching grid record"
    if m and a.get("mains_hum_detected"):
        p = m["combined_false_match_probability"]
        ts = parse_utc(m["best_recording_start_utc"]).timestamp()
        if p < 0.05:
            stats["when"] = narrate.london(ts, "%H:%M:%S")
            stats["when_note"] = narrate.london(ts, "%a %d %b %Y, %Z")
        else:
            stats["when_note"] = "No confident match"
        stats["odds"] = narrate.fluke(p)
    out["stats"] = stats

    # hum trace (seconds into the file)
    if tr is not None:
        k = _thin(len(tr.t))
        cuts = [j["t_s"] for j in (case.jumps or [])] + [c["t_s"] for c in case.timeline_cuts]
        out["hum"] = {"t": np.round(tr.t[k], 1).tolist(), "y": np.round(tr.deviation_mhz[k], 1).tolist(),
                      "cuts": cuts, "nominal": tr.nominal, "harmonic": tr.spec_harmonic}

    # recording laid on the grid record (UTC seconds)
    if m and case._match_obj is not None and m["combined_false_match_probability"] < 0.05:
        _, ref, _ = case._match_obj
        start = parse_utc(m["best_recording_start_utc"]).timestamp()
        end = max(parse_utc(p["implied_recording_start_utc"]).timestamp() + p["recording_to_s"] for p in m["pieces"])
        i0, w = ref.window(start - 60, end + 60)
        k = _thin(len(w))
        grid = {"t": (ref.t0 + (i0 + k) * ref.step_s).tolist(), "y": [None if not np.isfinite(v) else round(v * 1000, 1) for v in w[k]]}
        pieces = []
        for p in m["pieces"]:
            ps = parse_utc(p["implied_recording_start_utc"]).timestamp()
            sel = (tr.t >= p["recording_from_s"]) & (tr.t <= p["recording_to_s"])
            tt, yy = ps + tr.t[sel], tr.deviation_mhz[sel]
            _, rw = ref.window(tt[0], tt[-1])
            yy = yy + (np.nanmean(rw) * 1000 - yy.mean())  # device clocks shift absolute frequency slightly
            kk = _thin(len(tt), 600)
            pieces.append({"t": np.round(tt[kk], 1).tolist(), "y": np.round(yy[kk], 1).tolist()})
        missing = []
        for g, p, q in zip(m["edits_implied_by_timeline"], m["pieces"][:-1], m["pieces"][1:]):
            if g.get("seconds_missing"):
                missing.append([parse_utc(p["implied_recording_start_utc"]).timestamp() + g["at_recording_s"],
                                parse_utc(q["implied_recording_start_utc"]).timestamp() + g["at_recording_s"]])
        out["match"] = {"grid": grid, "pieces": pieces, "missing": missing, "nominal": ref.nominal,
                        "synthetic": ref.synthetic, "source": ref.source}

    out["custody"] = {
        "sha256": case.sha256,
        "model": analyst.model if analyst.llm else None,
        "reference": "; ".join(r.source for r in REFS),
        "synthetic": any(r.synthetic for r in REFS),
        "analysed_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    return out


# --------------------------------------------------------------------------- routes

@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION, "agent": bool(os.environ.get("NEBIUS_API_KEY")), "references": [r.coverage() for r in REFS],
            "ai_used_today": _ai_day["count"], "ai_daily_limit": AI_DAILY_LIMIT}


@app.get("/api/examples")
def examples():
    f = ROOT / "examples" / "claims.json"
    if not f.exists():
        return JSONResponse([])
    items = json.loads(f.read_text())
    return [{"title": e.get("title") or TITLES.get(Path(e["file"]).stem, Path(e["file"]).stem),
             "claim": e["claim"], "url": f"/examples/{e['file']}", "file": e["file"]} for e in items]


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@app.post("/api/analyse")
async def analyse(request: Request, file: UploadFile = File(...), claim: str = Form("")):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "That file is empty.")
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "That file is over 60 MB. Trim it to the part that matters.")
    claim = re.sub(r"\s+", " ", claim).strip()[:MAX_CLAIM]
    suffix = Path(file.filename or "").suffix.lower()
    suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,5}", suffix) else ".audio"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(raw)
    tmp.close()
    q: queue.Queue = queue.Queue()
    visitor = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    use_ai, why = (ai_allowed(visitor) if os.environ.get("NEBIUS_API_KEY") else (False, ""))
    step = lambda title, detail: q.put(("step", {"title": title, "detail": detail}))

    def work():
        started = time.time()
        got_slot = False
        try:
            if not _slots.acquire(blocking=False):
                step("Waiting for a free slot", "Other analyses are running. Yours starts in a moment.")
                if not _slots.acquire(timeout=240):
                    raise RuntimeError("busy")
            got_slot = True
            if why:
                step("Using the built-in report", f"AI paused: {why}. The physics still runs in full.")
            case = Case.open(tmp.name, REFS)
            seconds = len(case.x) / case.sr
            if seconds < MIN_SECONDS:
                raise ValueError(f"That recording is {seconds:.0f} seconds long. Gridprint needs at least "
                                 f"{MIN_SECONDS} seconds, and works best on two minutes or more.")
            if seconds > MAX_SECONDS:
                raise ValueError(f"That recording is {seconds / 60:.0f} minutes long. Trim it to the 30 minutes that matter.")
            analyst = Analyst(case, use_llm=use_ai, on_step=lambda s: step(s["title"], s["detail"]))
            report = analyst.investigate(claim)
            q.put(("result", build_payload(case, analyst, report, claim, time.time() - started)))
        except ValueError as e:  # expected problems with the file: say so plainly
            q.put(("error", {"message": str(e)}))
        except RuntimeError as e:
            msg = "Gridprint is busy right now. Try again in a minute." if str(e) == "busy" else \
                "Something went wrong analysing that file. Try another recording."
            q.put(("error", {"message": msg}))
        except Exception:  # noqa: BLE001 - log the detail for the operator, show the visitor a plain message
            import traceback
            traceback.print_exc()
            q.put(("error", {"message": "Something went wrong analysing that file. Try another recording."}))
        finally:
            if got_slot:
                _slots.release()
            q.put(None)
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    threading.Thread(target=work, daemon=True).start()

    def stream():
        yield _sse("step", {"title": "Received the recording", "detail": f"{len(raw) / 1e6:.1f} MB"})
        while True:
            try:
                item = q.get(timeout=10)
            except queue.Empty:
                yield ": keep-alive\n\n"  # stops proxies closing a quiet connection while the model thinks
                continue
            if item is None:
                break
            yield _sse(*item)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "7860")))
