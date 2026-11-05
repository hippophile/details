// Record page: 16 kHz capture -> 100 ms int16 blocks -> WebSocket /api/record/ws (server writes a crash-safe WAV).
"use strict";
const $ = id => document.getElementById(id);
const RATE = 16000, MAX_BUFFER_BLOCKS = 6000;   // 10 min of 100 ms blocks (~19 MB)
const P = window.RecordPause;     // Start / Pause / End state machine (pause.js, pure functions)
const MIN_DB = -70, CLIP_DB = -1, DEAD_DB = -60, DEAD_BLOCKS = 100;

let ws, ctx, stream, node;
let recording = false, stopping = false, ready = false, recId = null, paused = false;
let queue = [];                 // blocks (ArrayBuffer) and pause/continue sentinels not yet sent, in order
let blocks = 0, clips = 0, quiet = 0, level = MIN_DB, lastBlockAt = 0, reconnectTimer = null;
let scripts = [], activeTab = 0;

// ---- scripts / tabs -------------------------------------------------------------------------------
function renderScript(body) {
  const panel = document.createElement("div");
  panel.className = "script";
  const colour = new Map();
  for (const line of body.split("\n")) {
    if (!line.trim()) continue;
    const p = document.createElement("p");
    const m = line.match(/^\*\*(.+?):\*\*\s*(.*)$/);
    if (m) {
      if (!colour.has(m[1])) colour.set(m[1], colour.size % 3 + 1);
      const b = document.createElement("b");
      b.className = "s" + colour.get(m[1]);
      b.textContent = m[1] + ":";
      p.append(b, " " + m[2]);
    } else {
      p.textContent = line;
    }
    panel.appendChild(p);
  }
  return panel;
}

function selectTab(i, focus) {
  activeTab = i;
  document.querySelectorAll(".tab").forEach((t, j) => {
    t.classList.toggle("active", j === i);
    t.setAttribute("aria-selected", j === i);
    t.tabIndex = j === i ? 0 : -1;
    if (focus && j === i) t.focus();
  });
  document.querySelectorAll("#panels .script").forEach((p, j) => p.hidden = j !== i);
}

async function loadScripts() {
  try { scripts = await (await fetch("/api/scripts")).json(); } catch (e) { scripts = []; }
  scripts.forEach((s, i) => {
    const t = document.createElement("button");
    t.className = "tab"; t.type = "button"; t.setAttribute("role", "tab");
    t.textContent = s.name;
    t.onclick = () => selectTab(i);
    t.onkeydown = e => {
      if (e.key === "ArrowRight") { e.preventDefault(); selectTab((i + 1) % scripts.length, true); }
      if (e.key === "ArrowLeft") { e.preventDefault(); selectTab((i + scripts.length - 1) % scripts.length, true); }
    };
    $("tabs").appendChild(t);
    $("panels").appendChild(renderScript(s.body_md));
  });
  if (scripts.length) selectTab(0);
}

// ---- helpers --------------------------------------------------------------------------------------
const pos = db => Math.max(0, Math.min(100, (db - MIN_DB) / -MIN_DB * 100));
// Silence line (as in v1): where speech starts on the meter. Remembered per browser; visual only for now.
const threshold = () => +$("threshold").value;
function placeThreshold() {
  $("thr").style.left = pos(threshold()) + "%";
  $("thrVal").textContent = threshold() + " dB";
  try { localStorage.setItem("details.silence_db", String(threshold())); } catch (_) {}
}
try { const v = localStorage.getItem("details.silence_db"); if (v) $("threshold").value = v; } catch (_) {}
$("threshold").addEventListener("input", placeThreshold);
placeThreshold();
const mmss = s => String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(Math.floor(s % 60)).padStart(2, "0");
function defaultTitle() {
  const d = new Date(), p = n => String(n).padStart(2, "0");
  return `Recording ${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function showError(msg) { $("error").textContent = msg; $("error").hidden = !msg; }
function applyView() {
  const v = P.view({ recording, recId, stopping, paused });
  for (const [id, x] of [["mic", v.mic], ["pause", v.pause], ["end", v.end]]) {
    $(id).textContent = x.text; $(id).disabled = x.disabled;
  }
  $("end").classList.toggle("bad", !!v.end.bad);
}
function runStatus() {
  $("status").textContent = stopping ? "Finishing..." : !ready && recId != null ? "Connection lost - reconnecting (audio is buffered)..."
    : paused ? P.PAUSED_TEXT : "Recording";
}
function setUi(on) {
  paused = false;
  applyView();
  $("title").disabled = on;
  $("liveToggle").disabled = on;
}
function resetMeter() {
  level = MIN_DB; $("bar").style.width = "0"; $("levelText").textContent = "mic off"; $("dead").hidden = true;
}

// ---- audio ----------------------------------------------------------------------------------------
function onBlock({ pcm, rms, peak }) {
  // While paused only the meter and lastBlockAt move (no count, timer, clips, dead-mic or queue): see pause.js
  const r = P.blockStep({ paused, blocks, clips, quiet, level }, { rms, peak }, performance.now(), { clipDb: CLIP_DB, deadDb: DEAD_DB });
  level = r.level; lastBlockAt = r.lastBlockAt;
  const clipped = peak > CLIP_DB;
  if (r.queued) {
    blocks = r.blocks;
    $("timer").textContent = mmss(blocks / 10);
    if (r.clipped) { clips = r.clips; $("clips").textContent = `${clips} clip${clips === 1 ? "" : "s"}`; $("clips").classList.add("hit"); }
  }
  const thr = threshold();
  $("bar").style.width = pos(level) + "%";
  $("bar").style.background = clipped ? "var(--bad)" : level > thr ? "var(--good)" : "var(--muted)";
  $("levelText").textContent = `${level.toFixed(0)} dB - ` + (clipped ? "clipping, move back" : level < thr ? "silence"
    : level < -40 ? "speech, but quiet" : level > -8 ? "too loud" : "good");
  if (!r.queued) return;
  quiet = r.quiet;
  $("dead").hidden = quiet < DEAD_BLOCKS;
  queue.push(pcm);
  if (queue.length > MAX_BUFFER_BLOCKS) { queue = P.trim(queue, MAX_BUFFER_BLOCKS); $("bufwarn").hidden = false; }
  flush();
}

function flush() {
  if (ws && ws.readyState === 1 && ready) {
    P.drain(queue, x => ws.send(x));
    if (stopping) { ws.send(JSON.stringify({ type: "stop" })); stopping = "sent"; }
  }
}

function closeAudio() {
  if (stream) stream.getTracks().forEach(t => t.stop());
  if (ctx) ctx.close();
  stream = ctx = node = null;
  resetMeter();
}

// ---- websocket ------------------------------------------------------------------------------------
function connect(first) {
  ws = new WebSocket(`ws://${location.host}/api/record/ws`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    if (first) {
      const sel = scripts[activeTab];
      const msg = { type: "start", title: $("title").value.trim() || defaultTitle(), script_id: sel ? sel.id : null, sample_rate: RATE };
      if ($("liveToggle").checked) { msg.live = true; msg.silence_db = threshold(); }
      ws.send(JSON.stringify(msg));
    } else {
      ws.send(JSON.stringify({ type: "resume", recording_id: recId }));
    }
  };
  ws.onmessage = e => onMessage(JSON.parse(e.data));
  ws.onclose = () => {
    ready = false;
    if (recording && stopping !== "done") {
      $("status").textContent = "Connection lost - reconnecting (audio is buffered)...";
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(() => recId ? connect(false) : fail("Server disconnected"), 1000);
    }
  };
}

function onMessage(m) {
  if (m.type === "ready") {
    recId = m.recording_id; ready = true;
    paused = P.reconcile(paused, queue, m.paused);      // after a resume: the server's state, never re-send pause
    runStatus(); applyView();
    flush();
  } else if (m.type === "saved") {
    stopping = "done"; showSaved(m.recording); ws.close();
  } else if (m.type === "empty") {
    stopping = "done"; finish(); $("status").textContent = "Too short (under 0.5 s) - nothing saved.";
  } else if (m.type === "error") {
    fail(m.message);
  } else if (m.type.startsWith("live_") && window.RecordLive) {
    window.RecordLive.onMessage(m);      // live text/notes never touch capture or sending
  }
}

function fail(msg) {
  recording = false; stopping = "done"; clearTimeout(reconnectTimer);
  closeAudio(); setUi(false); showError(msg); $("status").textContent = "Stopped.";
  if (ws) ws.close();
}

function finish() { recording = false; closeAudio(); setUi(false); }

// ---- start / stop ---------------------------------------------------------------------------------
async function start() {
  showError(""); $("bufwarn").hidden = true; $("saved").hidden = true;
  blocks = 0; clips = 0; quiet = 0; queue = []; recId = null; ready = false; stopping = false; paused = false;
  $("timer").textContent = "00:00"; $("clips").textContent = "0 clips"; $("clips").classList.remove("hit");
  window.RecordLive.reset();
  ctx = new AudioContext({ sampleRate: RATE });
  if (ctx.sampleRate !== RATE) {
    const got = ctx.sampleRate; closeAudio();
    throw new Error(`This browser gave ${got} Hz instead of ${RATE} Hz, so recording is refused (no silent conversion). Try Chrome or Brave.`);
  }
  stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
  await ctx.audioWorklet.addModule("/record/worklet.js");
  node = new AudioWorkletNode(ctx, "cap");
  node.port.onmessage = e => onBlock(e.data);
  ctx.createMediaStreamSource(stream).connect(node);
  node.connect(ctx.destination);   // outputs silence; keeps the graph pulling
  recording = true; lastBlockAt = performance.now();
  setUi(true); $("status").textContent = "Connecting...";
  connect(true);
}

function stop() {
  stopping = true; recording = true;
  if (stream) stream.getTracks().forEach(t => t.stop());
  if (ctx) ctx.close();    // no more blocks after this
  stream = ctx = node = null;
  applyView(); runStatus();
  flush();
}

function showSaved(r) {
  finish();
  $("savedTitle").textContent = r.title;
  $("savedMeta").textContent = `${mmss(r.duration_ms / 1000)} - ${(r.size_bytes / 1048576).toFixed(1)} MB - ${clips} clip${clips === 1 ? "" : "s"}`;
  $("savedWarn").hidden = !r.warning; $("savedWarn").textContent = r.warning || "";
  $("analyse").href = `/analyse?rec=${encodeURIComponent(r.id)}`;
  $("saved").hidden = false;
  $("status").textContent = "Saved.";
}

$("end").onclick = () => { if (recording && !stopping) stop(); };
$("pause").onclick = () => {
  if (!recording || stopping || recId == null) return;
  paused = !paused;
  queue.push(P.ctl(paused ? "pause" : "continue"));   // ordered after the blocks already queued
  lastBlockAt = performance.now();                    // no false gap right after Continue
  runStatus(); applyView(); flush();
};
$("mic").onclick = () => {
  if (recording) return;
  $("mic").disabled = true;
  start().catch(err => { closeAudio(); setUi(false); showError(err.message || String(err)); });
};
$("liveToggle").onchange = () => window.RecordLive.show($("liveToggle").checked);
$("again").onclick = () => {
  $("saved").hidden = true; $("timer").textContent = "00:00"; $("title").value = ""; $("title").disabled = false;
  $("clips").textContent = "0 clips"; $("clips").classList.remove("hit"); $("status").textContent = "Press Start and read a script below.";
};

// Sleep / hidden-tab gaps are logged server-side (not recoverable audio).
document.addEventListener("visibilitychange", () => {
  const ms = P.gapDue({ visible: document.visibilityState === "visible", recording, paused, wsOpen: !!ws && ws.readyState === 1,
                        now: performance.now(), lastBlockAt, limit: 1500 });
  if (ms != null) ws.send(JSON.stringify({ type: "gap", ms }));
});

loadScripts();
