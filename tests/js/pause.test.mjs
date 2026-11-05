import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const P = createRequire(import.meta.url)("../../web/record/pause.js");

const rec = (o = {}) => ({ recording: true, recId: "r1", stopping: false, paused: false, ...o });

test("idle: Start enabled, Pause and End disabled", () => {
  const v = P.view({ recording: false, recId: null, stopping: false, paused: false });
  assert.deepEqual([v.mic.text, v.mic.disabled, v.pause.text, v.pause.disabled, v.end.disabled], ["Start", false, "Pause", true, true]);
});

test("recording: Start disabled, Pause and End enabled", () => {
  const v = P.view(rec());
  assert.deepEqual([v.mic.text, v.mic.disabled, v.pause.text, v.pause.disabled, v.end.text, v.end.disabled], ["Start", true, "Pause", false, "End", false]);
});

test("paused: the button reads Continue and End is enabled", () => {
  const v = P.view(rec({ paused: true }));
  assert.equal(v.pause.text, "Continue");
  assert.equal(v.pause.disabled, false);
  assert.equal(v.end.disabled, false);
  assert.equal(v.status, "Paused: nothing is recorded");
});

test("during a reconnect (no ready) Pause and End stay enabled once there is a recording id", () => {
  const v = P.view(rec({ ready: false }));
  assert.equal(v.pause.disabled, false);
  assert.equal(v.end.disabled, false);
});

test("before the first ready (no id) Pause is disabled and End is enabled (queued stop)", () => {
  const v = P.view(rec({ recId: null }));
  assert.equal(v.pause.disabled, true);
  assert.equal(v.end.disabled, false);
});

test("finishing: all three disabled, nothing leaks the paused label", () => {
  const v = P.view(rec({ stopping: true, paused: true }));
  assert.deepEqual([v.mic.disabled, v.pause.disabled, v.end.disabled], [true, true, true]);
  assert.equal(v.pause.text, "Pause");
  assert.equal(v.end.text, "Finishing...");
});

test("sentinels keep their order in the queue and are sent in that order", () => {
  const q = [];
  const a = new ArrayBuffer(2), b = new ArrayBuffer(4), c = new ArrayBuffer(6);
  q.push(a, b, P.ctl("pause"), P.ctl("continue"), c);
  const sent = [];
  P.drain(q, (x) => sent.push(x));
  assert.equal(q.length, 0);
  assert.deepEqual(sent.map((x) => (typeof x === "string" ? JSON.parse(x).type : x.byteLength)), [2, 4, "pause", "continue", 6]);
});

test("trimming a full buffer drops the oldest audio, never a pause sentinel", () => {
  const blocks = Array.from({ length: 5 }, (_, i) => new ArrayBuffer(i + 1));
  const q = [blocks[0], P.ctl("pause"), blocks[1], blocks[2], blocks[3], blocks[4]];
  const t = P.trim(q, 3);
  assert.equal(t.filter((x) => !P.isCtl(x)).length, 3);
  assert.equal(t.filter(P.isCtl).length, 1);
  assert.deepEqual(t.filter((x) => !P.isCtl(x)).map((x) => x.byteLength), [3, 4, 5]);
});

const consts = { clipDb: -1, deadDb: -60 };
const st0 = () => ({ paused: false, blocks: 0, clips: 0, quiet: 0, level: -70 });

test("onBlock while paused updates the meter only: no count, no timer, no clip, no queue", () => {
  const r = P.blockStep({ ...st0(), blocks: 50, paused: true }, { rms: -20, peak: 0 }, 1234, consts);
  assert.equal(r.queued, false);
  assert.equal(r.blocks, 50);                       // timer = blocks / 10 does not advance
  assert.equal(r.clips, 0);                         // a peak above the clip line is not counted
  assert.equal(r.quiet, 0);
  assert.equal(r.lastBlockAt, 1234);
  assert.equal(r.level, -20);                       // the meter still moves
});

test("onBlock when recording counts and queues", () => {
  const r = P.blockStep({ ...st0(), blocks: 50 }, { rms: -20, peak: 0 }, 99, consts);
  assert.equal(r.queued, true);
  assert.equal(r.blocks, 51);
  assert.equal(r.clips, 1);
});

test("gap check is silent while paused and right after Continue (lastBlockAt reset)", () => {
  const base = { recording: true, wsOpen: true, visible: true, limit: 1500 };
  assert.equal(P.gapDue({ ...base, paused: true, now: 10000, lastBlockAt: 0 }), null);     // 10 s paused
  assert.equal(P.gapDue({ ...base, paused: false, now: 10000, lastBlockAt: 9990 }), null); // Continue reset it
  assert.equal(P.gapDue({ ...base, paused: false, now: 10000, lastBlockAt: 0 }), 10000);   // a real gap still sends
});

test("after a reconnect the server's paused state is adopted, unless a pause or continue is still queued", () => {
  assert.equal(P.reconcile(false, [], true), true);
  assert.equal(P.reconcile(true, [], false), false);
  assert.equal(P.reconcile(true, [new ArrayBuffer(2), P.ctl("pause")], false), true);       // local intent wins
  assert.equal(P.reconcile(false, [P.ctl("continue")], true), false);
  assert.equal(P.reconcile(true, [], undefined), true);                                     // old server: keep local
});
