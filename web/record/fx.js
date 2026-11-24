// Visual layer for /record. Read-only: it watches the page that record.js drives and never changes its logic.
// - adds body.recording while a take is running (End button enabled) and body.paused while paused
// - draws a scrolling waveform from the level meter (#bar width, 0-100%)
(function () {
  const $ = (id) => document.getElementById(id);
  const end = $("end"), bar = $("bar"), status = $("status"), wrap = document.querySelector(".meterwrap");
  if (!end || !bar || !wrap) return;

  const syncState = () => {
    const rec = !end.disabled;
    document.body.classList.toggle("recording", rec);
    document.body.classList.toggle("paused", rec && /pause/i.test(status ? status.textContent : ""));
  };
  new MutationObserver(syncState).observe(end, { attributes: true, attributeFilter: ["disabled"] });
  if (status) new MutationObserver(syncState).observe(status, { childList: true, characterData: true, subtree: true });
  syncState();

  // Waveform canvas above the meter.
  const canvas = document.createElement("canvas");
  canvas.className = "wave";
  canvas.setAttribute("aria-hidden", "true");
  wrap.parentNode.insertBefore(canvas, wrap);
  const ctx = canvas.getContext("2d");
  const N = 96, hist = new Array(N).fill(0);
  let target = 0, smooth = 0, last = 0;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const size = () => {
    const r = canvas.getBoundingClientRect(), d = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, r.width * d); canvas.height = Math.max(1, r.height * d);
    ctx.setTransform(d, 0, 0, d, 0, 0);
  };
  size(); window.addEventListener("resize", size);

  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  function frame(t) {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    target = (parseFloat(bar.style.width) || 0) / 100;
    smooth += (target - smooth) * 0.35;
    if (t - last > 55) { hist.push(document.body.classList.contains("recording") ? smooth : smooth * 0.4); hist.shift(); last = t; }
    ctx.clearRect(0, 0, w, h);
    const g = ctx.createLinearGradient(0, 0, w, 0);
    g.addColorStop(0, css("--accent") || "#7c83ff"); g.addColorStop(1, css("--accent-2") || "#22d3ee");
    ctx.fillStyle = g;
    const step = w / N, bw = Math.max(2, step * 0.55), mid = h / 2;
    for (let i = 0; i < N; i++) {
      const fade = 0.25 + 0.75 * (i / N);
      const amp = Math.max(1.5, hist[i] * hist[i] * (h * 0.48) * 1.6);
      ctx.globalAlpha = fade;
      const x = i * step + (step - bw) / 2, y = mid - amp;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x, y, bw, amp * 2, bw / 2); else ctx.rect(x, y, bw, amp * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    if (!reduce) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  if (reduce) setInterval(() => frame(performance.now()), 200);
})();
