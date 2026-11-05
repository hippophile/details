// Record page: the Start / Pause-Continue / End state machine as pure functions (also loaded by `node --test`,
// tests/js/pause.test.mjs). No DOM, no WebSocket here; record.js applies the results.
(function () {
  const PAUSED_TEXT = "Paused: nothing is recorded";

  // st = { recording, recId, stopping, paused } -> what each button shows
  function view(st) {
    const live = !!st.recording && !st.stopping;
    return {
      mic: { text: "Start", disabled: !!st.recording },
      pause: { text: st.paused && !st.stopping ? "Continue" : "Pause", disabled: !(live && st.recId != null) },
      end: { text: st.stopping ? "Finishing..." : "End", disabled: !live, bad: !!st.recording },
      status: st.paused && !st.stopping ? PAUSED_TEXT : null,
    };
  }

  // pause / continue travel in the same queue as the audio blocks, so order is kept across a disconnect
  const ctl = (type) => ({ ctl: type });
  const isCtl = (x) => x != null && typeof x === "object" && typeof x.ctl === "string";

  // send everything queued, in order (audio = the ArrayBuffer itself, a control item = its JSON)
  function drain(queue, send) {
    while (queue.length) {
      const x = queue.shift();
      send(isCtl(x) ? JSON.stringify({ type: x.ctl }) : x);
    }
  }

  // keep at most `max` audio blocks (oldest dropped first); control items are never dropped
  function trim(queue, max) {
    let drop = queue.filter((x) => !isCtl(x)).length - max;
    if (drop <= 0) return queue;
    return queue.filter((x) => isCtl(x) || drop-- <= 0);
  }

  // One 100 ms block. While paused only the meter (level) and lastBlockAt move: no count, clip, dead-mic or queue.
  function blockStep(st, blk, now, k) {
    const level = blk.rms > st.level ? blk.rms : st.level * 0.7 + blk.rms * 0.3;
    const r = { paused: st.paused, blocks: st.blocks, clips: st.clips, quiet: st.quiet, level, lastBlockAt: now,
                clipped: false, queued: false };
    if (st.paused) return r;
    r.blocks = st.blocks + 1;
    r.clipped = blk.peak > k.clipDb;
    if (r.clipped) r.clips = st.clips + 1;
    r.quiet = blk.peak < k.deadDb ? st.quiet + 1 : 0;
    r.queued = true;
    return r;
  }

  // visibilitychange: -> ms of silence to report as a `gap`, or null (never while paused)
  function gapDue(o) {
    if (!o.visible || !o.recording || o.paused || !o.wsOpen) return null;
    return o.now - o.lastBlockAt > o.limit ? Math.round(o.now - o.lastBlockAt) : null;
  }

  // `ready` after a reconnect: adopt the server's paused flag, unless a pause/continue is still queued (local intent wins)
  function reconcile(localPaused, queue, serverPaused) {
    if (serverPaused === undefined || queue.some(isCtl)) return localPaused;
    return !!serverPaused;
  }

  const out = { PAUSED_TEXT, view, ctl, isCtl, drain, trim, blockStep, gapDue, reconcile };
  if (typeof module !== "undefined" && module.exports) module.exports = out;
  else window.RecordPause = out;
})();
