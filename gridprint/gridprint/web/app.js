/* Gridprint front end. No framework, no build step. */
(() => {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const SVGNS = "http://www.w3.org/2000/svg";
  const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function el(tag, attrs = {}, kids = []) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else n.setAttribute(k, v);
    }
    for (const k of [].concat(kids)) if (k !== null && k !== undefined) n.append(k);
    return n;
  }
  function svg(tag, attrs = {}) {
    const n = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  }
  const mmss = (s) => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  const capitalise = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const scrollTo = (node) => node.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });

  const ui = {
    drop: $("#drop"), input: $("#fileInput"), fileRow: $("#fileRow"), fileName: $("#fileName"), fileSize: $("#fileSize"),
    player: $("#player"), clear: $("#clearFile"), claim: $("#claim"), go: $("#go"), goHint: $("#goHint"),
    cases: $("#cases"), casesSection: $(".cases"), run: $("#run"), steps: $("#steps"),
    verdict: $("#verdict"), vLabel: $("#vLabel"), vReason: $("#vReason"),
    matchCard: $("#matchCard"), matchChart: $("#matchChart"), matchNote: $("#matchNote"), legMiss: $("#legMiss"),
    stats: $("#stats"), humCard: $("#humCard"), humChart: $("#humChart"), humNote: $("#humNote"),
    report: $("#report"), custody: $("#custody"), recBtn: $("#recBtn"), recTime: $("#recTime"),
    claimEcho: $("#claimEcho"), stepsTitle: $("#stepsTitle"), modeNote: $("#modeNote"), againRow: $("#againRow"), again: $("#again"),
  };
  const state = { file: null, url: null, running: false, last: null, recorder: null, agent: true };

  /* ------------------------------------------------------------------ choosing a recording */
  function setFile(file, claim) {
    state.file = file;
    if (state.url) URL.revokeObjectURL(state.url);
    state.url = URL.createObjectURL(file);
    ui.player.src = state.url;
    ui.fileName.textContent = file.name;
    ui.fileSize.textContent = file.size > 1e6 ? `${(file.size / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(file.size / 1e3))} KB`;
    ui.fileRow.hidden = false;
    if (claim !== undefined) ui.claim.value = claim;
    refreshGo();
  }
  function clearFile() {
    state.file = null;
    if (state.url) URL.revokeObjectURL(state.url);
    state.url = null;
    ui.player.removeAttribute("src");
    ui.fileRow.hidden = true;
    ui.input.value = "";
    $$(".case").forEach((c) => c.classList.remove("picked"));
    refreshGo();
  }
  function refreshGo() {
    ui.go.disabled = !state.file || state.running;
    ui.goHint.textContent = state.running ? "Usually under a minute."
      : state.file ? "Ready when you are." : "Add a recording to begin.";
  }

  ui.input.addEventListener("change", () => { if (ui.input.files[0]) setFile(ui.input.files[0]); });
  ui.drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ui.input.click(); } });
  ["dragenter", "dragover"].forEach((t) => ui.drop.addEventListener(t, (e) => { e.preventDefault(); ui.drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => ui.drop.addEventListener(t, (e) => { e.preventDefault(); ui.drop.classList.remove("over"); }));
  ui.drop.addEventListener("drop", (e) => { const f = e.dataTransfer && e.dataTransfer.files[0]; if (f) setFile(f); });
  ui.clear.addEventListener("click", clearFile);

  $$(".seg button").forEach((b) => b.addEventListener("click", () => {
    $$(".seg button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    $("#pane-upload").hidden = b.dataset.mode !== "upload";
    $("#pane-record").hidden = b.dataset.mode !== "record";
  }));

  /* ------------------------------------------------------------------ recording in the browser */
  let recTimer = null;
  ui.recBtn.addEventListener("click", async () => {
    if (state.recorder && state.recorder.state === "recording") { state.recorder.stop(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      ui.recTime.textContent = "Recording isn’t available in this browser. Upload a file instead.";
      return;
    }
    try {
      // Browsers filter out hum by default. Turn the filters off: the hum is the evidence.
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
      const rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
      const chunks = [];
      const started = Date.now();
      rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        clearInterval(recTimer);
        ui.recBtn.classList.remove("on");
        ui.recBtn.setAttribute("aria-label", "Start recording");
        const mime = rec.mimeType || type || "audio/webm";
        const ext = mime.includes("mp4") ? "m4a" : mime.includes("ogg") ? "ogg" : "webm";
        setFile(new File(chunks, `recording.${ext}`, { type: mime }));
      };
      rec.start(1000);
      state.recorder = rec;
      ui.recBtn.classList.add("on");
      ui.recBtn.setAttribute("aria-label", "Stop recording");
      ui.recTime.textContent = "0:00";
      recTimer = setInterval(() => { ui.recTime.textContent = mmss((Date.now() - started) / 1000); }, 250);
    } catch (err) {
      ui.recTime.textContent = "Microphone access is blocked. Allow it in your browser settings, then try again.";
    }
  });

  /* ------------------------------------------------------------------ example cases */
  async function loadCases() {
    try {
      const list = await (await fetch("/api/examples")).json();
      if (!list.length) { ui.casesSection.hidden = true; return; }
      ui.cases.replaceChildren(...list.map((c) => {
        const b = el("button", { class: "case", type: "button" }, [
          el("span", { class: "case-title", text: c.title }),
          el("span", { class: "case-claim", text: c.claim }),
        ]);
        b.addEventListener("click", async () => {
          $$(".case").forEach((x) => x.classList.toggle("picked", x === b));
          b.classList.add("loading");
          try {
            const blob = await (await fetch(c.url)).blob();
            setFile(new File([blob], c.file, { type: blob.type || "audio/wav" }), c.claim);
            $(".composer").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
          } finally { b.classList.remove("loading"); }
        });
        return b;
      }));
    } catch (e) { ui.casesSection.hidden = true; }
  }

  /* ------------------------------------------------------------------ running an analysis */
  function resetRun() {
    [ui.verdict, ui.matchCard, ui.stats, ui.humCard, ui.report, ui.custody, ui.claimEcho, ui.againRow].forEach((n) => { n.hidden = true; });
    ui.stepsTitle.textContent = state.agent ? "How Nemotron is investigating" : "How Gridprint is investigating";
    ui.steps.replaceChildren();
    ui.matchChart.replaceChildren();
    ui.humChart.replaceChildren();
    ui.stats.classList.remove("shown");
    $$(".run > .error").forEach((n) => n.remove());
  }
  function stepItem(title, detail, cls = "") {
    return el("li", { class: `step ${cls}`.trim() }, [
      el("span", { class: "step-icon", "aria-hidden": "true" }),
      el("div", {}, [el("p", { class: "step-title", text: title }), el("p", { class: "step-detail", text: detail || "" })]),
    ]);
  }
  function addStep(s) {
    const warn = /couldn|error|stopped|unavailable/i.test(`${s.title} ${s.detail}`);
    ui.steps.insertBefore(stepItem(s.title, s.detail, warn ? "warn" : ""), ui.steps.querySelector(".working"));
  }
  function showError(msg) {
    ui.run.prepend(el("p", { class: "error", role: "alert", text: msg }));
  }

  async function analyse() {
    if (!state.file || state.running) return;
    state.running = true;
    ui.go.textContent = "Analysing…";
    refreshGo();
    resetRun();
    ui.run.hidden = false;
    const thinking = state.agent ? "Nemotron is deciding what to check next" : "Running the analysis";
    const working = stepItem("Working", `${thinking}.`, "working");
    ui.steps.append(working);
    const t0 = Date.now();
    const tick = setInterval(() => {
      const s = Math.round((Date.now() - t0) / 1000);
      working.querySelector(".step-detail").textContent = `${thinking}. ${s} second${s === 1 ? "" : "s"} so far.`;
    }, 1000);
    scrollTo(ui.run);
    const form = new FormData();
    form.append("file", state.file, state.file.name);
    form.append("claim", ui.claim.value.trim());
    try {
      const res = await fetch("/api/analyse", { method: "POST", body: form });
      if (!res.ok) {
        let msg = `The server couldn’t take that file (error ${res.status}).`;
        try { msg = (await res.json()).detail || msg; } catch (e) { /* keep the default message */ }
        throw new Error(msg);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) { handleEvent(buf.slice(0, i)); buf = buf.slice(i + 2); }
      }
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      clearInterval(tick);
      working.remove();
      state.running = false;
      ui.go.textContent = "Analyse recording";
      refreshGo();
    }
  }
  ui.go.addEventListener("click", analyse);
  ui.claim.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); analyse(); } });
  ui.again.addEventListener("click", () => {
    $(".composer").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
    ui.drop.focus({ preventScroll: true });
  });

  function handleEvent(block) {
    let ev = "message", data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) ev = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) return;
    let d;
    try { d = JSON.parse(data); } catch (e) { return; }
    if (ev === "step") addStep(d);
    else if (ev === "result") showResult(d, true);
    else if (ev === "error") showError(d.message);
  }

  /* ------------------------------------------------------------------ results */
  function showResult(r, animate) {
    state.last = r;
    ui.stepsTitle.textContent = r.mode === "agent" ? "How Nemotron investigated" : "How Gridprint investigated";
    ui.claimEcho.textContent = r.claim ? `The claim: “${r.claim}”` : "No claim given: Gridprint searched for when it was made.";
    ui.claimEcho.hidden = false;
    ui.againRow.hidden = false;
    const v = r.verdict || {};
    ui.verdict.dataset.kind = v.kind || "unknown";
    ui.vLabel.textContent = v.label || "Analysis complete";
    ui.vReason.textContent = capitalise(v.reason || "");
    ui.verdict.hidden = false;

    const s = r.stats || {};
    const pairs = [["when", "when_note"], ["odds", "odds_note"], ["cuts", "cuts_note"], ["grid", "grid_note"]];
    ui.stats.replaceChildren(...pairs.map(([val, note]) => el("div", { class: "stat" }, [
      el("dt", { text: s[note] || "" }), el("dd", { text: s[val] || "–" }),
    ])));
    ui.stats.hidden = false;

    if (r.match) {
      ui.matchCard.hidden = false;
      ui.legMiss.hidden = !(r.match.missing && r.match.missing.length);
      ui.matchNote.textContent = r.match.synthetic
        ? "Synthetic test grid data: a demonstration, not evidence."
        : `Grid record: ${r.match.source}.`;
    } else ui.matchCard.hidden = true;

    if (r.hum) {
      ui.humCard.hidden = false;
      ui.humNote.textContent = `Millihertz away from ${r.hum.nominal} Hz, read from the ${r.hum.harmonic * r.hum.nominal} Hz harmonic.`
        + (r.hum.cuts.length ? " Yellow marks a cut." : "");
    } else ui.humCard.hidden = true;

    ui.report.replaceChildren(el("h3", { class: "report-head", text: "Full report" }), ...renderMarkdown(stripVerdict(r.report_md || "")));
    ui.report.hidden = false;
    renderCustody(r);
    drawAll(animate);
    scrollTo(ui.claimEcho);
  }

  function drawAll(animate) {
    const r = state.last;
    if (!r) return;
    if (r.match) drawMatch(r.match);
    if (r.hum) drawHum(r.hum);
    const lock = () => { $$("#matchChart .rec").forEach((g) => g.classList.add("locked")); ui.stats.classList.add("shown"); };
    if (animate && !reduceMotion && r.match) requestAnimationFrame(() => requestAnimationFrame(lock));
    else { $$("#matchChart .rec").forEach((g) => { g.style.transition = "none"; }); lock(); }
  }
  let resizeTimer = null;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => drawAll(false), 150); });

  function renderCustody(r) {
    const c = r.custody || {};
    const who = c.model ? `Reasoning by ${c.model} on Nebius Token Factory.` : "Offline mode: built-in report template.";
    const dl = el("button", { type: "button", class: "as-link", text: "Download report" });
    dl.addEventListener("click", () => {
      const text = r.report_download || `${r.report_md}\n\nFile SHA-256: ${c.sha256}\n${who}\nGrid data: ${c.reference}\n`;
      const a = el("a", { href: URL.createObjectURL(new Blob([text], { type: "text/markdown" })), download: `gridprint-${(c.sha256 || "report").slice(0, 8)}.md` });
      document.body.append(a); a.click(); a.remove();
    });
    ui.custody.replaceChildren(
      el("p", {}, ["File fingerprint (SHA-256) ", el("span", { class: "hash", text: c.sha256 || "" })]),
      el("p", { text: `${who} Grid data: ${c.reference}. Analysed ${c.analysed_utc}${r.elapsed_s ? `, in ${Math.round(r.elapsed_s)} s` : ""}.` }),
      el("p", {}, [dl]),
    );
    ui.custody.hidden = false;
  }

  /* ------------------------------------------------------------------ markdown (escaped first, so model output can't inject HTML) */
  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*(?!\s)([^*]+?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  function stripVerdict(md) { return md.replace(/##\s*Verdict\s*\n+[\s\S]*?(?=\n\s*##|$)/, "").trim(); }
  function renderMarkdown(md) {
    const out = [];
    let list = null, para = [];
    const html = (tag, s) => { const n = document.createElement(tag); n.innerHTML = inline(s); return n; };
    const flush = () => { if (para.length) { out.push(html("p", para.join(" "))); para = []; } };
    for (const raw of md.split("\n")) {
      const line = raw.trimEnd();
      const bullet = line.match(/^\s*[-*]\s+(.*)$/);
      const num = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (/^#{1,6}\s/.test(line)) { flush(); list = null; out.push(html("h4", line.replace(/^#+\s*/, ""))); }
      else if (bullet || num) {
        flush();
        const tag = bullet ? "ul" : "ol";
        if (!list || list.tagName.toLowerCase() !== tag) { list = document.createElement(tag); out.push(list); }
        list.append(html("li", (bullet || num)[1]));
      } else if (!line.trim()) { flush(); list = null; }
      else { list = null; para.push(line.trim()); }
    }
    flush();
    return out;
  }

  /* ------------------------------------------------------------------ charts (plain SVG) */
  function niceStep(span, target, steps) { for (const s of steps) if (span / s <= target) return s; return steps[steps.length - 1]; }
  function yScale(values) {
    const v = values.filter((x) => x !== null && Number.isFinite(x));
    let lo = Math.min(...v), hi = Math.max(...v);
    if (!Number.isFinite(lo)) { lo = -50; hi = 50; }
    const pad = Math.max(5, (hi - lo) * 0.12);
    lo -= pad; hi += pad;
    const step = niceStep(hi - lo, 5, [5, 10, 20, 25, 50, 100, 200, 250, 500]);
    const ticks = [];
    for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) {
      ticks.push({ v: t, label: t > 0 ? `+${t}` : t < 0 ? `−${Math.abs(t)}` : "0" });
    }
    return { min: lo, max: hi, ticks };
  }
  const clock = (withSeconds) => new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/London", hour: "2-digit", minute: "2-digit", ...(withSeconds ? { second: "2-digit" } : {}),
  });

  function drawChart(box, o) {
    const W = Math.max(280, Math.round(box.clientWidth || box.getBoundingClientRect().width || 640));
    const H = o.height;
    const m = { t: 14, r: 6, b: 26, l: 40 };
    const sx = (v) => m.l + ((v - o.x.min) / ((o.x.max - o.x.min) || 1)) * (W - m.l - m.r);
    const sy = (v) => m.t + (1 - (v - o.y.min) / ((o.y.max - o.y.min) || 1)) * (H - m.t - m.b);
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": o.label });
    const defs = svg("defs");
    const pat = svg("pattern", { id: `${o.id}-hatch`, width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
    pat.append(svg("line", { x1: 0, y1: 0, x2: 0, y2: 6, class: "hatch" }));
    defs.append(pat);
    root.append(defs);
    const unit = svg("text", { x: m.l - 8, y: 8, "text-anchor": "end", class: "tick" });
    unit.textContent = "mHz";
    root.append(unit);
    for (const t of o.y.ticks) {
      root.append(svg("line", { x1: m.l, x2: W - m.r, y1: sy(t.v), y2: sy(t.v), class: "gridline" }));
      const tx = svg("text", { x: m.l - 8, y: sy(t.v) + 4, "text-anchor": "end", class: "tick" });
      tx.textContent = t.label;
      root.append(tx);
    }
    for (const t of o.x.ticks) {
      const x = sx(t.v);
      if (x < m.l + 12 || x > W - m.r - 12) continue;
      const tx = svg("text", { x, y: H - 6, "text-anchor": "middle", class: "tick" });
      tx.textContent = t.label;
      root.append(tx);
    }
    for (const b of o.bands || []) {
      const x0 = sx(b[0]), w = Math.max(3, sx(b[1]) - x0);
      root.append(svg("rect", { x: x0, y: m.t, width: w, height: H - m.t - m.b, class: "band" }));
      root.append(svg("rect", { x: x0, y: m.t, width: w, height: H - m.t - m.b, fill: `url(#${o.id}-hatch)` }));
    }
    for (const L of o.lines) {
      const g = svg("g", L.group ? { class: L.group } : {});
      let d = "", pen = false;
      for (let i = 0; i < L.x.length; i++) {
        const y = L.y[i];
        if (y === null || !Number.isFinite(y)) { pen = false; continue; }
        d += `${pen ? "L" : "M"}${sx(L.x[i]).toFixed(1)} ${sy(y).toFixed(1)}`;
        pen = true;
      }
      g.append(svg("path", { d, class: L.cls }));
      root.append(g);
    }
    box.replaceChildren(root);
  }

  function drawMatch(mt) {
    const xs = mt.grid.t;
    const x0 = xs[0], x1 = xs[xs.length - 1];
    const step = niceStep(x1 - x0, 6, [10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]);
    const fmt = clock(step < 60);
    const ticks = [];
    for (let t = Math.ceil(x0 / step) * step; t <= x1; t += step) ticks.push({ v: t, label: fmt.format(new Date(t * 1000)) });
    const allY = mt.grid.y.concat(...mt.pieces.map((p) => p.y));
    drawChart(ui.matchChart, {
      id: "match", label: "The recording’s hum overlaid on the grid frequency record",
      height: ui.matchChart.clientWidth < 560 ? 220 : 280,
      x: { min: x0, max: x1, ticks }, y: yScale(allY), bands: mt.missing,
      lines: [{ x: mt.grid.t, y: mt.grid.y, cls: "line-grid" },
        ...mt.pieces.map((p) => ({ x: p.t, y: p.y, cls: "line-rec", group: "rec" }))],
    });
  }

  function drawHum(h) {
    const x0 = h.t[0], x1 = h.t[h.t.length - 1];
    const step = niceStep(x1 - x0, 6, [10, 15, 30, 60, 120, 300, 600]);
    const ticks = [];
    for (let t = Math.ceil(x0 / step) * step; t <= x1; t += step) ticks.push({ v: t, label: mmss(t) });
    drawChart(ui.humChart, {
      id: "hum", label: "The hum frequency through the recording", height: 170,
      x: { min: x0, max: x1, ticks }, y: yScale(h.y),
      bands: h.cuts.map((c) => [c - 1.5, c + 1.5]),
      lines: [{ x: h.t, y: h.y, cls: "line-rec" }],
    });
  }

  window.__gridprint = { showResult, renderMarkdown, handleEvent };  // used by tests
  async function loadHealth() {
    try {
      const h = await (await fetch("/api/health")).json();
      state.agent = !!h.agent;
      if (!h.agent) {
        ui.modeNote.textContent = "Running without an AI key: the physics runs in full, and reports come from the built-in template.";
        ui.modeNote.hidden = false;
      }
    } catch (e) { /* the page still works without it */ }
  }

  loadHealth();
  loadCases();
  refreshGo();
})();
