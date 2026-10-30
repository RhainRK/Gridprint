"""The web server, end to end, in offline mode (no model or network needed)."""
import json

from fastapi.testclient import TestClient

import server


def _events(text):
    out = []
    for block in text.split("\n\n"):
        ev = data = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if ev:
            out.append((ev, data))
    return out


def test_pages_and_examples():
    c = TestClient(server.app)
    assert "Gridprint" in c.get("/").text
    assert c.get("/web/app.js").status_code == 200
    ex = c.get("/api/examples").json()
    assert ex and all({"title", "claim", "url"} <= set(e) for e in ex)


def test_analyse_streams_steps_then_result(monkeypatch):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    c = TestClient(server.app)
    ex = next(e for e in c.get("/api/examples").json() if e["file"].startswith("leaked-meeting"))
    audio = c.get(ex["url"]).content
    r = c.post("/api/analyse", files={"file": (ex["file"], audio, "audio/wav")}, data={"claim": ex["claim"]})
    events = _events(r.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "step" and kinds[-1] == "result" and kinds.count("step") >= 4
    res = events[-1][1]
    assert res["verdict"]["kind"] == "inconsistent"
    assert res["stats"]["cuts"].endswith("s")          # the 50 s cut is reported
    assert res["match"] and res["match"]["missing"]     # and drawn on the chart
    assert len(res["custody"]["sha256"]) == 64


def test_parse_verdict_handles_model_formatting():
    v = server.parse_verdict("## Verdict\n**Inconsistent with the claim.** The hum dates it to Thursday.\n## Next")
    assert v["kind"] == "inconsistent" and v["reason"].startswith("The hum")


def test_spending_guard_limits_ai_calls(monkeypatch):
    monkeypatch.setattr(server, "AI_DAILY_LIMIT", 3)
    monkeypatch.setattr(server, "AI_PER_VISITOR_PER_HOUR", 2)
    server._ai_day.update(date="", count=0)
    server._ai_visitors.clear()
    assert server.ai_allowed("a")[0] and server.ai_allowed("a")[0]
    assert not server.ai_allowed("a")[0]          # per-visitor hourly cap
    assert server.ai_allowed("b")[0]
    ok, why = server.ai_allowed("c")              # daily cap (3 used)
    assert not ok and "daily" in why
