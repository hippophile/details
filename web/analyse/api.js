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
  const out = { fmtClock, fmtWall, seekTo, isActivateKey, api, el };
  if (typeof module !== "undefined" && module.exports) module.exports = out;
  else window.AnalyseApi = out;
})();
