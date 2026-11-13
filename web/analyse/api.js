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
  // The run Analyse opens first: the first done run that is not a live preview (live-assistant), else the first done run.
  function pickRun(runs) {
    const done = runs.filter((r) => r.status === "done");
    return done.find((r) => !(r.settings && r.settings.live_preview)) || done[0];
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
  const out = { fmtClock, fmtWall, seekTo, isActivateKey, api, el, pickRun, freshRun, transcriptText };
  if (typeof module !== "undefined" && module.exports) module.exports = out;
  else window.AnalyseApi = out;
})();
