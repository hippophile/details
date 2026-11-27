// AI write-up on the Analyse page: model card, output chips, result tabs, tickable checklist, Markdown copy/download.
// Text is only ever set via textContent. Pure helpers are exported for `node --test` (tests/js/outputs.test.mjs).
(function () {
  const A = typeof module !== "undefined" && module.exports ? require("./api.js") : window.AnalyseApi;
  const { api, el, fmtWall, fmtClock, seekTo, isActivateKey } = A;

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
    if (it.verified === false) parts.push("quote not found");
    return parts.join(" · ");
  }
  // A quote with at_ms plays the recording from there; without it, plain text.
  function quoteLabel(it) { return (typeof it.at_ms === "number" ? `▶ ${fmtClock(it.at_ms)} ` : "") + "“" + it.quote + "”"; }
  function countsLine(kd) {
    const v = kd.verified || 0, u = kd.unverified || 0;
    if (!v && !u) return "";
    return `${v} ${v === 1 ? "quote" : "quotes"} found in the transcript, ${u} not found${u ? " (greyed out)" : ""}. ` +
      "Found means the words were said, not that the point is right.";
  }
  // job to retry for a kind: only a failed one (the job runner refuses the rest)
  function retryJob(kd, g) { return kd && kd.status === "failed" && g && g.job_id ? g.job_id : null; }
  function groupLabel(g) { return `Analysis #${g.n} · ${g.model_label} · ${g.wall_ms ? fmtWall(g.wall_ms) : g.status}`; }
  function footer(g) {
    const tps = g.tok_per_s ? ` · ${g.tok_per_s} tok/s` : "";
    return `${groupLabel(g)} · ${g.tokens_in} in / ${g.tokens_out} out tokens${tps}`;
  }
  // Prompt versions (prompt-ab). `versions` = {kind: [1, 2, ...]} from /api/prompts.
  function pickerKinds(versions, ticked) { return ticked.filter((k) => (versions[k] || []).length > 1); }
  function promptVersionsBody(versions, picks, ticked) {
    const out = {};
    for (const k of pickerKinds(versions, ticked)) if (picks[k] != null && picks[k] !== "") out[k] = Number(picks[k]);
    return out;
  }
  function promptsText(prompts, versions) {
    return Object.keys(prompts || {}).filter((k) => (versions[k] || []).length > 1).map((k) => `${k} v${prompts[k]}`).join(", ");
  }
  // ---- Brief: the view as plain data (the page turns it into DOM with textContent) -----------------------
  const KINDS = ["brief", "debrief", "notes", "checklist", "tags"];
  function tabKinds(have) { return KINDS.filter((k) => have.includes(k)); }
  // item = { text, meta, quote (an object with quote/at_ms/verified, or null), jump (ms or null), unverified, sub: [] }
  function briefView(c) {
    const decisions = c.decisions || [], byId = Object.fromEntries(decisions.filter((d) => d.id).map((d) => [d.id, d]));
    const mk = (text, o) => Object.assign({ text, meta: "", quote: null, jump: null, after: false, unverified: false, sub: [] }, o);
    const changed = (d) => mk("changed from: " + d.text, { jump: typeof d.at_ms === "number" ? d.at_ms : null, after: true,
      sub: (d.replaces || []).filter((r) => byId[r]).map((r) => changed(byId[r])) });
    const decided = decisions.filter((d) => d.status !== "replaced").map((d) => mk("✓ " + d.text, { quote: d, unverified: d.verified === false,
      sub: (d.replaces || []).filter((r) => byId[r]).map((r) => changed(byId[r])) }));
    const disagreed = (c.disagreements || []).map((x) => {
      const by = byId[x.settled_by];
      return mk(`⚡ ${x.topic} — ${by ? "settled by: " + by.text : "still open"}`,
        { sub: x.positions.map((p) => mk(`${p.who || "Unknown speaker"}: ${p.stance}`, { quote: p, unverified: p.verified === false })) });
    });
    const todo = (c.action_items || []).map((a) => mk("☐ " + a.text, { meta: itemMeta(a), quote: a, unverified: a.verified === false }));
    const questions = (c.open_questions || []).map((q) => mk("? " + q.text, { quote: q, unverified: q.verified === false }));
    const chapters = (c.chapters || []).map((h) => typeof h.at_ms === "number" ? mk(`${fmtClock(h.at_ms)} ${h.title}`, { jump: h.at_ms }) : mk(h.title));
    const sections = [{ key: "decided", heading: "Decided", empty: "Nothing was decided.", items: decided },
      { key: "disagreed", heading: "Disagreed", empty: "No disagreements.", items: disagreed },
      { key: "todo", heading: "To do", empty: "Nothing to do.", items: todo },
      { key: "questions", heading: "Open questions", empty: "No open questions.", items: questions }];
    if (chapters.length) sections.push({ key: "chapters", heading: "Chapters", empty: null, items: chapters });
    const notes = [];
    const dn = c.dropped_not_meeting, nd = dn ? dn.decisions + dn.disagreements + dn.action_items : 0;
    if (nd) notes.push(`Not a meeting: ${nd} ${nd === 1 ? "item the model proposed was" : "items the model proposed were"} left out.`);
    if (c.links === "failed") notes.push("Could not work out which decisions replaced earlier ones.");
    if ((c.script_warnings || []).length) notes.push("Some text is in an unexpected alphabet.");
    return { title: "BRIEF · " + c.kind_of_recording, notes, summary: c.summary, sections };
  }
  const exported = { canStart, modelLabel, itemMeta, footer, groupLabel, pickerKinds, promptVersionsBody, promptsText, quoteLabel, countsLine, retryJob, briefView, tabKinds };
  if (typeof module !== "undefined" && module.exports) { module.exports = exported; return; }

  // ---- page ---------------------------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const rec = new URLSearchParams(location.search).get("rec");
  const TITLES = { brief: "Brief", debrief: "Debrief", notes: "Notes", checklist: "Checklist", tags: "Tags" };
  let models = [], doneRuns = [], source = "", watching = null, shown = null, tab = null;
  let versions = {};            // {kind: [version numbers]}; a version select shows only for kinds with more than one

  async function loadVersions() {
    try {
      const j = await api("/api/prompts");
      versions = Object.fromEntries(j.kinds.map((k) => [k.kind, k.versions.map((v) => v.version)]));
    } catch (e) { versions = {}; }
    syncPickers();
  }
  function syncPickers() {
    const ticked = kindsOn(), want = pickerKinds(versions, ticked);
    for (const chip of document.querySelectorAll("#aiSlot .chip")) {
      const kind = chip.querySelector("input[data-kind]").dataset.kind;
      let sel = chip.parentElement.querySelector(`select[data-pv="${kind}"]`);
      if (!want.includes(kind)) { if (sel) sel.remove(); continue; }
      if (sel) continue;
      sel = el("select", { className: "pv-select" });
      sel.dataset.pv = kind;
      sel.setAttribute("aria-label", `Prompt version for ${TITLES[kind]}`);
      const latest = Math.max(...versions[kind]);
      for (const v of versions[kind]) sel.appendChild(el("option", { value: String(v) }, `v${v}` + (v === latest ? " (latest)" : "")));
      sel.value = String(latest);
      chip.after(sel);
    }
  }
  function picks() {
    const out = {};
    for (const s of document.querySelectorAll("#aiSlot select[data-pv]")) out[s.dataset.pv] = s.value;
    return out;
  }

  function msg(text, bad) { $("aiMsg").textContent = text || ""; $("aiMsg").style.color = bad ? "var(--bad)" : ""; }
  function kindsOn() { return Array.from(document.querySelectorAll("#aiSlot input[data-kind]")).filter((i) => i.checked).map((i) => i.dataset.kind); }

  function syncStart() {
    syncPickers();
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
    if (live && live.job_id) watch(live.job_id, live.group_id, live.model_label);
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
        const used = promptsText(g.prompts, versions);
        if (used) row.appendChild(el("span", { className: "pv-used meta" }, used));
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
        body: JSON.stringify({ model_id: model, kinds: kindsOn(), output_language: $("aiLang").value,
          prompt_versions: promptVersionsBody(versions, picks(), kindsOn()),
          diarization_run_id: window.Speakers ? window.Speakers.labelRunId() : undefined }) });
      msg("");
      watch(r.job_id, r.group_id, (models.find((m) => m.id === model) || {}).label);
    } catch (e) { msg("Could not start: " + e.message, true); syncStart(); }
  }
  function watch(jobId, groupId, label) {
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
    if (window.Insights) window.Insights.attach($("aiInsightsHost"), jobId);
    if (window.LiveStream) window.LiveStream.attach(jobId, label);
    $("aiCancel").hidden = false;
    syncStart();
    poll();
  }
  function stopWatch() {
    if (watching) clearInterval(watching.timer);
    watching = null;
    if (window.LiveStream) window.LiveStream.detach();
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
    const kinds = tabKinds(g.kinds.map((x) => x.kind));
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
    renderKind($("aiPanel"), kd, g);
    const out = kd && kd.output;
    $("aiCopy").disabled = $("aiDownload").disabled = !out;
    $("aiCopy").onclick = () => copyMd(`/api/outputs/${out.id}/markdown`);
    $("aiDownload").onclick = () => download(`/api/outputs/${out.id}/markdown?download=1`);
    const any = g.kinds.some((x) => x.output);
    $("aiCopyAll").disabled = $("aiDownloadAll").disabled = !any;
    $("aiCopyAll").onclick = () => copyMd(`/api/analyses/${g.group_id}/markdown`);
    $("aiDownloadAll").onclick = () => download(`/api/analyses/${g.group_id}/markdown?download=1`);
    for (const row of document.querySelectorAll(".analysis")) row.classList.remove("sel");
    $("aiInspect").disabled = !kd || !kd.run_id;
    $("aiInspect").onclick = () => window.Insights && window.Insights.inspect(kd.run_id);
    $("aiLog").disabled = !g.job_id;
    $("aiLog").onclick = () => window.Insights && window.Insights.attach($("aiInsightsHost"), g.job_id, { finished: !["queued", "running"].includes(g.status) });
  }

  function renderKind(panel, kd, g) {
    panel.replaceChildren();
    if (!kd) return;
    if (kd.status === "failed" || kd.status === "cancelled") {
      panel.appendChild(el("div", { className: "bad-note" }, `${kd.status}: ${kd.error || ""}`));
      const job = retryJob(kd, g);
      if (job) {
        const b = el("button", { type: "button", className: "btn sm" }, "Retry");
        b.onclick = async () => {
          b.disabled = true;
          try { await api(`/api/jobs/${job}/retry`, { method: "POST" }); msg(""); watch(job, g.group_id, g.model_label); }
          catch (e) { b.disabled = false; msg("Retry failed: " + e.message, true); }
        };
        panel.appendChild(b);
      }
      return;
    }
    if (kd.status !== "done") { panel.appendChild(el("div", { className: "meta" }, "Working on it...")); return; }
    if (kd.schema_valid === false || !kd.output) {
      panel.appendChild(el("div", { className: "bad-note" }, "model returned invalid JSON"));
      if (kd.validation_errors) panel.appendChild(el("div", { className: "meta" }, kd.validation_errors.join("; ")));
      panel.appendChild(el("pre", { className: "rawtext" }, kd.raw_text || ""));
      return;
    }
    const c = kd.output.content;
    if (countsLine(kd)) panel.appendChild(el("div", { className: "meta" }, countsLine(kd)));
    if (kd.kind === "brief") briefPanel(panel, c);
    else if (kd.kind === "checklist") { panel.appendChild(el("h3", null, c.title)); panel.appendChild(checklist(kd.output.id, c.items)); }
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

  function briefPanel(panel, c) {
    const v = briefView(c);
    panel.appendChild(el("h3", null, v.title));
    for (const n of v.notes) panel.appendChild(el("div", { className: "bad-note" }, n));
    panel.appendChild(el("p", { className: "summary" }, v.summary));
    for (const s of v.sections) {
      panel.appendChild(el("h3", null, s.heading));
      if (!s.items.length) { panel.appendChild(el("div", { className: "meta" }, s.empty)); continue; }
      panel.appendChild(briefList(s.items));
    }
  }
  function briefList(items) {
    const ul = el("ul");
    for (const x of items) {
      const li = el("li", { className: "item" + (x.unverified ? " unverified" : "") });
      if (x.jump !== null) {
        const b = el("button", { type: "button", className: "quote quote-play", title: "Play the recording from here" },
          x.after ? `${x.text} ▶ ${fmtClock(x.jump)}` : "▶ " + x.text);
        b.onclick = () => seekTo($("player"), x.jump);
        li.appendChild(b);
      } else li.appendChild(el("span", null, x.text));
      if (x.meta) li.appendChild(el("span", { className: "meta" }, "  " + x.meta));
      if (x.quote && x.quote.quote) li.appendChild(quoteNode(x.quote));
      if (x.sub.length) li.appendChild(briefList(x.sub));
      ul.appendChild(li);
    }
    return ul;
  }
  function itemNode(it, tag) {
    const li = el(tag || "li", { className: "item" + (it.verified === false ? " unverified" : "") });
    li.appendChild(el("span", null, it.text));
    const meta = itemMeta(it);
    if (meta) li.appendChild(el("span", { className: "meta" }, "  " + meta));
    if (it.quote) li.appendChild(quoteNode(it));
    return li;
  }
  function quoteNode(it) {
    if (typeof it.at_ms !== "number") return el("div", { className: "quote" }, quoteLabel(it));
    const b = el("button", { type: "button", className: "quote quote-play", title: "Play the recording from here" }, quoteLabel(it));
    b.onclick = () => seekTo($("player"), it.at_ms);
    return b;
  }
  function bullets(items) { const ul = el("ul"); for (const it of items) ul.appendChild(itemNode(it)); return ul; }
  function tagPills(tags) {
    const ul = el("ul", { className: "tagpills" });
    for (const t of tags) {
      const li = el("li", { className: "tagpill" + (t.verified === false ? " unverified" : ""), title: t.quote || "" }, t.name);
      if (typeof t.at_ms === "number") {
        li.classList.add("seek");
        li.tabIndex = 0;
        li.setAttribute("role", "button");
        li.title = `${t.quote || ""} (play from ${fmtClock(t.at_ms)})`;
        li.onclick = () => seekTo($("player"), t.at_ms);
        li.onkeydown = (ev) => { if (isActivateKey(ev)) { ev.preventDefault(); seekTo($("player"), t.at_ms); } };
      }
      ul.appendChild(li);
    }
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
      if (it.quote) li.appendChild(quoteNode(it));
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
  loadVersions();
})();
