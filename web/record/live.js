// Live assist card on /record: pure state + render helpers (also loaded by `node --test`, tests/js/live.test.mjs) and the
// small browser controller `window.RecordLive` that record.js feeds with the `live_*` WebSocket messages.
// Text is only ever set via textContent. Nothing here touches audio capture or the WebSocket.
(function () {
  const pad = (n) => String(n).padStart(2, "0");
  const mmss = (s) => pad(Math.floor(s / 60)) + ":" + pad(Math.floor(s % 60));

  function initialState() {
    return { state: "off", message: "", lines: [], partial: "", notes: null, notesAt: null, status: {} };
  }
  // -> a new state; `m` is one server message, `nowMs` the browser clock (for "updated ... ago")
  function apply(s, m, nowMs) {
    const n = { ...s };
    switch (m.type) {
      case "live_state": n.state = m.state; n.message = m.message || ""; break;
      case "live_commit":
        if (!s.lines.some((l) => l.idx === m.idx)) n.lines = [...s.lines, { idx: m.idx, start_ms: m.start_ms, end_ms: m.end_ms, text: m.text }];
        n.partial = "";
        break;
      case "live_partial": n.partial = m.text || ""; break;
      case "live_notes": n.notes = m; n.notesAt = nowMs == null ? Date.now() : nowMs; break;
      case "live_status": n.status = { ...s.status, [m.part]: { level: m.level, message: m.message } }; break;
      case "live_snapshot":
        n.state = m.state || s.state; n.message = m.message || "";
        n.lines = Array.isArray(m.lines) ? m.lines.map((l) => ({ idx: l.idx, start_ms: l.start_ms, end_ms: l.end_ms, text: l.text })) : s.lines;
        n.partial = "";
        if (m.notes) { n.notes = m.notes; n.notesAt = nowMs == null ? Date.now() : nowMs; }
        n.status = m.status ? { ...m.status } : s.status;
        break;
    }
    return n;
  }
  // one list entry: notes/actions have `text`, tags have `name`; no `verified: true` = not proven
  function itemView(it) {
    return { text: it.text != null ? it.text : it.name, owner: it.owner || null, unverified: it.verified !== true };
  }
  function agoText(sec) { return sec == null ? "no update yet" : "updated " + mmss(sec) + " ago"; }
  const STATE = { off: "off", waiting_gpu: "waiting for the GPU", loading: "loading the speech model", listening: "listening", failed: "stopped" };
  function stateText(s) { return s.message || (STATE[s.state] || s.state); }

  function render(doc, root, s, nowMs) {
    const mk = (tag, text, cls) => { const e = doc.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
    const part = (label, text, level) => { const d = mk("div", null, "lv " + (level || "ok")); d.append(mk("span", label + ": ", "lbl"), mk("span", text)); return d; };
    const sp = s.status.stt;
    const speech = sp && sp.level === "error" ? part("Speech", sp.message, "error")
      : part("Speech", s.state === "waiting_gpu" ? "waiting" : stateText(s), s.state === "failed" ? "error" : "ok");
    const nt = s.status.notes;
    const notes = part("Notes", nt ? nt.message : "waiting for the first update", nt ? nt.level : "ok");
    const gpu = part("GPU queue", s.state === "waiting_gpu" ? s.message || "waiting for the running job step" : s.state === "off" ? "-" : "live has the GPU; queued jobs wait", s.state === "waiting_gpu" ? "warn" : "ok");
    root.status.replaceChildren(speech, notes, gpu);
    root.lines.replaceChildren(...s.lines.map((l) => { const p = mk("p", l.text, "line"); p.title = mmss(l.start_ms / 1000); return p; }));
    root.partial.textContent = s.partial;
    const fill = (ul, items) => ul.replaceChildren(...items.map((it) => {
      const v = itemView(it), li = mk("li", null);
      li.append(mk("span", v.text));
      if (v.owner) li.append(mk("span", " (" + v.owner + ")", "owner"));
      if (v.unverified) li.append(mk("span", " unverified", "unv"));
      return li;
    }));
    const n = s.notes;
    fill(root.notes, n ? n.notes : []);
    fill(root.actions, n ? n.action_items : []);
    fill(root.tags, n ? n.tags : []);
    root.updated.textContent = agoText(s.notesAt == null ? null : Math.max(0, Math.round((nowMs - s.notesAt) / 1000)));
  }

  const out = { initialState, apply, itemView, agoText, stateText, render, mmss };
  if (typeof module !== "undefined" && module.exports) { module.exports = out; return; }

  // ---- browser controller -------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const root = { status: $("liveStatus"), lines: $("liveLines"), partial: $("livePartial"), notes: $("liveNotes"),
                 actions: $("liveActions"), tags: $("liveTags"), updated: $("liveUpdated") };
  let st = initialState(), timer = null;
  function paint() {
    const box = root.lines, stick = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
    render(document, root, st, Date.now());
    if (stick) box.scrollTop = box.scrollHeight;
  }
  window.RecordLive = {
    reset() { st = initialState(); paint(); },
    show(on) { $("live").hidden = !on; },
    onMessage(m) {
      st = apply(st, m, Date.now());
      paint();
      if (!timer) timer = setInterval(() => { if (!$("live").hidden && st.notesAt != null) paint(); }, 1000);
    },
  };
  window.RecordLive.reset();
})();
