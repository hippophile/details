// AI write-up on the Analyse page: model card, output chips, result tabs, tickable checklist, Markdown copy/download.
// Text is only ever set via textContent. Pure helpers are exported for `node --test` (tests/js/outputs.test.mjs).
(function () {
  const A = typeof module !== "undefined" && module.exports ? require("./api.js") : window.AnalyseApi;
  const { api, el, fmtWall } = A;

  // ---- pure helpers -------------------------------------------------------------------------------------
  function canStart(s) { return !!(s.model && s.kinds.length && s.run && !s.busy); }
  function modelLabel(m) {
    const base = `${m.label} · ${m.role} · ~${m.needs_gb} GB`;
    return m.installed ? base : `${base} (not available: ${m.unavailable_reason || "unknown reason"})`;
  }
  function itemMeta(it) {
    const parts = [];
    if (it.owner) parts.push("owner: " + it.owner);
    if (it.due) parts.push("due: " + it.due);
    if (it.severity) parts.push("severity: " + it.severity);
    if (it.verified === false) parts.push("unverified");
    return parts.join(" · ");
  }
  function groupLabel(g) { return `Analysis #${g.n} · ${g.model_label} · ${g.wall_ms ? fmtWall(g.wall_ms) : g.status}`; }
  function footer(g) {
    const tps = g.tok_per_s ? ` · ${g.tok_per_s} tok/s` : "";
    return `${groupLabel(g)} · ${g.tokens_in} in / ${g.tokens_out} out tokens${tps}`;
  }
  const exported = { canStart, modelLabel, itemMeta, footer, groupLabel };
  if (typeof module !== "undefined" && module.exports) { module.exports = exported; return; }

  // ---- page ---------------------------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const rec = new URLSearchParams(location.search).get("rec");
  const KINDS = ["debrief", "notes", "checklist", "tags"];
  const TITLES = { debrief: "Debrief", notes: "Notes", checklist: "Checklist", tags: "Tags" };
  let models = [], doneRuns = [], source = "", watching = null, shown = null, tab = null;

  function msg(text, bad) { $("aiMsg").textContent = text || ""; $("aiMsg").style.color = bad ? "var(--bad)" : ""; }
  function kindsOn() { return Array.from(document.querySelectorAll("#aiSlot input[data-kind]")).filter((i) => i.checked).map((i) => i.dataset.kind); }

  function syncStart() {
    const m = models.find((x) => x.id === $("aiModel").value);
    $("aiStart").disabled = !canStart({ model: m && m.installed ? m.id : "", kinds: kindsOn(), run: source, busy: !!watching });
    $("aiReason").textContent = !doneRuns.length ? "Run a speech model first: the AI works on a finished transcript." :
      !$("aiModel").value ? "Choose an AI model to start." : !kindsOn().length ? "Pick at least one output." : "";
  }

  async function loadModels() {
    const sel = $("aiModel");
    try { models = await api("/api/models?kind=llm"); } catch (e) { models = []; $("aiReason").textContent = "Could not load AI models: " + e.message; }
    sel.replaceChildren(el("option", { value: "" }, "Choose AI model"));
    for (const m of models) sel.appendChild(el("option", { value: m.id, disabled: !m.installed }, modelLabel(m)));
    sel.value = "";
    syncStart();
  }

  // ---- runs / sources -----------------------------------------------------------------------------------
  function setRuns(runs, selected) {
    doneRuns = runs.filter((r) => r.status === "done");
    const sel = $("aiSource");
    const keep = selected && selected.status === "done" ? selected.id : (doneRuns.some((r) => r.id === source) ? source : (doneRuns[0] || {}).id || "");
    sel.replaceChildren(...doneRuns.map((r) => el("option", { value: r.id }, r.label)));
    sel.value = keep;
    source = keep;
    syncStart();
    if (source) refreshGroups();
  }
  function pick(run) { if (run && run.status === "done" && run.id !== source) { source = run.id; $("aiSource").value = source; syncStart(); refreshGroups(); } }

  async function refreshGroups() {
    if (!source) return;
    let groups = [];
    try { groups = await api(`/api/stt-runs/${source}/analyses`); } catch (e) { return; }
    const live = groups.find((g) => g.status === "queued" || g.status === "running");
    if (live && live.job_id) watch(live.job_id, live.group_id);
    else if (!shown && groups.length) show(groups[0].group_id);
  }

  function decorate(li, run) {
    if (run.status !== "done") return;
    api(`/api/stt-runs/${run.id}/analyses`).then((groups) => {
      if (!groups.length) return;
      const ul = el("ul", { className: "analyses", "aria-label": "Analyses of this transcript" });
      for (const g of groups) {
        const row = el("li", { className: "analysis" + (shown === g.group_id ? " sel" : ""), tabIndex: 0 });
        row.setAttribute("role", "button");
        row.appendChild(el("span", { className: "label" }, g.label));
        row.appendChild(el("span", { className: "st " + g.status }, g.status));
        const open = (ev) => { ev.stopPropagation(); pick(run); show(g.group_id); };
        row.onclick = open;
        row.onkeydown = (ev) => { if (ev.target === row && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); open(ev); } };
        ul.appendChild(row);
      }
      li.appendChild(ul);
    }).catch(() => {});
  }

  // ---- start + watch ------------------------------------------------------------------------------------
  async function start() {
    const model = $("aiModel").value;
    $("aiStart").disabled = true;
    try {
      const r = await api(`/api/stt-runs/${source}/analyses`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_id: model, kinds: kindsOn(), output_language: $("aiLang").value }) });
      msg("");
      watch(r.job_id, r.group_id);
    } catch (e) { msg("Could not start: " + e.message, true); syncStart(); }
  }
  function watch(jobId, groupId) {
    if (watching && watching.id === jobId) return;
    stopWatch();
    const poll = async () => {
      let j;
      try { j = await api(`/api/jobs/${jobId}`); } catch (e) { return; }
      const pct = j.step_progress != null ? Math.round(j.step_progress * 100) + "%" : "";
      $("aiStatus").textContent = j.status === "queued" ? "Waiting in the queue" : (j.message || j.step || j.status) + (j.step === "analyse" ? " " + pct : "");
      if (["done", "failed", "cancelled"].includes(j.status)) {
        stopWatch();
        if (j.status !== "done") msg(`Analysis ${j.status}${j.error ? ": " + j.error : ""}`, true);
        await show(groupId);
        document.dispatchEvent(new CustomEvent("analyses-changed"));
      }
    };
    watching = { id: jobId, timer: setInterval(poll, 1500) };
    $("aiCancel").hidden = false;
    syncStart();
    poll();
  }
  function stopWatch() {
    if (watching) clearInterval(watching.timer);
    watching = null;
    $("aiCancel").hidden = true;
    $("aiStatus").textContent = "";
    syncStart();
  }

  // ---- result rendering ---------------------------------------------------------------------------------
  async function show(groupId) {
    let g;
    try { g = await api(`/api/analyses/${groupId}`); } catch (e) { msg("Could not load the analysis: " + e.message, true); return; }
    shown = g.group_id;
    $("aiResults").hidden = false;
    $("aiFooter").textContent = footer(g);
    const kinds = KINDS.filter((k) => g.kinds.some((x) => x.kind === k));
    if (!tab || !kinds.includes(tab)) tab = kinds[0];
    const tabs = $("aiTabs");
    tabs.replaceChildren();
    for (const k of kinds) {
      const t = el("button", { type: "button", className: "tab" + (k === tab ? " active" : "") }, TITLES[k]);
      t.setAttribute("role", "tab");
      t.setAttribute("aria-selected", k === tab ? "true" : "false");
      t.onclick = () => { tab = k; show(groupId); };
      tabs.appendChild(t);
    }
    const kd = g.kinds.find((x) => x.kind === tab);
    renderKind($("aiPanel"), kd);
    const out = kd && kd.output;
    $("aiCopy").disabled = $("aiDownload").disabled = !out;
    $("aiCopy").onclick = () => copyMd(`/api/outputs/${out.id}/markdown`);
    $("aiDownload").onclick = () => download(`/api/outputs/${out.id}/markdown?download=1`);
    const any = g.kinds.some((x) => x.output);
    $("aiCopyAll").disabled = $("aiDownloadAll").disabled = !any;
    $("aiCopyAll").onclick = () => copyMd(`/api/analyses/${g.group_id}/markdown`);
    $("aiDownloadAll").onclick = () => download(`/api/analyses/${g.group_id}/markdown?download=1`);
    for (const row of document.querySelectorAll(".analysis")) row.classList.remove("sel");
  }

  function renderKind(panel, kd) {
    panel.replaceChildren();
    if (!kd) return;
    if (kd.status === "failed" || kd.status === "cancelled") { panel.appendChild(el("div", { className: "bad-note" }, `${kd.status}: ${kd.error || ""}`)); return; }
    if (kd.status !== "done") { panel.appendChild(el("div", { className: "meta" }, "Working on it...")); return; }
    if (kd.schema_valid === false || !kd.output) {
      panel.appendChild(el("div", { className: "bad-note" }, "model returned invalid JSON"));
      if (kd.validation_errors) panel.appendChild(el("div", { className: "meta" }, kd.validation_errors.join("; ")));
      panel.appendChild(el("pre", { className: "rawtext" }, kd.raw_text || ""));
      return;
    }
    const c = kd.output.content;
    if (kd.unverified) panel.appendChild(el("div", { className: "meta" }, `${kd.verified} quotes verified, ${kd.unverified} unverified (greyed out).`));
    if (kd.kind === "checklist") { panel.appendChild(el("h3", null, c.title)); panel.appendChild(checklist(kd.output.id, c.items)); }
    else if (kd.kind === "tags") panel.appendChild(tagPills(c.tags));
    else if (kd.kind === "notes") {
      panel.appendChild(el("h3", null, c.title));
      for (const s of c.sections) { panel.appendChild(el("h3", null, s.heading)); panel.appendChild(bullets(s.bullets)); }
    } else {
      panel.appendChild(el("p", { className: "summary" }, c.summary));
      const lists = [["Decisions", c.decisions], ["Action items", c.action_items], ["Risks", c.risks], ["Open questions", c.open_questions]];
      for (const [title, items] of lists) if (items.length) { panel.appendChild(el("h3", null, title)); panel.appendChild(bullets(items)); }
    }
  }

  function itemNode(it, tag) {
    const li = el(tag || "li", { className: "item" + (it.verified === false ? " unverified" : "") });
    li.appendChild(el("span", null, it.text));
    const meta = itemMeta(it);
    if (meta) li.appendChild(el("span", { className: "meta" }, "  " + meta));
    if (it.quote) li.appendChild(el("div", { className: "quote" }, "“" + it.quote + "”"));
    return li;
  }
  function bullets(items) { const ul = el("ul"); for (const it of items) ul.appendChild(itemNode(it)); return ul; }
  function tagPills(tags) {
    const ul = el("ul", { className: "tagpills" });
    for (const t of tags) ul.appendChild(el("li", { className: "tagpill" + (t.verified === false ? " unverified" : ""), title: t.quote || "" }, t.name));
    return ul;
  }
  function checklist(outputId, items) {
    const ul = el("ul", { className: "ckl" });
    items.forEach((it, i) => {
      const li = el("li", { className: "ck" + (it.verified === false ? " unverified" : "") });
      const box = el("input", { type: "checkbox", id: `ck-${outputId}-${i}`, checked: !!it.done });
      const lab = el("label", null, it.text);
      lab.htmlFor = box.id;
      box.onchange = async () => {
        const want = box.checked;
        try {
          await api(`/api/outputs/${outputId}/items/${i}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ done: want }) });
          msg("");
        } catch (e) { box.checked = !want; msg("Could not save the tick: " + e.message, true); }
      };
      li.append(box, lab);
      const meta = itemMeta(it);
      if (meta) li.appendChild(el("span", { className: "meta" }, meta));
      if (it.quote) li.appendChild(el("div", { className: "quote" }, "“" + it.quote + "”"));
      ul.appendChild(li);
    });
    return ul;
  }

  // ---- Markdown copy / download -------------------------------------------------------------------------
  async function copyMd(url) {
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      await navigator.clipboard.writeText(await r.text());
      msg("Copied Markdown to the clipboard.");
    } catch (e) { msg("Copy failed: " + e.message, true); }
  }
  function download(url) {
    const a = el("a", { href: url });
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  // ---- boot ---------------------------------------------------------------------------------------------
  window.Outputs = { setRuns, pick, decorate };
  if (!rec) return;
  $("aiStart").onclick = start;
  $("aiModel").onchange = syncStart;
  $("aiSource").onchange = () => { source = $("aiSource").value; shown = null; tab = null; $("aiResults").hidden = true; syncStart(); refreshGroups(); };
  for (const i of document.querySelectorAll("#aiSlot input[data-kind]")) i.onchange = syncStart;
  $("aiCancel").onclick = async () => {
    if (!watching) return;
    try { await api(`/api/jobs/${watching.id}/cancel`, { method: "POST" }); msg("Cancelling..."); } catch (e) { msg("Cancel failed: " + e.message, true); }
  };
  loadModels();
})();
