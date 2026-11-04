// Run insights on the Analyse page: pipeline step table (SSE `steps`), job log panel (polls /log), prompt inspector dialog.
// Text is only ever set via textContent. Pure helpers are exported for `node --test` (tests/js/insights.test.mjs).
(function () {
  const A = typeof module !== "undefined" && module.exports ? require("./api.js") : window.AnalyseApi;
  const { api, el, fmtWall } = A;

  // ---- pure helpers -------------------------------------------------------------------------------------
  const dash = (v) => v == null || v === "";
  function fmtDuration(ms) { return dash(ms) ? "-" : fmtWall(ms); }
  function fmtTps(v) { return dash(v) ? "-" : Number(v).toFixed(1); }
  function fmtGb(v) { return dash(v) ? "-" : Number(v).toFixed(1) + " GB"; }
  function fmtTokens(v) { return dash(v) ? "-" : String(v); }
  // name, status, duration (ticks while running), model, tokens in/out, tok/s, free RAM (start while running, end after)
  function rowCells(s, nowMs) {
    const running = s.status === "running";
    const dur = running && s.started_at ? nowMs - Date.parse(s.started_at) : s.duration_ms;
    return [s.name, s.status, fmtDuration(dur), dash(s.model) ? "-" : s.model, fmtTokens(s.tokens_in), fmtTokens(s.tokens_out),
      fmtTps(s.tok_per_s), fmtGb(running || dash(s.ram_free_end_gb) ? s.ram_free_start_gb : s.ram_free_end_gb)];
  }
  const LEVELS = ["debug", "info", "warn", "error"];
  function passesLevel(level, min) { return min === "all" || LEVELS.indexOf(level) >= LEVELS.indexOf(min); }
  function logText(l) {
    const d = new Date(l.ts), p = (n) => String(n).padStart(2, "0");
    const t = isNaN(d) ? "" : `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())} `;
    const extra = Object.entries(l.data || {}).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" ");
    return `${t}${l.level.toUpperCase().padEnd(5)} [${l.step || "job"}] ${l.src}: ${l.msg}${extra ? " " + extra : ""}`;
  }
  function isNearBottom(e) { return e.scrollHeight - e.scrollTop - e.clientHeight < 24; }
  function mergeLines(have, more) {
    const seen = new Set(have.map((x) => x.n));
    return have.concat(more.filter((x) => !seen.has(x.n))).sort((a, b) => a.n - b.n);
  }
  const exported = { fmtDuration, fmtTps, fmtGb, fmtTokens, rowCells, passesLevel, logText, isNearBottom, mergeLines };
  if (typeof module !== "undefined" && module.exports) { module.exports = exported; return; }

  // ---- page ---------------------------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const ICON = { done: "✓", failed: "✕", cancelled: "■", running: "…", waiting_gpu: "…", pending: "·", interrupted: "!" };
  const LOG_CAP = 5000;
  let cur = null;      // { jobId, es, poll, tick, steps, lines, after, live, stick }

  function renderSteps() {
    if (!cur) return;
    const rows = $("stepRows");
    rows.replaceChildren();
    for (const s of cur.steps) {
      const tr = el("tr", { className: "step " + s.status });
      const cells = rowCells(s, Date.now());
      cells.forEach((text, i) => {
        const td = el("td", { className: i >= 4 ? "ncol" : "" });
        if (i === 1) { td.appendChild(el("span", { className: "st " + s.status }, `${ICON[s.status] || ""} ${text}`)); if (s.error) td.title = s.error; }
        else td.textContent = text;
        tr.appendChild(td);
      });
      rows.appendChild(tr);
    }
    $("stepTable").hidden = cur.steps.length === 0;
  }

  // ---- log ----------------------------------------------------------------------------------------------
  function lineNode(l) { return el("div", { className: "logline " + l.level }, logText(l)); }
  function renderLog() {
    const body = $("logBody"), min = $("logLevel").value;
    body.replaceChildren(...cur.lines.filter((l) => passesLevel(l.level, min)).map(lineNode));
    if (cur.stick) body.scrollTop = body.scrollHeight;
  }
  async function pollLog() {
    const c = cur;
    if (!c || !$("logPanel").open) return;
    try {
      const r = await api(`/api/jobs/${c.jobId}/log?after=${c.after}&limit=1000`);
      if (cur !== c) return;
      if (r.lines.length) {
        c.after = r.next;
        c.lines = mergeLines(c.lines, r.lines).slice(-LOG_CAP);
        const body = $("logBody"), min = $("logLevel").value;
        c.stick = isNearBottom(body);          // paused when the user scrolled up
        for (const l of r.lines) if (passesLevel(l.level, min)) body.appendChild(lineNode(l));
        if (c.stick) body.scrollTop = body.scrollHeight;
      }
      if (r.done && !c.live) stopPolling(c);
    } catch (e) { /* retry on the next tick */ }
  }
  function stopPolling(c) { clearInterval(c.poll); c.poll = null; }

  // ---- steps (SSE, with a polling fallback) -------------------------------------------------------------
  function setSteps(c, steps, status) {
    if (cur !== c) return;
    c.steps = steps || c.steps;
    renderSteps();
    if (status && ["done", "failed", "cancelled"].includes(status)) end(c);
  }
  function end(c) {          // the job is over: stop the SSE and the tick, read the log once more, then stop polling
    c.live = false;
    if (c.es) c.es.close();
    clearInterval(c.tick);
    clearInterval(c.sp);
    setTimeout(() => { if (cur === c) { pollLog().then(() => { if (cur === c) stopPolling(c); }); } }, 700);
  }
  async function pollSteps(c) {
    try { const r = await api(`/api/jobs/${c.jobId}/steps`); const j = await api(`/api/jobs/${c.jobId}`); setSteps(c, r.steps, j.status); } catch (e) { /* next tick */ }
  }

  function attach(host, jobId, opts) {
    detach();
    const live = !(opts && opts.finished);
    const box = $("insights");
    host.appendChild(box);
    box.hidden = false;
    const c = cur = { jobId, es: null, poll: null, tick: null, sp: null, steps: [], lines: [], after: 0, live, stick: true };
    $("logBody").replaceChildren();
    $("logDownload").href = `/api/jobs/${jobId}/log?download=1`;
    $("logDownload").download = `job-${String(jobId).slice(0, 8)}.log`;
    renderSteps();
    c.poll = setInterval(pollLog, 1000);
    if (!live) { pollSteps(c).then(() => { if (cur === c) end(c); }); pollLog(); return; }
    c.tick = setInterval(renderSteps, 1000);
    if (window.EventSource) {
      const es = new EventSource(`/api/jobs/${jobId}/events`);
      c.es = es;
      es.onmessage = (ev) => { try { const d = JSON.parse(ev.data); setSteps(c, d.steps, d.status); } catch (e) { /* bad frame */ } };
      es.onerror = () => { es.close(); if (cur === c && !c.sp && c.live) c.sp = setInterval(() => pollSteps(c), 2000); };
    } else c.sp = setInterval(() => pollSteps(c), 2000);
    pollLog();
  }
  function detach(host) {
    if (!cur || (host && !host.contains($("insights")))) return;
    const c = cur;
    cur = null;
    c.live = false;
    if (c.es) c.es.close();
    for (const t of [c.poll, c.tick, c.sp]) clearInterval(t);
    $("insights").hidden = true;
  }

  // ---- prompt inspector ---------------------------------------------------------------------------------
  const TABS = [["prompt", "Prompt"], ["raw", "Raw reply"], ["params", "Params"]];
  let insp = null;     // { data, attempt, tab }
  function inspectText() {
    const a = insp.data.attempts[insp.attempt], d = insp.data;
    if (insp.tab === "prompt") return a.rendered_prompt;
    if (insp.tab === "raw") return a.raw_text;
    return JSON.stringify({ kind: d.kind, model_id: d.model_id, endpoint: d.endpoint, template: d.template,
      output_language: d.output_language, params: d.params, attempt: { attempt: a.attempt, status: a.status, tokens_in: a.tokens_in,
        tokens_out: a.tokens_out, tok_per_s: a.tok_per_s, wall_ms: a.wall_ms, finish_reason: a.finish_reason, schema_valid: a.schema_valid,
        validation_errors: a.validation_errors, error: a.error } }, null, 2);
  }
  function renderInspect() {
    const d = insp.data, a = d.attempts[insp.attempt];
    const sel = $("inspectAttempts");
    sel.hidden = d.attempts.length < 2;
    $("inspectTabs").replaceChildren(...TABS.map(([k, label]) => {
      const t = el("button", { type: "button", className: "tab" + (k === insp.tab ? " active" : "") }, label);
      t.setAttribute("role", "tab");
      t.setAttribute("aria-selected", k === insp.tab ? "true" : "false");
      t.onclick = () => { insp.tab = k; renderInspect(); };
      return t;
    }));
    const notes = [];
    if (d.prompt_scope === "first_chunk" && insp.tab === "prompt") notes.push("Long transcript: this is the prompt of the first chunk only.");
    if (d.raw_text_kind === "merged_by_code" && insp.tab === "raw") notes.push("Not a model reply: this JSON was merged by code from the chunk replies.");
    if (a.error) notes.push("Error: " + a.error);
    $("inspectNote").textContent = notes.join(" ");
    const text = inspectText();
    $("inspectBody").textContent = text == null ? (insp.tab === "prompt" ? "(no prompt stored: the call failed before a reply)" : "(no reply stored)") : text;
    $("inspectCopy").disabled = text == null;
    $("inspectMeta").textContent = `${d.kind} · ${d.model_id} · ${fmtTokens(a.tokens_in)} in / ${fmtTokens(a.tokens_out)} out · ${fmtTps(a.tok_per_s)} tok/s`;
  }
  async function inspect(runId) {
    const dlg = $("inspectDlg");
    $("inspectMsg").textContent = "";
    let data;
    try { data = await api(`/api/analysis-runs/${runId}/inspect`); } catch (e) { $("inspectNote").textContent = "Could not load: " + e.message; dlg.showModal(); return; }
    insp = { data, attempt: data.attempts.length - 1, tab: "prompt" };
    const sel = $("inspectAttempts");
    sel.replaceChildren(...data.attempts.map((a, i) => el("option", { value: String(i) }, `Attempt ${a.attempt} (${a.status}${a.schema_valid === false ? ", invalid JSON" : ""})`)));
    sel.value = String(insp.attempt);
    sel.onchange = () => { insp.attempt = +sel.value; renderInspect(); };
    renderInspect();
    if (!dlg.open) dlg.showModal();
  }
  $("inspectCopy").onclick = async () => {
    const text = insp && inspectText();
    if (text == null) return;
    try { await navigator.clipboard.writeText(text); $("inspectMsg").textContent = "Copied."; }
    catch (e) { $("inspectMsg").textContent = "Copy failed: " + e.message; }
  };
  $("inspectClose").onclick = () => $("inspectDlg").close();

  // ---- boot ---------------------------------------------------------------------------------------------
  $("logPanel").addEventListener("toggle", () => { if ($("logPanel").open && cur) { if (!cur.poll && cur.live) cur.poll = setInterval(pollLog, 1000); pollLog(); } });
  $("logLevel").onchange = () => { if (cur) renderLog(); };
  $("logBody").addEventListener("scroll", () => { if (cur) cur.stick = isNearBottom($("logBody")); });
  window.Insights = { attach, detach, inspect };
})();
