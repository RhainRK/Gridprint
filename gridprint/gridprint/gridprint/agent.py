"""The forensic analyst: NVIDIA Nemotron on Nebius Token Factory, with Tavily for context.

The model never touches raw audio. It plans the investigation, turns a messy human claim
("leaked audio from the meeting on Tuesday afternoon") into a testable UTC window, calls
the deterministic physics tools, and writes up what they show, with honest uncertainty.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from . import narrate
from .forensics import Case

TOKEN_FACTORY_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")
DEFAULT_MODEL = os.environ.get("GRIDPRINT_MODEL", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")

SYSTEM = """You are Gridprint, a forensic audio analyst. You test claims about WHEN an audio recording was made, and whether it was edited, using Electrical Network Frequency (ENF) analysis: mains-powered equipment leaks a faint hum at the grid frequency into recordings, the grid frequency wanders randomly from second to second, and that wander is identical across a whole synchronous grid and logged by the grid operator. Matching the hum to the log timestamps the recording.

Today's date is {today}. Work in UTC. Great Britain uses GMT in winter and BST (UTC+1) from the last Sunday of March to the last Sunday of October.

Method (use the tools, do not guess):
1. Call analyse_recording first.
2. Work out the claimed time window. If the claim names an event without a precise time, call web_search to find when the event happened. If no claim is given, test against all available reference data.
3. Call list_reference_coverage, then match_grid over the claimed window padded generously (a date only: the whole day; a time: +-2 hours).
4. If the best match in the claimed window is weak (false_match_probability above 0.01) and the reference covers more time, call match_grid again over the full coverage to see where the recording really fits.
5. Call check_edits.
6. Write the report.

Rules: quote the statistics (correlation, sigma, false-match probability) and say what they mean in plain English. Never claim more certainty than the numbers give. Missing hum is NOT evidence of fakery on its own. A 60 Hz hum on a recording claimed to be from a 50 Hz country (or vice versa) is strong evidence against the claimed location. If the reference data is marked synthetic, say clearly that the result is a demonstration only. Do not invent tool results.

Final answer: a Markdown report with exactly these sections:
## Verdict
One line, starting with one of: "Consistent with the claim", "Inconsistent with the claim", "Inconclusive". Then one sentence why.
## What was claimed
## What the physics shows
## Was it edited?
## Caveats
## Next steps for an investigator
Keep it under 400 words. Write for a journalist or a court, not a signal-processing expert."""

TOOLS = [
    {"type": "function", "function": {
        "name": "analyse_recording",
        "description": "Detect mains hum, identify the grid (50 or 60 Hz), extract the ENF trace and find phase discontinuities. Call first.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "list_reference_coverage",
        "description": "List which grid-frequency reference data is available (grid, time span, resolution, whether synthetic).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "match_grid",
        "description": "Search the grid reference between start_utc and end_utc for when the recording started. Returns the best start time, correlation, sigma, look-elsewhere-corrected false-match probability, and per-piece timeline (which reveals cut-out audio).",
        "parameters": {"type": "object", "properties": {
            "start_utc": {"type": "string", "description": "ISO-8601 UTC, e.g. 2025-01-17T12:00:00Z"},
            "end_utc": {"type": "string", "description": "ISO-8601 UTC"}},
            "required": ["start_utc", "end_utc"]}}},
    {"type": "function", "function": {
        "name": "check_edits",
        "description": "Summarise evidence of editing: hum phase discontinuities, gaps in the matched timeline, displaced segments.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the live web (Tavily) for context about the claim, e.g. when an event took place.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
]


def _strip_think(text: str | None) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def _short(obj, n: int = 600) -> str:
    s = json.dumps(obj, default=str)
    return s if len(s) <= n else s[:n] + "…"


class Analyst:
    def __init__(self, case: Case, model: str | None = None, on_step=None, use_llm: bool = True):
        self.case = case
        self.model = model or DEFAULT_MODEL
        self.trace: list[dict] = []   # visible to the user: every step the agent took
        self.on_step = on_step        # called with each step as it happens (live progress in the UI)
        self.llm = None
        self.tavily = None
        if use_llm and os.environ.get("NEBIUS_API_KEY"):
            from openai import OpenAI
            self.llm = OpenAI(base_url=TOKEN_FACTORY_URL, api_key=os.environ["NEBIUS_API_KEY"])
        if os.environ.get("TAVILY_API_KEY"):
            from tavily import TavilyClient
            self.tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])

    # ------------------------------------------------------------------ tool dispatch
    def _call(self, name: str, args: dict):
        c = self.case
        try:
            if name == "analyse_recording":
                r = c.analyse_recording()
            elif name == "list_reference_coverage":
                r = c.list_reference_coverage()
            elif name == "match_grid":
                r = c.match_grid(args["start_utc"], args["end_utc"])
            elif name == "check_edits":
                r = c.check_edits()
            elif name == "web_search":
                if not self.tavily:
                    r = {"error": "web search unavailable (no TAVILY_API_KEY)"}
                else:
                    hits = self.tavily.search(args["query"], max_results=5)["results"]
                    r = [{"title": h["title"], "url": h["url"], "content": h["content"][:400]} for h in hits]
            else:
                r = {"error": f"unknown tool {name}"}
        except Exception as e:  # surface tool errors to the model rather than crashing the run
            r = {"error": f"{type(e).__name__}: {e}"}
        self._record(name, args, r)
        return r

    def _record(self, name: str, args: dict, r) -> None:
        step = {"tool": name, "args": args, "title": narrate.title(name, args),
                "detail": narrate.detail(name, args, r), "result": _short(r)}
        self.trace.append(step)
        if self.on_step:
            self.on_step(step)

    # ------------------------------------------------------------------ agent loop
    def investigate(self, claim: str, max_rounds: int = 10) -> str:
        if not self.llm:
            return self._offline_report(claim)
        today = datetime.now(timezone.utc).strftime("%A %d %B %Y")
        messages = [
            {"role": "system", "content": SYSTEM.format(today=today)},
            {"role": "user", "content": f"Claim about this recording: {claim.strip() or '(no claim given: find when it was recorded)'}"},
        ]
        extra = {}
        if os.environ.get("GRIDPRINT_DISABLE_THINKING", "1") == "1":
            extra = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}

        def ask():
            nonlocal extra
            try:
                return self.llm.chat.completions.create(
                    model=self.model, messages=messages, tools=TOOLS, temperature=0.2, max_tokens=2500, **extra)
            except Exception as e:  # a provider that rejects the thinking switch: drop it and retry once
                if extra and "400" in str(e):
                    extra = {}
                    return self.llm.chat.completions.create(
                        model=self.model, messages=messages, tools=TOOLS, temperature=0.2, max_tokens=2500)
                raise
        try:
            for _ in range(max_rounds):
                resp = ask()
                msg = resp.choices[0].message
                if not msg.tool_calls:
                    report = _strip_think(msg.content)
                    if "## Verdict" in report:
                        self._record("report", {}, f"Written by {self.model} on Nebius Token Factory.")
                        return report
                    messages.append({"role": "assistant", "content": msg.content or ""})
                    messages.append({"role": "user", "content": "Use the tools to finish the method, then write the report in the required format."})
                    continue
                messages.append({"role": "assistant", "content": _strip_think(msg.content),
                                 "tool_calls": [{"id": tc.id, "type": "function",
                                                 "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                                                for tc in msg.tool_calls]})
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = self._call(tc.function.name, args)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)[:6000]})
        except Exception as e:
            self._record("agent", {}, {"error": f"The agent stopped ({type(e).__name__}); running the fixed pipeline instead."})
            return self._pipeline_report(claim)
        return self._pipeline_report(claim)

    # ------------------------------------------------------------------ fallbacks (never leave the user empty-handed)
    def _run_pipeline(self, claim: str) -> dict:
        a = self._call("analyse_recording", {})
        cov = self._call("list_reference_coverage", {})
        m = None
        if a.get("mains_hum_detected"):
            refs = [r for r in cov["references"] if r["nominal_hz"] == a["grid_nominal_hz"]]
            if refs:
                m = self._call("match_grid", {"start_utc": refs[0]["from_utc"], "end_utc": refs[0]["to_utc"]})
        e = self._call("check_edits", {})
        return {"claim": claim, "analysis": a, "coverage": cov, "match": m, "edits": e}

    def _pipeline_report(self, claim: str) -> str:
        facts = self._run_pipeline(claim)
        if self.llm:
            try:
                resp = self.llm.chat.completions.create(model=self.model, temperature=0.2, max_tokens=1500, messages=[
                    {"role": "system", "content": SYSTEM.format(today=datetime.now(timezone.utc).strftime("%d %B %Y"))},
                    {"role": "user", "content": f"Claim: {claim}\n\nTool results (already run):\n{json.dumps(facts, default=str)[:12000]}\n\nWrite the report."}])
                self._record("report", {}, f"Written by {self.model} on Nebius Token Factory.")
                return _strip_think(resp.choices[0].message.content)
            except Exception as e:
                self._record("report", {}, f"Model unavailable ({type(e).__name__}); used the built-in template.")
        return self._template(facts)

    def _offline_report(self, claim: str) -> str:
        report = self._template(self._run_pipeline(claim))
        self._record("report", {}, "Written by Gridprint’s built-in template.")
        return report

    @staticmethod
    def _offline_verdict(claim: str, a: dict, m: dict | None, e: dict) -> str:
        c = (claim or "").lower()
        gb_words = ("london", "uk", "britain", "england", "scotland", "wales", "manchester", "birmingham",
                    "glasgow", "edinburgh", "cardiff", "leeds", "liverpool", "bristol", "canary wharf")
        if not a.get("mains_hum_detected"):
            return ("Inconclusive: there is no mains hum to timestamp. Missing hum is common on battery devices "
                    "and proves nothing on its own.")
        if a["grid_nominal_hz"] == 60:
            if any(w in c for w in gb_words):
                return "Inconsistent with the claim: the hum is at 60 Hz, so this was not recorded on the British grid."
            return "Inconclusive: the hum is at 60 Hz, and no 60 Hz grid records are loaded to date it."
        if not m or "error" in m:
            return "Inconclusive: no grid records cover this recording."
        if m["combined_false_match_probability"] >= 0.01:
            return "Inconclusive: the hum does not fit the grid record confidently enough to date the recording."
        found = datetime.fromisoformat(m["best_recording_start_utc"].replace("Z", "+00:00")).astimezone(narrate.LONDON)
        when = found.strftime("%a %d %b %Y at %H:%M")
        gaps = [g for g in m.get("edits_implied_by_timeline", []) if g.get("seconds_missing")]
        edited = e.get("edit_suspected")
        months = "january february march april may june july august september october november december".split()
        claimed = None
        for pat, order in ((r"(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(months) + r"),?\s+(\d{4})", "dmy"),
                           (r"(" + "|".join(months) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", "mdy"),
                           (r"(\d{1,2})/(\d{1,2})/(\d{4})", "uk")):
            hit = re.search(pat, c)
            if hit:
                g = hit.groups()
                try:
                    if order == "dmy":
                        claimed = datetime(int(g[2]), months.index(g[1]) + 1, int(g[0]))
                    elif order == "mdy":
                        claimed = datetime(int(g[2]), months.index(g[0]) + 1, int(g[1]))
                    else:  # UK numeric dates are day/month/year
                        claimed = datetime(int(g[2]), int(g[1]), int(g[0]))
                except ValueError:
                    claimed = None
                break
        hit = claimed
        if hit:
            if claimed.date() != found.date():
                return f"Inconsistent with the claim: the hum dates this recording to {when}, not {claimed:%a %d %b %Y}."
            t = re.search(r"(\d{1,2}):(\d{2})", c)
            if t and abs((int(t.group(1)) * 60 + int(t.group(2))) - (found.hour * 60 + found.minute)) > 45:
                return f"Inconsistent with the claim: right day, but the hum places it at {found:%H:%M}, not {t.group(0)}."
        if edited:
            cut = f"about {gaps[0]['seconds_missing']:.0f} s were cut out at {narrate.mmss(gaps[0]['at_recording_s'])}" if gaps \
                else "the hum shows a cut"
            return f"Inconsistent with the claim: the timing fits, but {cut}."
        if hit:
            return f"Consistent with the claim: the hum dates this recording to {when}, and it runs unbroken."
        return f"Consistent with the claim: the hum dates this recording to {when}, and it runs unbroken."

    @staticmethod
    def _template(f: dict) -> str:  # noqa: C901
        a, m, e = f["analysis"], f["match"], f["edits"]
        lines = ["## Verdict", Analyst._offline_verdict(f["claim"], a, m, e)]
        if not a.get("mains_hum_detected"):
            lines += ["", "## What the physics shows", a.get("note", "")]
            return "\n".join(lines)
        nom = a["grid_nominal_hz"]
        snr = ", ".join(f"{v:.0f} dB at {int(k) * nom} Hz" for k, v in a["hum_snr_db_by_harmonic"].items())
        lines += ["", "## What was claimed", f["claim"] or "Nothing was claimed, so Gridprint searched for when it was made.",
                  "", "## What the physics shows",
                  f"- The recording carries a {nom} Hz mains hum, the frequency used in {a['grids_with_this_frequency']}.",
                  f"- Hum strength above the background noise: {snr}."]
        if m and "error" not in m:
            an = m["anchor"]
            lines.append(f"- The hum fits the {m['reference']['grid']} grid record starting {m['best_recording_start_london']}, "
                         f"with a correlation of {an['peak_correlation']:.2f}. After allowing for every other moment searched, "
                         f"the chance of a coincidence is {narrate.fluke(m['combined_false_match_probability']).lower()}.")
            if m.get("warning"):
                lines.append("- **This result uses synthetic test grid data, so it is a demonstration, not evidence.**")
        lines += ["", "## Was it edited?"]
        if e.get("edit_suspected"):
            gaps = [g for g in (m or {}).get("edits_implied_by_timeline", []) if g.get("seconds_missing")]
            for g in gaps:
                lines.append(f"- About {g['seconds_missing']:.0f} seconds of audio are missing, "
                             f"{narrate.mmss(g['at_recording_s'])} into the file.")
            for j in e.get("phase_discontinuities", []):
                n = len(j.get("harmonics_agreeing", []))
                lines.append(f"- The hum’s rhythm jumps at {narrate.mmss(j['t_s'])}"
                             + (f", seen on {n} harmonics at once" if n > 1 else "") + ", the signature of a cut.")
        else:
            lines.append("- No sign of editing: the hum runs unbroken from start to finish.")
        lines += ["", "## Caveats",
                  "- Gridprint depends on the microphone picking up mains hum; accuracy depends on how strong the hum is and how long the recording runs.",
                  "- A skilled forger could add an artificial hum, but matching it to the real grid record second by second is much harder.",
                  "", "## Next steps for an investigator",
                  "- Ask for the original file from the device that made it, and compare its fingerprint with this one.",
                  "- Check the timing against other evidence, such as phone records or witnesses."]
        return "\n".join(lines)
