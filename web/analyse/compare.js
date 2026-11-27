// Compare two runs on the Analyse page: ticks in the runs list, side-by-side transcript word diff (Web Worker, rendered in
// batches), output counts, different-transcripts warning, script scores. Text is only ever set via textContent.
// Pure helpers are exported for `node --test` (tests/js/compare.test.mjs).
(function () {
  const A = typeof module !== "undefined" && module.exports ? require("./api.js") : window.AnalyseApi;
  const { api, el, fmtWall } = A;

  // ---- pure helpers -------------------------------------------------------------------------------------
  function toggleTick(ticked, key) {
    if (ticked.includes(key)) return ticked.filter((k) => k !== key);
    return ticked.length >= 2 ? ticked.slice() : [...ticked, key];
  }
  function boxDisabled(ticked, key) { return ticked.length >= 2 && !ticked.includes(key); }
  function limitReason(ticked) { return ticked.length >= 2 ? "Two runs are ticked: untick one to pick another." : ""; }
  function canCompare(ticked) { return ticked.length === 2; }
  function compareLabel(ticked) { return `Compare selected (${ticked.length})`; }
  function compareUrl(rec, cid) { return `/analyse?rec=${encodeURIComponent(rec)}` + (cid ? `&compare=${encodeURIComponent(cid)}` : ""); }
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
  function countsText(kind, c) {
    let parts;
    if (kind === "checklist") parts = [plural(c.items, "item"), `${c.done} done`, `${c.with_due || 0} with due date`];
    else if (kind === "tags") parts = [plural(c.tags, "tag")];
    else if (kind === "notes") parts = [plural(c.sections, "section"), plural(c.bullets, "bullet")];
    else if (kind === "brief") parts = [`${c.decisions_final} final ${c.decisions_final === 1 ? "decision" : "decisions"}`, `${c.decisions_replaced} replaced`,
      `${c.disagreements_open} open ${c.disagreements_open === 1 ? "disagreement" : "disagreements"}`, `${c.disagreements_settled} settled`,
      plural(c.action_items, "action item"), plural(c.open_questions, "open question"), plural(c.chapters, "chapter")];
    else parts = [plural(c.decisions, "decision"), plural(c.action_items, "action item"), plural(c.risks, "risk"), plural(c.open_questions, "open question")];
    if (c.unverified) parts.push(`${c.unverified} unverified`);
    return parts.join(" · ");
  }
  function sideSummary(kind, s) {
    if (!s) return "no outputs";
    if (s.invalid) return "invalid output";
    if (!s.counts) return s.status && s.status !== "done" ? s.status : "no outputs";
    return countsText(kind, s.counts);
  }
  function headerLines(side) {
    const s = side.stt || {};
    const stt = [s.model_label, s.wall_ms ? fmtWall(s.wall_ms) : null, s.rtf ? s.rtf.toFixed(1) + "×" : null].filter(Boolean).join(" · ");
    const ai = side.ai ? [side.ai.model_label, side.ai.wall_ms ? fmtWall(side.ai.wall_ms) : null].filter(Boolean).join(" · ") : "no AI step";
    return { stt, ai };
  }
  const CHECK_LABELS = { due_year_in_transcript: "year check" };
  // "checklist v2 + year check": the prompt version of one side, with the code checks that version carries.
  function promptText(kind, p) {
    if (!p || p.version == null) return "";
    return `${p.name || kind} v${p.version}` + (p.checks || []).map((c) => " + " + (CHECK_LABELS[c] || c)).join("");
  }
  function diffCounter(st) { return `A: ${st.delA} of ${st.nA} words differ · B: ${st.insB} of ${st.nB}`; }
  const exported = { toggleTick, boxDisabled, limitReason, canCompare, compareLabel, compareUrl, countsText, sideSummary, headerLines, diffCounter, promptText };
  if (typeof module !== "undefined" && module.exports) { module.exports = exported; return; }

  // ---- page ---------------------------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const rec = new URLSearchParams(location.search).get("rec");
  if (!rec) return;
  const TABS = [["diff", "Transcript diff"], ["brief", "Brief"], ["debrief", "Debrief"], ["notes", "Notes"], ["checklist", "Checklist"], ["tags", "Tags"]];
  const NODES_PER_FRAME = 500;
  let ticked = [], data = null, tab = "diff", texts = null, worker = null, raf = 0, token = 0, paneSide = "a";

  const keyOf = (runId, groupId) => `${runId}|${groupId || ""}`;
  const sideOf = (key) => { const [r, g] = key.split("|"); return g ? { stt_run_id: r, analysis_group_id: g } : { stt_run_id: r }; };

  // ---- ticks in the runs list -----------------------------------------------------------------------------
  function syncTicks() {
    for (const box of document.querySelectorAll("#runs input.cmp-tick")) {
      box.checked = ticked.includes(box.dataset.key);
      box.disabled = boxDisabled(ticked, box.dataset.key);
    }
    $("compareBtn").disabled = !canCompare(ticked);
    $("compareBtn").textContent = compareLabel(ticked);
    $("compareHint").textContent = limitReason(ticked);
  }
  function tickBox(key, what) {
    const box = el("input", { type: "checkbox", className: "cmp-tick" });
    box.dataset.key = key;
    box.setAttribute("aria-label", `Select ${what} for comparison`);
    box.onclick = (ev) => ev.stopPropagation();
    box.onchange = () => { ticked = toggleTick(ticked, key); syncTicks(); };
    return box;
  }
  function decorateRun(li, run) {
    if (run.status !== "done") return;
    li.prepend(tickBox(keyOf(run.id), run.label));
    li.dataset.runId = run.id;
    syncTicks();
  }
  // Analysis rows are built by outputs.js; add a tick to each one once its list appears (matched by label).
  async function tickAnalyses(li) {
    const ul = li.querySelector(".analyses");
    if (!ul || ul.dataset.ticked) return;
    ul.dataset.ticked = "1";
    let groups = [];
    try { groups = await api(`/api/stt-runs/${li.dataset.runId}/analyses`); } catch (e) { return; }
    for (const row of ul.children) {
      const label = row.querySelector(".label");
      const g = groups.find((x) => label && x.label === label.textContent);
      if (g && g.status === "done") row.prepend(tickBox(keyOf(li.dataset.runId, g.group_id), g.label));
    }
    syncTicks();
  }
  new MutationObserver((muts) => {
    for (const m of muts) for (const n of m.addedNodes) {
      if (n.nodeType === 1 && n.classList.contains("analyses") && n.parentElement && n.parentElement.dataset.runId) tickAnalyses(n.parentElement);
    }
  }).observe($("runs"), { childList: true, subtree: true });

  $("compareBtn").onclick = async () => {
    if (!canCompare(ticked)) return;
    $("compareBtn").disabled = true;
    try {
      const r = await api("/api/comparisons", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recording_id: rec, a: sideOf(ticked[0]), b: sideOf(ticked[1]) }) });
      history.pushState({ compare: r.id }, "", compareUrl(rec, r.id));
      await open(r.id);
    } catch (e) { $("compareHint").textContent = "Could not compare: " + e.message; }
    syncTicks();
  };

  // ---- view ---------------------------------------------------------------------------------------------
  function stop() {
    token++;
    if (worker) { worker.terminate(); worker = null; }
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
  }
  async function open(cid) {
    stop();
    document.body.classList.add("comparing");
    $("compareView").hidden = false;
    $("cmpProgress").textContent = "Loading…";
    try { data = await api(`/api/comparisons/${encodeURIComponent(cid)}`); }
    catch (e) { $("cmpProgress").textContent = "Could not load the comparison: " + e.message; return; }
    texts = null;
    tab = "diff";
    renderAll();
    try { texts = await api(`/api/comparisons/${encodeURIComponent(cid)}/transcripts`); } catch (e) { $("cmpProgress").textContent = "Could not load transcripts: " + e.message; return; }
    if (tab === "diff") renderDiff();
  }
  function close() {
    stop();
    data = texts = null;
    $("compareView").hidden = true;
    document.body.classList.remove("comparing");
    history.pushState({}, "", compareUrl(rec));
  }
  $("cmpClose").onclick = close;
  $("cmpSwap").onclick = () => {
    if (!data) return;
    data = { ...data, a: { ...data.a, side: "A" }, b: { ...data.b, side: "B" } };
    [data.a, data.b] = [data.b, data.a];
    data.a.side = "A"; data.b.side = "B";
    data.outputs = Object.fromEntries(Object.entries(data.outputs).map(([k, v]) => [k, { a: v.b, b: v.a }]));
    if (data.eval) data.eval = { ...data.eval, a: data.eval.b, b: data.eval.a };
    if (texts) texts = { a: texts.b, b: texts.a };
    renderAll();
    if (tab === "diff" && texts) renderDiff();
  };
  window.addEventListener("popstate", () => {
    const cid = new URLSearchParams(location.search).get("compare");
    if (cid) open(cid); else { stop(); $("compareView").hidden = true; document.body.classList.remove("comparing"); }
  });
  for (const b of $("cmpToggle").querySelectorAll("button")) {
    b.onclick = () => { paneSide = b.dataset.side; applyToggle(); };
  }
  function applyToggle() {
    $("cmpPanes").classList.toggle("show-a", paneSide === "a");
    $("cmpPanes").classList.toggle("show-b", paneSide === "b");
    for (const b of $("cmpToggle").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.side === paneSide));
  }

  function renderHeader(box, s, side) {
    box.replaceChildren();
    const h = headerLines(s);
    box.appendChild(el("div", { className: "pane-title" }, `${s.side} · ${s.label}`));
    box.appendChild(el("div", { className: "meta" }, `Speech: ${h.stt}`));
    box.appendChild(speedBar(s.stt.wall_ms, Math.max(data.a.stt.wall_ms || 0, data.b.stt.wall_ms || 0)));
    box.appendChild(el("div", { className: "meta" }, `AI: ${h.ai}`));
    if (s.ai) box.appendChild(speedBar(s.ai.wall_ms, Math.max((data.a.ai || {}).wall_ms || 0, (data.b.ai || {}).wall_ms || 0)));
    const pt = tab === "diff" ? "" : promptText(tab, ((data.outputs[tab] || {})[side] || {}).prompt);
    if (pt) box.appendChild(el("div", { className: "meta prompt-line" }, "Prompt: " + pt));
    box.appendChild(el("div", { className: "meta" }, new Date(s.date).toLocaleString()));
  }
  function speedBar(ms, max) {
    const bar = el("div", { className: "speed" }), fill = el("div");
    fill.style.width = ms && max ? Math.max(4, Math.round((100 * ms) / max)) + "%" : "0";
    bar.appendChild(fill);
    return bar;
  }

  function renderAll() {
    renderHeader($("cmpHeaderA"), data.a, "a");
    renderHeader($("cmpHeaderB"), data.b, "b");
    const w = $("cmpWarnings");
    w.replaceChildren();
    for (const x of data.warnings) w.appendChild(el("div", { className: "warn-line", role: "alert" }, "Warning: " + x.message));
    renderEval();
    const tabs = $("cmpTabs");
    tabs.replaceChildren();
    for (const [id, name] of TABS) {
      const t = el("button", { type: "button", className: "tab" + (id === tab ? " active" : "") }, name);
      t.setAttribute("role", "tab");
      t.setAttribute("aria-selected", String(id === tab));
      t.onclick = () => { tab = id; renderAll(); if (id === "diff" && texts) renderDiff(); };
      tabs.appendChild(t);
    }
    applyToggle();
    if (tab !== "diff") { stop(); $("cmpProgress").textContent = ""; renderOutputs(tab); }
    else { $("bodyA").replaceChildren(); $("bodyB").replaceChildren(); if (!texts) $("cmpProgress").textContent = "Loading transcripts…"; }
  }

  function renderEval() {
    const box = $("cmpEval");
    box.replaceChildren();
    if (!data.eval) { box.appendChild(el("div", { className: "meta" }, "no script reference for this recording")); return; }
    const e = data.eval;
    box.appendChild(el("div", { className: "meta" }, `Compared with the script "${e.script.name}". Assumes the speaker read it word for word: use it to rank runs, not as an absolute.`));
    const pct = (v) => (v == null ? "n/a" : v + "%");
    const rows = [
      ["Words wrong (WER), lower is better", (m) => m.wer_all + "%"],
      ["WER ignoring εεε/εμμ, lower is better", (m) => m.wer_no_hesitations + "%"],
      ["Tech words right, higher is better", (m) => pct(m.tech_terms.pct)],
      ["Numbers right, higher is better", (m) => pct(m.numbers.pct)],
      ["Missed tech terms", (m) => (m.tech_terms.missed.slice(0, 12).join(", ") || "-") + (m.tech_terms.missed.length > 12 ? ` (+${m.tech_terms.missed.length - 12})` : "")],
    ];
    const t = el("table", { className: "evaltable" });
    const head = el("tr");
    head.append(el("th", null, "Metric"), el("th", null, "A"), el("th", null, "B"));
    t.appendChild(head);
    for (const [name, f] of rows) {
      const tr = el("tr");
      tr.append(el("th", { scope: "row" }, name), el("td", null, f(e.a)), el("td", null, f(e.b)));
      t.appendChild(tr);
    }
    box.appendChild(t);
  }

  // ---- outputs ------------------------------------------------------------------------------------------
  function renderOutputs(kind) {
    const o = data.outputs[kind] || { a: null, b: null };
    for (const [s, box] of [["a", $("bodyA")], ["b", $("bodyB")]]) {
      box.replaceChildren();
      const v = o[s];
      box.appendChild(el("div", { className: "counts" + (v && v.invalid ? " bad" : "") }, sideSummary(kind, v)));
      if (v && v.content) box.appendChild(content(kind, v.content));
    }
  }
  const li = (it) => el("li", { className: it.verified === false ? "unverified" : "" }, it.text);
  const ul = (items) => { const u = el("ul"); for (const it of items) u.appendChild(li(it)); return u; };
  function content(kind, c) {
    const box = el("div", { className: "out" });
    if (kind === "checklist") {
      box.appendChild(el("h3", null, c.title));
      const u = el("ul", { className: "ckl" });
      for (const it of c.items) u.appendChild(el("li", { className: it.verified === false ? "unverified" : "" }, (it.done ? "☑ " : "☐ ") + it.text));
      box.appendChild(u);
    } else if (kind === "tags") {
      const u = el("ul", { className: "tagpills" });
      for (const t of c.tags) u.appendChild(el("li", { className: "tagpill" + (t.verified === false ? " unverified" : "") }, t.name));
      box.appendChild(u);
    } else if (kind === "notes") {
      box.appendChild(el("h3", null, c.title));
      for (const s of c.sections) { box.appendChild(el("h3", null, s.heading)); box.appendChild(ul(s.bullets)); }
    } else if (kind === "brief") {
      box.appendChild(el("p", null, c.summary));
      const final = (c.decisions || []).filter((d) => d.status !== "replaced");
      const lists = [["Decisions", final], ["Disagreements", (c.disagreements || []).map((x) => ({ text: x.topic + (x.settled_by ? " (settled)" : " (open)") }))],
        ["Action items", c.action_items], ["Open questions", c.open_questions]];
      for (const [title, items] of lists) if (items && items.length) { box.appendChild(el("h3", null, title)); box.appendChild(ul(items)); }
    } else {
      box.appendChild(el("p", null, c.summary));
      for (const [title, items] of [["Decisions", c.decisions], ["Action items", c.action_items], ["Risks", c.risks], ["Open questions", c.open_questions]]) {
        if (items && items.length) { box.appendChild(el("h3", null, title)); box.appendChild(ul(items)); }
      }
    }
    return box;
  }

  // ---- transcript diff (worker -> queue -> <= 500 nodes per animation frame) -------------------------------
  function renderDiff() {
    stop();
    const my = token;
    const bodyA = $("bodyA"), bodyB = $("bodyB"), prog = $("cmpProgress");
    bodyA.replaceChildren(); bodyB.replaceChildren();
    prog.textContent = "Comparing… 0%";
    const queue = [];
    let progress = 0, finished = null;
    const pump = () => {
      raf = 0;
      if (my !== token) return;
      let nodes = 0;
      const fa = document.createDocumentFragment(), fb = document.createDocumentFragment();
      while (queue.length && nodes < NODES_PER_FRAME) {
        const o = queue.shift();
        if (o.t === "eq") { fa.appendChild(document.createTextNode(o.a.join(" ") + " ")); fb.appendChild(document.createTextNode(o.b.join(" ") + " ")); nodes += 2; }
        else if (o.t === "del") { fa.appendChild(el("del", { className: "mark-del" }, o.a.join(" "))); fa.appendChild(document.createTextNode(" ")); nodes += 2; }
        else { fb.appendChild(el("ins", { className: "mark-ins" }, o.b.join(" "))); fb.appendChild(document.createTextNode(" ")); nodes += 2; }
      }
      bodyA.appendChild(fa); bodyB.appendChild(fb);
      if (queue.length || !finished) {
        if (!finished) prog.textContent = `Comparing… ${Math.round(progress * 100)}%`;
        raf = requestAnimationFrame(pump);
      } else {
        prog.textContent = diffCounter(finished.stats) + (finished.coarse ? " · coarse diff: some stretches are shown as whole blocks" : "");
      }
    };
    try { worker = new Worker("/analyse/diff-worker.js", { type: "module" }); }
    catch (e) { prog.textContent = "Could not start the diff worker: " + e.message; return; }
    worker.onmessage = (ev) => {
      if (my !== token) return;
      const m = ev.data;
      if (m.error) { prog.textContent = "Diff failed: " + m.error; return; }
      if (m.done) finished = m; else { progress = m.progress; queue.push(...m.ops); }
      if (!raf) raf = requestAnimationFrame(pump);
    };
    worker.onerror = (ev) => { prog.textContent = "The diff worker failed: " + (ev.message || "unknown error"); };
    worker.postMessage({ a: texts.a.text, b: texts.b.text, options: {} });
  }

  window.AnalyseCompare = { decorateRun, syncTicks };
  syncTicks();
  const cid = new URLSearchParams(location.search).get("compare");
  if (cid) open(cid);
})();
