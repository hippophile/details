// Analyse page: recording header + player, New run, progress (SSE with polling fallback), runs list, transcript.
(function () {
  const { fmtClock, fmtWall, seekTo, isActivateKey, api, el, pickRun, startBody, freshRun, transcriptText, paragraphs, transcriptView } = window.AnalyseApi;
  const $ = (id) => document.getElementById(id);
  const rec = new URLSearchParams(location.search).get("rec");
  const player = $("player"), modelSel = $("model"), startBtn = $("start"), reason = $("reason");
  let models = [], runs = [], selected = null, segEls = [], watching = null, tick = null;
  const ICON = { done: "✓", failed: "✕", cancelled: "■", queued: "…", running: "…" };

  function say(msg, bad) { $("status").textContent = msg || ""; $("status").style.color = bad ? "var(--bad)" : ""; }
  function confirmDlg(msg, yes) {
    return new Promise((resolve) => {
      const dlg = $("confirmDlg");
      $("confirmMsg").textContent = msg;
      $("confirmYes").textContent = yes;
      const done = (v) => { dlg.close(); resolve(v); };
      $("confirmYes").onclick = () => done(true);
      $("confirmNo").onclick = () => done(false);
      dlg.oncancel = () => resolve(false);
      dlg.showModal();
    });
  }

  // ---- new run ------------------------------------------------------------------------------------------
  function syncStart() {
    const m = models.find((x) => x.id === modelSel.value);
    const bad = !m || !m.installed;
    startBtn.disabled = bad || !!watching;
    reason.textContent = !m ? "No speech models are configured." : m.installed ? "" :
      `${m.label} is not available: ${m.unavailable_reason || "unknown reason"}${m.hint ? " (" + m.hint + ")" : ""}`;
  }
  async function loadModels() {
    models = await api("/api/models?kind=stt");
    modelSel.replaceChildren();
    for (const m of models) {
      const o = el("option", { value: m.id, disabled: !m.installed }, m.installed ? m.label : `${m.label} (not available)`);
      modelSel.appendChild(o);
    }
    const first = models.find((m) => m.installed);
    if (first) modelSel.value = first.id;
    syncStart();
  }
  modelSel.onchange = syncStart;
  // "Also spoken" boxes: the codes come from the server, the names from the browser (each in its own language)
  const alsoBoxes = [];
  const alsoTicked = () => alsoBoxes.filter((b) => b.checked).map((b) => b.value);
  function syncAlso() { for (const b of alsoBoxes) b.disabled = $("lang").value === "auto"; }
  async function loadAlso() {
    const { extra } = await api("/api/models?kind=stt_languages");
    for (const code of extra) {
      let name = code;
      try { name = new Intl.DisplayNames([code], { type: "language" }).of(code); } catch (e) { /* unknown code: show it */ }
      const box = el("input", { type: "checkbox", value: code });
      alsoBoxes.push(box);
      const lab = el("label");
      lab.append(box, " " + name.charAt(0).toUpperCase() + name.slice(1));
      $("also").appendChild(lab);
    }
    $("alsoWrap").hidden = !extra.length;
    syncAlso();
  }
  $("lang").onchange = syncAlso;
  startBtn.onclick = async () => {
    startBtn.disabled = true;
    try {
      const r = await api(`/api/recordings/${rec}/stt-runs`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(startBody(modelSel.value, $("lang").value, $("hint").checked, alsoTicked())) });
      say("");
      await loadRuns();
      watch(r.job_id);
    } catch (e) { say("Could not start: " + e.message, true); syncStart(); }
  };

  // ---- progress -----------------------------------------------------------------------------------------
  function show(j) {
    const pct = j.step_progress != null ? j.step_progress : j.pct;
    $("stage").textContent = j.status === "queued" ? "Waiting in the queue" : (j.message || j.msg || j.step || j.status);
    $("pct").textContent = pct != null ? Math.round(pct * 100) + "%" : "";
    $("barfill").style.width = pct != null ? Math.round(pct * 100) + "%" : "0";
  }
  function stopWatching() {
    if (watching) { if (watching.es) watching.es.close(); clearInterval(watching.poll); }
    clearInterval(tick);
    watching = null;
    if (window.Insights) window.Insights.detach($("progress"));
    $("progress").hidden = true;
    syncStart();
  }
  async function finished(j) {
    const onFinish = watching && watching.onFinish;
    stopWatching();
    await loadRuns();
    say(j.status === "done" ? "Run finished." : `Run ${j.status}${j.error ? ": " + j.error : ""}`, j.status !== "done");
    const r = runs.find((x) => x.job_id === j.id);
    if (r && j.status === "done") select(r);
    if (onFinish) onFinish(j);
  }
  // onFinish (optional): called with the finished job; the speaker card (speakers.js) watches its diarize job this way
  function watch(jobId, onFinish) {
    if (watching && watching.id === jobId) return;
    stopWatching();
    watching = { id: jobId, es: null, poll: null, onFinish };
    $("progress").hidden = false;
    if (window.Insights) window.Insights.attach($("progress"), jobId);
    startBtn.disabled = true;
    const t0 = Date.now();
    tick = setInterval(() => { $("elapsed").textContent = fmtClock(Date.now() - t0); }, 1000);
    const apply = (j) => { if (!watching || watching.id !== jobId) return; show(j); if (["done", "failed", "cancelled"].includes(j.status)) finished({ ...j, id: jobId }); };
    const poll = async () => { try { apply(await api(`/api/jobs/${jobId}`)); } catch (e) { /* retry on the next tick */ } };
    api(`/api/jobs/${jobId}`).then((j) => { if (j.started_at) { const s = Date.parse(j.started_at); $("elapsed").textContent = fmtClock(Date.now() - s); clearInterval(tick); tick = setInterval(() => { $("elapsed").textContent = fmtClock(Date.now() - s); }, 1000); } apply(j); }).catch(() => {});
    if (window.EventSource) {
      const es = new EventSource(`/api/jobs/${jobId}/events`);
      watching.es = es;
      es.onmessage = (ev) => { try { apply(JSON.parse(ev.data)); } catch (e) { /* ignore a bad frame */ } };
      es.onerror = () => {      // SSE dropped: close it and poll every 2 s instead
        es.close();
        if (watching && watching.id === jobId && !watching.poll) watching.poll = setInterval(poll, 2000);
      };
    } else watching.poll = setInterval(poll, 2000);
  }
  $("cancel").onclick = async () => {
    if (!watching || !await confirmDlg("Cancel this run? The work done so far is kept for a retry.", "Cancel run")) return;
    try { await api(`/api/jobs/${watching.id}/cancel`, { method: "POST" }); say("Cancelling..."); }
    catch (e) { say("Cancel failed: " + e.message, true); }
  };

  // ---- runs ---------------------------------------------------------------------------------------------
  async function loadRuns() {
    runs = await api(`/api/recordings/${rec}/stt-runs`);
    const fresh = freshRun(runs, selected);
    if (fresh) { selected = fresh; $("transcriptTitle").textContent = "Transcript: " + fresh.label; }
    const ul = $("runs");
    ul.replaceChildren();
    $("runsEmpty").hidden = runs.length > 0;
    for (const r of runs) {
      const li = el("li", { className: "run" + (selected && selected.id === r.id ? " sel" : ""), tabIndex: 0 });
      li.setAttribute("role", "button");
      li.appendChild(el("span", { className: "label" }, r.label));
      li.appendChild(el("span", { className: "st " + r.status }, `${ICON[r.status] || ""} ${r.status}`));
      if (r.status === "done" || r.status === "failed" || r.status === "cancelled") {
        const del = el("button", { type: "button", className: "btn sm ghost" }, "Delete");
        del.onclick = async (ev) => {
          ev.stopPropagation();
          if (!await confirmDlg(`Delete run ${r.label}?`, "Delete")) return;
          try { await api(`/api/stt-runs/${r.id}`, { method: "DELETE" }); if (selected && selected.id === r.id) clearTranscript(); await loadRuns(); }
          catch (e) { say("Delete failed: " + e.message, true); }
        };
        li.appendChild(del);
      }
      if (r.error) li.appendChild(el("div", { className: "run-err" }, r.error));
      li.onclick = () => select(r);
      li.onkeydown = (ev) => { if (ev.target === li && isActivateKey(ev)) { ev.preventDefault(); select(r); } };
      ul.appendChild(li);
      if (window.Outputs) window.Outputs.decorate(li, r);
      if (window.AnalyseCompare) window.AnalyseCompare.decorateRun(li, r);
    }
    if (window.Outputs) window.Outputs.setRuns(runs, selected);
    const live = runs.find((r) => (r.status === "queued" || r.status === "running") && r.job_id);
    if (live) watch(live.job_id);
  }

  // ---- transcript ---------------------------------------------------------------------------------------
  function clearTranscript() {
    selected = null; segEls = []; copyable = null; shown = null;
    $("transcript").replaceChildren(el("div", { className: "meta" }, "Select a run to read its transcript."));
    $("transcriptTitle").textContent = "Transcript";
  }
  let copyable = null;    // {segs} or {text}: what the Copy transcript button puts on the clipboard (read-only, nothing is stored)
  $("copyTranscript").onclick = async () => {      // never greyed out: if the page has not kept the lines, fetch them for the selected run
    try {
      let src = copyable;
      if (!src) {
        if (!selected || selected.status !== "done") { say("Pick a finished run first.", true); return; }
        src = { segs: await api(`/api/stt-runs/${selected.id}/segments`) };
        if (!src.segs.length) src = { text: (await api(`/api/stt-runs/${selected.id}`)).text || "" };
      }
      const text = src.segs ? transcriptText(src.segs, { timestamps: $("copyStamps").checked }) : src.text;
      await navigator.clipboard.writeText(text); say("Copied.");
    }
    catch (e) { say("Copy failed: " + e.message, true); }
  };
  let selToken = 0;       // a newer select() (e.g. the speaker card picking a run) makes an older one stop before it draws
  async function select(r) {
    const token = ++selToken;
    selected = r;
    if (window.Speakers) window.Speakers.transcriptChanged(r);     // the turns list of the speaker card reads this transcript
    if (window.Outputs) window.Outputs.pick(r);
    for (const li of $("runs").children) li.classList.remove("sel");
    await loadRuns_mark(r);
    const box = $("transcript");
    box.replaceChildren();
    copyable = null; shown = null;
    $("transcriptTitle").textContent = "Transcript: " + r.label;
    if (r.status !== "done") { box.appendChild(el("div", { className: "meta" }, r.error || `This run is ${r.status}.`)); return; }
    const dr = window.Speakers && window.Speakers.pickedId();         // speaker run picked: rows get a speaker chip
    let segs;
    try { segs = await api(`/api/stt-runs/${r.id}/segments` + (dr ? `?diarization_run_id=${encodeURIComponent(dr)}` : "")); }
    catch (e) { if (!dr) throw e; segs = await api(`/api/stt-runs/${r.id}/segments`); }
    if (token !== selToken) return;
    segEls = [];
    if (segs.length) copyable = { segs };
    if (!segs.length) {
      const full = await api(`/api/stt-runs/${r.id}`);
      if (token !== selToken) return;
      box.appendChild(el("div", { className: "meta" }, "No timestamps for this run (imported from v1)."));
      box.appendChild(el("div", { className: "plain" }, full.text || ""));
      copyable = { text: full.text || "" };
      return;
    }
    shown = segs;
    draw();
  }
  // Transcript view: Paragraphs (default) or Lines, kept in localStorage. Both fill segEls with the elements that seek and highlight.
  let shown = null;
  const viewKey = "analyse.transcriptView";
  let view = "paragraphs";
  try { view = transcriptView(localStorage.getItem(viewKey)); } catch (e) { /* storage blocked: default */ }
  function syncToggle() {
    $("viewParas").setAttribute("aria-pressed", String(view === "paragraphs"));
    $("viewLines").setAttribute("aria-pressed", String(view === "lines"));
  }
  function setView(v) {
    view = v; syncToggle();
    try { localStorage.setItem(viewKey, v); } catch (e) { /* storage blocked: not remembered */ }
    if (shown) draw();
  }
  $("viewParas").onclick = () => setView("paragraphs");
  $("viewLines").onclick = () => setView("lines");
  syncToggle();
  function draw() {
    const box = $("transcript");
    box.replaceChildren();
    segEls = [];
    if (view === "paragraphs") {
      for (const p of paragraphs(shown)) {
        const row = el("div", { className: "para" });
        row.appendChild(el("span", { className: "t" }, `[${fmtClock(p.start_ms)}]`));
        if (p.segs[0].speaker_name !== undefined && window.Speakers) row.appendChild(window.Speakers.chip(p.segs[0]));
        const x = el("span", { className: "x" });
        p.segs.forEach((s, i) => {
          const sp = el("span", { className: "s", tabIndex: 0 }, (i ? " " : "") + s.text);
          sp.setAttribute("role", "button");
          sp.dataset.start = s.start_ms; sp.dataset.end = s.end_ms;
          sp.onclick = () => seekTo(player, s.start_ms);
          sp.onkeydown = (ev) => { if (isActivateKey(ev)) { ev.preventDefault(); seekTo(player, s.start_ms); } };
          x.appendChild(sp);
          segEls.push(sp);
        });
        row.appendChild(x);
        box.appendChild(row);
      }
      return;
    }
    for (const s of shown) {
      const row = el("div", { className: "seg", tabIndex: 0 });
      row.setAttribute("role", "button");
      row.appendChild(el("span", { className: "t" }, `[${fmtClock(s.start_ms)}]`));
      if (s.speaker_name !== undefined && window.Speakers) row.appendChild(window.Speakers.chip(s));
      row.appendChild(el("span", { className: "x" }, s.text));
      row.dataset.start = s.start_ms; row.dataset.end = s.end_ms;
      row.onclick = () => seekTo(player, s.start_ms);
      row.onkeydown = (ev) => { if (isActivateKey(ev)) { ev.preventDefault(); seekTo(player, s.start_ms); } };
      box.appendChild(row);
      segEls.push(row);
    }
  }
  async function loadRuns_mark(r) {
    const i = runs.findIndex((x) => x.id === r.id);
    if (i >= 0 && $("runs").children[i]) $("runs").children[i].classList.add("sel");
  }
  document.addEventListener("analyses-changed", () => { loadRuns().catch(() => {}); });
  window.AnalyseJobs = { watch, refreshTranscript: () => { if (selected) select(selected); } };
  player.addEventListener("timeupdate", () => {
    const ms = player.currentTime * 1000;
    for (const row of segEls) row.classList.toggle("on", ms >= +row.dataset.start && ms < +row.dataset.end);
  });

  // ---- boot ---------------------------------------------------------------------------------------------
  async function boot() {
    if (!rec) { $("noRec").hidden = false; return; }
    $("page").hidden = false;
    try {
      const r = await api(`/api/recordings/${encodeURIComponent(rec)}`);
      $("recTitle").textContent = r.title;
      $("recMeta").textContent = `${new Date(r.started_at).toLocaleString()} · ${fmtClock(r.duration_ms)}`;
      player.src = `/api/recordings/${encodeURIComponent(rec)}/audio`;
    } catch (e) { $("recTitle").textContent = "Recording not found"; $("newRun").hidden = true; say(e.message, true); return; }
    try { await loadAlso(); } catch (e) { /* the run can still start without extra languages */ }
    try { await loadModels(); } catch (e) { reason.textContent = "Could not load models: " + e.message; startBtn.disabled = true; }
    try { await loadRuns(); } catch (e) { say("Could not load runs: " + e.message, true); }
    const done = pickRun(runs);
    if (!done) $("transcriptCard").before($("newRun"), $("aiSlot"));     // nothing to review yet: starting a run comes first
    if (done && !watching) select(done);
  }
  boot();
})();
