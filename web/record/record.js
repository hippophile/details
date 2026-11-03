// Record page: 16 kHz capture -> 100 ms int16 blocks -> WebSocket /api/record/ws (server writes a crash-safe WAV).
"use strict";
const $ = id => document.getElementById(id);
const RATE = 16000, MAX_BUFFER_BLOCKS = 6000;   // 10 min of 100 ms blocks (~19 MB)
const MIN_DB = -70, CLIP_DB = -1, DEAD_DB = -60, DEAD_BLOCKS = 100;

let ws, ctx, stream, node;
let recording = false, stopping = false, ready = false, recId = null;
let queue = [];                 // blocks (ArrayBuffer) not yet sent
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
const mmss = s => String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(Math.floor(s % 60)).padStart(2, "0");
function defaultTitle() {
  const d = new Date(), p = n => String(n).padStart(2, "0");
  return `Recording ${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function showError(msg) { $("error").textContent = msg; $("error").hidden = !msg; }
function setUi(on) {
  const b = $("mic");
  b.classList.toggle("bad", on);
  b.textContent = on ? "Stop" : "Mic on";
  b.disabled = false;
  $("title").disabled = on;
}
function resetMeter() {
  level = MIN_DB; $("bar").style.width = "0"; $("levelText").textContent = "mic off"; $("dead").hidden = true;
}

// ---- audio ----------------------------------------------------------------------------------------
function onBlock({ pcm, rms, peak }) {
  blocks++; lastBlockAt = performance.now();
  $("timer").textContent = mmss(blocks / 10);
  level = rms > level ? rms : level * 0.7 + rms * 0.3;
  const clipped = peak > CLIP_DB;
  if (clipped) { clips++; $("clips").textContent = `${clips} clip${clips === 1 ? "" : "s"}`; $("clips").classList.add("hit"); }
  $("bar").style.width = pos(level) + "%";
  $("bar").style.background = clipped ? "var(--bad)" : "var(--good)";
  $("levelText").textContent = `${level.toFixed(0)} dB`;
  quiet = peak < DEAD_DB ? quiet + 1 : 0;
  $("dead").hidden = quiet < DEAD_BLOCKS;
  queue.push(pcm);
  if (queue.length > MAX_BUFFER_BLOCKS) { queue.splice(0, queue.length - MAX_BUFFER_BLOCKS); $("bufwarn").hidden = false; }
  flush();
}

function flush() {
  if (ws && ws.readyState === 1 && ready) {
    while (queue.length) ws.send(queue.shift());
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
      ws.send(JSON.stringify({ type: "start", title: $("title").value.trim() || defaultTitle(),
                               script_id: sel ? sel.id : null, sample_rate: RATE }));
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
    $("status").textContent = stopping ? "Finishing..." : "Recording";
    flush();
  } else if (m.type === "saved") {
    stopping = "done"; showSaved(m.recording); ws.close();
  } else if (m.type === "empty") {
    stopping = "done"; finish(); $("status").textContent = "Too short (under 0.5 s) - nothing saved.";
  } else if (m.type === "error") {
    fail(m.message);
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
  blocks = 0; clips = 0; quiet = 0; queue = []; recId = null; ready = false; stopping = false;
  $("timer").textContent = "00:00"; $("clips").textContent = "0 clips"; $("clips").classList.remove("hit");
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
  $("mic").disabled = true; $("mic").textContent = "Finishing...";
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

$("mic").onclick = () => {
  if (recording) return stop();
  $("mic").disabled = true;
  start().catch(err => { closeAudio(); setUi(false); showError(err.message || String(err)); });
};
$("again").onclick = () => {
  $("saved").hidden = true; $("timer").textContent = "00:00"; $("title").value = ""; $("title").disabled = false;
  $("clips").textContent = "0 clips"; $("clips").classList.remove("hit"); $("status").textContent = "Press Mic on and read a script below.";
};

// Sleep / hidden-tab gaps are logged server-side (not recoverable audio).
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && recording && performance.now() - lastBlockAt > 1500 && ws && ws.readyState === 1)
    ws.send(JSON.stringify({ type: "gap", ms: Math.round(performance.now() - lastBlockAt) }));
});

loadScripts();
