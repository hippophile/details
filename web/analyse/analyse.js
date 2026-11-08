// Analyse page: recording header + player, New run, progress (SSE with polling fallback), runs list, transcript.
(function () {
  const { fmtClock, fmtWall, seekTo, isActivateKey, api, el, pickRun, freshRun } = window.AnalyseApi;
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
  startBtn.onclick = async () => {
    startBtn.disabled = true;
    try {
      const r = await api(`/api/recordings/${rec}/stt-runs`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_id: modelSel.value, language: $("lang").value, vocab_hint: $("hint").checked }) });
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
    stopWatching();
    await loadRuns();
    say(j.status === "done" ? "Run finished." : `Run ${j.status}${j.error ? ": " + j.error : ""}`, j.status !== "done");
    const r = runs.find((x) => x.job_id === j.id);
    if (r && j.status === "done") select(r);
  }
  function watch(jobId) {
    if (watching && watching.id === jobId) return;
    stopWatching();
    watching = { id: jobId, es: null, poll: null };
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
    selected = null; segEls = [];
    $("transcript").replaceChildren(el("div", { className: "meta" }, "Select a run to read its transcript."));
    $("transcriptTitle").textContent = "Transcript";
  }
  async function select(r) {
    selected = r;
    if (window.Outputs) window.Outputs.pick(r);
    for (const li of $("runs").children) li.classList.remove("sel");
    await loadRuns_mark(r);
    const box = $("transcript");
    box.replaceChildren();
    $("transcriptTitle").textContent = "Transcript: " + r.label;
    if (r.status !== "done") { box.appendChild(el("div", { className: "meta" }, r.error || `This run is ${r.status}.`)); return; }
    const segs = await api(`/api/stt-runs/${r.id}/segments`);
    segEls = [];
    if (!segs.length) {
      const full = await api(`/api/stt-runs/${r.id}`);
      box.appendChild(el("div", { className: "meta" }, "No timestamps for this run (imported from v1)."));
      box.appendChild(el("div", { className: "plain" }, full.text || ""));
      return;
    }
    for (const s of segs) {
      const row = el("div", { className: "seg", tabIndex: 0 });
      row.setAttribute("role", "button");
      row.appendChild(el("span", { className: "t" }, `[${fmtClock(s.start_ms)}]`));
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
    try { await loadModels(); } catch (e) { reason.textContent = "Could not load models: " + e.message; startBtn.disabled = true; }
    try { await loadRuns(); } catch (e) { say("Could not load runs: " + e.message, true); }
    const done = pickRun(runs);
    if (done && !watching) select(done);
  }
  boot();
})();
