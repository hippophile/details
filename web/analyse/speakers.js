// "Who talked" card on the Analyse page: start a speaker run, talk-time bars, a clickable timeline, turns, rename.
// The pure helpers at the top are also loaded by `node --test` (tests/js/speakers.test.mjs). Text is set via textContent only.
(function () {
  const A = typeof module !== "undefined" && module.exports ? require("./api.js") : window.AnalyseApi;
  const { fmtClock, seekTo, isActivateKey, api, el } = A;

  // ---- pure helpers -------------------------------------------------------------------------------------
  function fmtPct(p) { return p == null || Number.isNaN(+p) ? "-" : Math.round(+p) + "%"; }
  function fmtTalk(ms) { return fmtClock(ms || 0); }
  function colorClass(speaker) { return speaker == null ? "spk-c0" : "spk-c" + (((speaker - 1) % 6) + 1); }
  function barRows(speakers) {
    return (speakers || []).map((s) => ({
      speaker: s.speaker, name: s.display, pct: fmtPct(s.share_pct), time: fmtTalk(s.talk_ms),
      width: Math.max(0, Math.min(100, +s.share_pct || 0)), cls: colorClass(s.speaker),
    }));
  }
  // Consecutive segments of one speaker become one line when the gap is below gapMs (null = any gap).
  function groupTurns(segs, gapMs) {
    const out = [];
    for (const s of segs || []) {
      const last = out[out.length - 1];
      if (last && last.speaker === s.speaker && (gapMs == null || s.start_ms - last.end_ms < gapMs)) {
        last.text = (last.text + " " + s.text).trim(); last.end_ms = s.end_ms;
      } else out.push({ start_ms: s.start_ms, end_ms: s.end_ms, speaker: s.speaker, name: s.speaker_name || "Unknown", text: s.text });
    }
    return out;
  }
  function turnText(g) { return `[${fmtClock(g.start_ms)}] ${g.name}${g.text ? " " + g.text : ""}`; }
  function chipText(seg) { return (seg && seg.speaker_name) || "Unknown"; }
  function timelineBlocks(turns, audioMs) {
    if (!audioMs) return [];
    const r = (x) => Math.round(x * 1000) / 1000;
    return (turns || []).map((t) => ({ left: r(t.start_ms * 100 / audioMs), width: r((t.end_ms - t.start_ms) * 100 / audioMs),
      speaker: t.speaker, start_ms: t.start_ms, cls: colorClass(t.speaker) }));
  }
  function unavailableText(m) {
    if (!m) return "No speaker model is configured.";
    if (m.installed) return "";
    return `${m.label} is not available: ${m.unavailable_reason || "unknown reason"}${m.hint ? " (" + m.hint + ")" : ""}`;
  }
  function nameToSend(text) { const t = (text || "").trim(); return t ? t : null; }
  function emptyText(st) {
    if (st.running) return "Speakers are being detected.";
    if (!st.available) return "The speaker model is unavailable.";
    if (!st.run) return "No speaker run yet. Press Detect speakers.";
    if (st.run.status === "done" && !(st.run.speakers || []).length) return "No speech was found in this recording.";
    return "";
  }

  const out = { fmtPct, fmtTalk, colorClass, barRows, groupTurns, turnText, chipText, timelineBlocks, unavailableText, nameToSend, emptyText };
  if (typeof module !== "undefined" && module.exports) { module.exports = out; return; }

  // ---- the card -----------------------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const rec = new URLSearchParams(location.search).get("rec");
  let entry = null, runs = [], picked = null, turns = [], sttRun = null, busy = false;
  window.Speakers = {
    pickedId: () => (picked && picked.status === "done" ? picked.id : null),
    labelRunId: () => ($("aiLabels") && $("aiLabels").value) || undefined,
    chip: (seg) => el("span", { className: "spk-chip " + colorClass(seg.speaker) }, chipText(seg)),
    transcriptChanged: (run) => { sttRun = run && run.status === "done" ? run : null; renderTurns(); },
  };
  if (!rec) return;

  const say = (msg, bad) => { $("spkStatus").textContent = msg || ""; $("spkStatus").style.color = bad ? "var(--bad)" : ""; };
  const available = () => !!(entry && entry.installed);
  const player = () => $("player");

  function syncStart() {
    $("spkStart").disabled = !available() || busy;
    $("spkReason").textContent = available() ? "" : unavailableText(entry);
  }
  function syncLabelSelect() {
    const sel = $("aiLabels"), keep = sel.value;
    sel.replaceChildren(el("option", { value: "" }, "No labels (plain transcript)"));
    for (const r of runs.filter((x) => x.status === "done" && (x.speakers || []).length)) sel.appendChild(el("option", { value: r.id }, r.label));
    sel.value = Array.from(sel.options).some((o) => o.value === keep) ? keep : "";
  }

  function renderBars() {
    const box = $("spkBars"), done = picked && picked.status === "done";
    box.replaceChildren();
    for (const r of done ? barRows(picked.speakers) : []) {
      const row = el("div", { className: "spk-row " + r.cls });
      row.appendChild(el("span", { className: "spk-name" }, r.name));
      const bar = el("span", { className: "spk-bar" });
      bar.appendChild(el("span", { className: "spk-fill" }));
      bar.firstChild.style.width = r.width + "%";
      row.appendChild(bar);
      row.appendChild(el("span", { className: "spk-pct num" }, r.pct));
      row.appendChild(el("span", { className: "spk-time num" }, r.time));
      box.appendChild(row);
    }
    const msg = emptyText({ available: available(), run: picked, running: busy });
    $("spkEmpty").textContent = msg;
    $("spkEmpty").hidden = !msg;
    const guess = !!(done && (picked.speakers || []).length);
    $("spkWarn1").hidden = $("spkWarn2").hidden = !guess;
  }
  function renderTimeline() {
    const box = $("spkTimeline");
    box.replaceChildren();
    for (const b of timelineBlocks(turns, picked && picked.audio_ms)) {
      const name = ((picked.speakers || []).find((s) => s.speaker === b.speaker) || {}).display || "Speaker";
      const blk = el("button", { type: "button", className: "spk-block " + b.cls, title: `${name} ${fmtClock(b.start_ms)}` });
      blk.setAttribute("aria-label", `${name} at ${fmtClock(b.start_ms)}`);
      blk.style.left = b.left + "%"; blk.style.width = b.width + "%";
      blk.onclick = () => seekTo(player(), b.start_ms);
      box.appendChild(blk);
    }
    box.hidden = !box.children.length;
  }
  let turnsToken = 0;      // several renders can overlap (pick, rename, transcript change): only the newest draws
  async function renderTurns() {
    const box = $("spkTurns"), token = ++turnsToken;
    if (!picked || picked.status !== "done" || !(picked.speakers || []).length) { box.replaceChildren(); return; }
    const gap = picked.settings && picked.settings.merge_gap_ms != null ? picked.settings.merge_gap_ms : null;
    let groups;
    if (sttRun) {
      try { groups = groupTurns(await api(`/api/stt-runs/${sttRun.id}/segments?diarization_run_id=${picked.id}`), gap); }
      catch (e) { groups = null; }
      if (token !== turnsToken) return;
    }
    box.replaceChildren();
    if (!groups) {         // no readable transcript: the speaker turns alone
      const names = Object.fromEntries((picked.speakers || []).map((s) => [s.speaker, s.display]));
      groups = groupTurns(turns.map((t) => ({ ...t, speaker_name: names[t.speaker], text: "" })), gap);
    }
    for (const g of groups) {
      const row = el("div", { className: "spk-turn " + colorClass(g.speaker), tabIndex: 0 });
      row.setAttribute("role", "button");
      row.setAttribute("aria-label", turnText(g));
      row.appendChild(el("span", { className: "t" }, `[${fmtClock(g.start_ms)}]`));
      row.appendChild(el("span", { className: "spk-chip " + colorClass(g.speaker) }, g.name));
      if (g.text) row.appendChild(el("span", { className: "x" }, g.text));
      row.onclick = () => seekTo(player(), g.start_ms);
      row.onkeydown = (ev) => { if (isActivateKey(ev)) { ev.preventDefault(); seekTo(player(), g.start_ms); } };
      box.appendChild(row);
    }
  }
  function renderNames() {
    const box = $("spkNames");
    box.replaceChildren();
    if (!picked || picked.status !== "done") return;
    for (const s of picked.speakers || []) {
      const row = el("div", { className: "spk-rename " + colorClass(s.speaker) });
      const id = "spkName" + s.speaker;
      row.appendChild(el("label", { htmlFor: id }, `Speaker ${s.speaker}`));
      const input = el("input", { id, type: "text", value: s.name || "", maxLength: 60, placeholder: `Speaker ${s.speaker}` });
      const save = el("button", { type: "button", className: "btn sm ghost" }, "Save name");
      const go = async () => {
        save.disabled = true;
        try {
          await api(`/api/diarization-runs/${picked.id}/speakers/${s.speaker}`, { method: "PATCH",
            headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: nameToSend(input.value) }) });
          say("Name saved.");
          await reloadPicked();
          if (window.AnalyseJobs) window.AnalyseJobs.refreshTranscript();
        } catch (e) { say("Could not save the name: " + e.message, true); }
        save.disabled = false;
      };
      save.onclick = go;
      input.onkeydown = (ev) => { if (ev.key === "Enter") { ev.preventDefault(); go(); } };
      row.appendChild(input);
      row.appendChild(save);
      box.appendChild(row);
    }
  }
  function renderAll() { renderBars(); renderTimeline(); renderNames(); renderTurns(); syncStart(); }

  async function reloadPicked() {
    if (!picked) return;
    picked = await api(`/api/diarization-runs/${picked.id}`);
    const i = runs.findIndex((r) => r.id === picked.id);
    if (i >= 0) runs[i] = picked;
    syncLabelSelect();
    renderBars(); renderNames(); renderTurns();
  }
  async function pick(run) {
    picked = run || null;
    turns = [];
    if (picked && picked.status === "done") {
      try { turns = await api(`/api/diarization-runs/${picked.id}/turns`); } catch (e) { turns = []; }
    }
    $("spkRun").value = picked ? picked.id : "";
    renderAll();
    if (window.AnalyseJobs) window.AnalyseJobs.refreshTranscript();
  }

  function onFinish(j) {
    busy = false;
    loadRuns(true).then(() => say(j.status === "done" ? "Speakers detected." : `Speaker run ${j.status}${j.error ? ": " + j.error : ""}`,
      j.status !== "done"));
  }
  async function loadRuns(pickNewest) {
    runs = await api(`/api/recordings/${rec}/diarization-runs`);
    const sel = $("spkRun");
    sel.replaceChildren(...runs.map((r) => el("option", { value: r.id }, r.label)));
    syncLabelSelect();
    const keep = !pickNewest && picked && runs.find((r) => r.id === picked.id);
    const next = keep || runs.find((r) => r.status === "done");
    if (next) await pick(next); else { picked = null; renderAll(); }
    const live = runs.find((r) => (r.status === "queued" || r.status === "running") && r.job_id);
    busy = !!live;
    syncStart();
    if (live && window.AnalyseJobs) { say("Detecting speakers: progress is in the Running card above."); window.AnalyseJobs.watch(live.job_id, onFinish); }
  }

  $("spkStart").onclick = async () => {
    const hint = (id) => { const v = $(id).value.trim(); return v === "" ? undefined : Number(v); };
    busy = true; syncStart();
    try {
      await api(`/api/recordings/${rec}/diarization-runs`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ min_speakers: hint("spkMin"), max_speakers: hint("spkMax") }) });
      say("");
      await loadRuns(true);
    } catch (e) { busy = false; say("Could not start: " + e.message, true); syncStart(); }
  };
  $("spkRun").onchange = () => { const r = runs.find((x) => x.id === $("spkRun").value); if (r) pick(r); };

  async function boot() {
    try { entry = (await api("/api/models?kind=diar"))[0] || null; } catch (e) { entry = null; say("Could not load the speaker model: " + e.message, true); }
    syncStart();
    renderBars();
    try { await loadRuns(); } catch (e) { say("Could not load speaker runs: " + e.message, true); }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
