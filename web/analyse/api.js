// Small helpers for the Analyse page (also loaded by `node --test`, see tests/js). Text is only ever set via textContent.
(function () {
  function fmtClock(ms) {
    const s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    const p = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${p(m)}:${p(x)}` : `${p(m)}:${p(x)}`;
  }
  function fmtWall(ms) {
    const s = ms / 1000;
    return s < 60 ? s.toFixed(1) + " s" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
  }
  function seekTo(audio, ms) {
    audio.currentTime = ms / 1000;
    try { const p = audio.play(); if (p && p.catch) p.catch(() => {}); } catch (e) { /* autoplay blocked */ }
  }
  function isActivateKey(ev) { return ev.key === "Enter" || ev.key === " "; }
  async function api(path, opts) {
    const r = await fetch(path, opts);
    let j = null;
    try { j = await r.json(); } catch (e) { /* non-JSON body */ }
    if (!r.ok) throw new Error((j && j.error) || `${r.status} ${r.statusText}`);
    return j;
  }
  function el(tag, props, text) {
    const e = document.createElement(tag);
    if (props) Object.assign(e, props);
    if (text != null) e.textContent = text;
    return e;
  }
  // The run Analyse opens first (runs are newest first): a done, non-live-preview run that is accurate and cleaned, else
  // the newest cleaned one, else the first done run that is not a live preview (live-assistant), else the first done run.
  function pickRun(runs) {
    const done = runs.filter((r) => r.status === "done");
    const full = done.filter((r) => !(r.settings && r.settings.live_preview));
    return full.find((r) => r.cleaned && r.model_role === "accurate") || full.find((r) => r.cleaned) || full[0] || done[0];
  }
  // POST body of a new run; `also` is only sent with a fixed language and at least one extra language ticked
  function startBody(modelId, language, hint, also) {
    const b = { model_id: modelId, language, vocab_hint: hint };
    if (language !== "auto" && also && also.length) b.also = also;
    return b;
  }
  // run numbers are positions, so deleting a run renumbers the rest: always show the reloaded copy
  function freshRun(runs, selected) {
    return (selected && runs.find((r) => r.id === selected.id)) || null;
  }
  // plain text of a whole transcript for the clipboard: one line per segment, "[mm:ss] " only on request, "Name: " when the segment has a speaker
  function transcriptText(segs, opts) {
    const stamps = !!(opts && opts.timestamps);
    return segs.map((s) => (stamps ? `[${fmtClock(s.start_ms)}] ` : "") + (s.speaker_name ? `${s.speaker_name}: ` : "") + String(s.text).trim()).join("\n");
  }
  // Transcript paragraphs: consecutive segments, new paragraph on a speaker change, a silence over gapMs, a sentence end once the
  // paragraph is minMs long, or when adding the segment would pass maxMs. Each: {start_ms, end_ms, speaker, segs}.
  function paragraphs(segs, opts) {
    const o = { gapMs: 1500, minMs: 8000, maxMs: 45000, ...(opts || {}) };
    const out = [];
    let prev = null, cur = null;
    for (const s of segs || []) {
      if (cur && (s.speaker !== cur.speaker || s.start_ms - prev.end_ms > o.gapMs ||
          (prev.end_ms - cur.start_ms >= o.minMs && /[.;!?…]\s*$/.test(String(prev.text))) || s.end_ms - cur.start_ms > o.maxMs)) cur = null;
      if (!cur) { cur = { start_ms: s.start_ms, end_ms: s.end_ms, speaker: s.speaker, segs: [] }; out.push(cur); }
      cur.segs.push(s); cur.end_ms = s.end_ms; prev = s;
    }
    return out;
  }
  function transcriptView(stored) { return stored === "lines" ? "lines" : "paragraphs"; }
  const out = { fmtClock, fmtWall, seekTo, isActivateKey, api, el, pickRun, startBody, freshRun, transcriptText, paragraphs, transcriptView };
  if (typeof module !== "undefined" && module.exports) module.exports = out;
  else window.AnalyseApi = out;
})();
