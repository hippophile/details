import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { fmtClock, seekTo, isActivateKey, fmtWall, pickRun, startBody } = createRequire(import.meta.url)("../../web/analyse/api.js");

test("fmtClock renders mm:ss and h:mm:ss", () => {
  assert.equal(fmtClock(65000), "01:05");
  assert.equal(fmtClock(0), "00:00");
  assert.equal(fmtClock(3725000), "1:02:05");
});

test("seekTo sets currentTime in seconds and plays", () => {
  const audio = { currentTime: 0, played: 0, play() { this.played++; return Promise.resolve(); } };
  seekTo(audio, 65000);
  assert.equal(audio.currentTime, 65);
  assert.equal(audio.played, 1);
});

test("seekTo survives a rejected play()", () => {
  const audio = { currentTime: 0, play() { return Promise.reject(new Error("blocked")); } };
  seekTo(audio, 1500);
  assert.equal(audio.currentTime, 1.5);
});

test("Enter and Space activate a row, other keys do not", () => {
  assert.ok(isActivateKey({ key: "Enter" }));
  assert.ok(isActivateKey({ key: " " }));
  assert.ok(!isActivateKey({ key: "a" }));
});

test("fmtWall", () => {
  assert.equal(fmtWall(9700), "9.7 s");
  assert.equal(fmtWall(125000), "2:05");
});

test("pickRun prefers the first done run that is not a live preview", () => {
  const live = { id: "a", status: "done", settings: { live_preview: true } };
  const full = { id: "b", status: "done", settings: {} };
  const old = { id: "c", status: "done" };
  assert.equal(pickRun([live, full, old]).id, "b");
  assert.equal(pickRun([live, { id: "r", status: "running" }, old]).id, "c");
});

test("pickRun falls back to a live preview when it is the only done run, and to nothing", () => {
  const live = { id: "a", status: "done", settings: { live_preview: true } };
  assert.equal(pickRun([{ id: "q", status: "queued" }, live]).id, "a");
  assert.equal(pickRun([{ id: "q", status: "failed" }]), undefined);
  assert.equal(pickRun([]), undefined);
});

test("pickRun prefers the newest done accurate run that is cleaned, else the newest cleaned, else today's rule", () => {
  const fast = { id: "f", status: "done", model_role: "fast", cleaned: true };
  const acc = { id: "a", status: "done", model_role: "accurate", cleaned: true };
  const accOld = { id: "o", status: "done", model_role: "accurate", cleaned: false };
  const accLive = { id: "l", status: "done", model_role: "accurate", cleaned: true, settings: { live_preview: true } };
  assert.equal(pickRun([fast, acc]).id, "a");
  assert.equal(pickRun([accLive, fast, acc]).id, "a");
  assert.equal(pickRun([fast, accOld]).id, "f");
  assert.equal(pickRun([accOld, { id: "x", status: "done" }]).id, "o");
  assert.equal(pickRun([{ id: "r", status: "running", model_role: "accurate", cleaned: true }, accOld]).id, "o");
});

test("startBody sends also only for a non-auto language and only when something is ticked", () => {
  assert.deepEqual(startBody("m", "el", true, ["en", "de"]), { model_id: "m", language: "el", vocab_hint: true, also: ["en", "de"] });
  assert.deepEqual(startBody("m", "el", false, []), { model_id: "m", language: "el", vocab_hint: false });
  assert.deepEqual(startBody("m", "auto", false, ["en"]), { model_id: "m", language: "auto", vocab_hint: false });
});

test("freshRun swaps the selected run for its reloaded copy, so a renumbered label is not stale", () => {
  const { freshRun } = createRequire(import.meta.url)("../../web/analyse/api.js");
  const old = { id: "b", label: "#2 · Whisper large-v3" };
  const reloaded = [{ id: "b", label: "#1 · Whisper large-v3" }];
  assert.equal(freshRun(reloaded, old).label, "#1 · Whisper large-v3");
  assert.equal(freshRun(reloaded, { id: "gone" }), null);
  assert.equal(freshRun(reloaded, null), null);
});
