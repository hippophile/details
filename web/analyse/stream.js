// llm-streaming: the "<model> is writing" box on the Analyse page. Shows the raw reply of the analyse step as it is
// written (SSE /api/jobs/{id}/stream). It is an UNCHECKED draft: validation runs on the final text on the server.
// Text is only ever set via textContent. Pure helpers are exported for `node --test` (tests/js/stream.test.mjs).
(function () {
  // ---- pure helpers -------------------------------------------------------------------------------------
  function newState() {
    return { kind: null, attempt: 0, chunk: null, text: "", tokens: 0, active: false, retrying: false,
      truncated: false, fallback: null, firstAt: null, firstN: 0, lastAt: null };
  }
  // Events: snapshot | attempt_start | delta | attempt_end | fallback | idle. `now` is a ms timestamp (client clock).
  function apply(state, type, d, now) {
    const s = { ...state };
    if (type === "snapshot") {
      Object.assign(s, newState(), { kind: d.kind, attempt: d.attempt, chunk: d.chunk, text: d.text || "", tokens: d.pieces || 0,
        active: !!d.active, truncated: !!d.truncated, retrying: false });
    } else if (type === "attempt_start") {
      Object.assign(s, newState(), { kind: d.kind, attempt: d.attempt, chunk: d.chunk, active: true,
        retrying: d.attempt > 1, fallback: state.fallback });
    } else if (type === "delta") {
      s.text = state.text + d.text;
      s.tokens = state.tokens + (d.n || 1);
      s.retrying = false;
      if (s.firstAt == null) { s.firstAt = now; s.firstN = s.tokens; }
      s.lastAt = now;
    } else if (type === "attempt_end") {
      s.active = false;
      if (d.valid === true) { s.text = ""; s.tokens = 0; s.firstAt = s.lastAt = null; }        // the validated result takes over
      else if (d.valid === false) s.retrying = true;
      else { s.text = ""; s.tokens = 0; s.firstAt = s.lastAt = null; }                          // cancelled / failed call
    } else if (type === "fallback") {
      s.fallback = d.reason;
    } else if (type === "idle") {
      Object.assign(s, newState());
    }
    return s;
  }
  // Tokens per second as seen by this browser (estimate, not measured by LM Studio): null until two deltas arrived.
  function tokPerS(s) {
    if (s.firstAt == null || s.lastAt == null || s.lastAt <= s.firstAt || s.tokens <= s.firstN) return null;
    return (s.tokens - s.firstN) / ((s.lastAt - s.firstAt) / 1000);
  }
  function header(s) {
    const parts = [s.kind || "reply"];
    if (s.chunk) parts.push(`chunk ${s.chunk[0]}/${s.chunk[1]}`);
    parts.push(`attempt ${s.attempt}`, `${s.tokens} tokens`);
    const r = tokPerS(s);
    if (r != null) parts.push(`${Math.round(r)} tok/s`);
    if (s.retrying) parts.push("retrying");
    return parts.join(" · ");
  }
  function visible(s) { return s.active || s.text.length > 0; }
  function liveTitle(label) { return label ? `${label} is writing` : "The model is writing"; }
  function isNearBottom(e) { return e.scrollHeight - e.scrollTop - e.clientHeight < 24; }
  const exported = { newState, apply, tokPerS, header, visible, liveTitle, isNearBottom };
  if (typeof module !== "undefined" && module.exports) { module.exports = exported; return; }

  // ---- page ---------------------------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const TYPES = ["snapshot", "attempt_start", "delta", "attempt_end", "fallback", "idle"];
  let cur = null;      // { jobId, es, state, stick, ended }

  function render() {
    if (!cur) return;
    const s = cur.state, box = $("liveBox"), pre = $("liveText");
    box.hidden = !(visible(s) || cur.unavailable);
    if (cur.unavailable) { $("liveHead").textContent = ""; $("liveNote").textContent = "live view unavailable"; pre.textContent = ""; return; }
    $("liveHead").textContent = header(s);
    $("liveNote").textContent = "draft, not yet checked" + (s.truncated ? " (start of the reply cut from this view)" : "") +
      (s.fallback ? " · live text off: " + s.fallback : "");
    const near = cur.stick;
    pre.textContent = s.text;
    if (near) pre.scrollTop = pre.scrollHeight;
  }

  function attach(jobId, label) {
    detach();
    if (!window.EventSource || !$("liveBox")) return;
    $("liveTitle").textContent = liveTitle(label);
    cur = { jobId, es: null, state: newState(), stick: true, unavailable: false, ended: false };
    const pre = $("liveText");
    pre.onscroll = () => { if (cur) cur.stick = isNearBottom(pre); };
    const es = new EventSource(`/api/jobs/${jobId}/stream`);
    cur.es = es;
    for (const t of TYPES) {
      es.addEventListener(t, (ev) => {
        if (!cur || cur.es !== es) return;
        try { cur.state = apply(cur.state, t, JSON.parse(ev.data), Date.now()); } catch (e) { return; }
        render();
      });
    }
    es.addEventListener("end", () => { if (cur && cur.es === es) { cur.ended = true; es.close(); } });
    es.onerror = () => {      // not a clean end: stop (EventSource would retry forever) and say so; the progress bar still works
      if (!cur || cur.es !== es || cur.ended) return;
      es.close();
      cur.unavailable = true;
      render();
    };
  }

  function detach() {
    if (cur && cur.es) cur.es.close();
    cur = null;
    const box = $("liveBox");
    if (box) { box.hidden = true; $("liveText").textContent = ""; $("liveHead").textContent = ""; $("liveNote").textContent = ""; }
  }

  window.LiveStream = { attach, detach };
})();
