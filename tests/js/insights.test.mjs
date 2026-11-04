import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { fmtDuration, fmtTps, fmtGb, fmtTokens, rowCells, passesLevel, logText, isNearBottom, mergeLines } =
  createRequire(import.meta.url)("../../web/analyse/insights.js");

test("missing values are a dash", () => {
  assert.equal(fmtDuration(null), "-");
  assert.equal(fmtTps(undefined), "-");
  assert.equal(fmtGb(null), "-");
  assert.equal(fmtTokens(null), "-");
});

test("duration, tok/s, GB and token formatting", () => {
  assert.equal(fmtDuration(1234), "1.2 s");
  assert.equal(fmtDuration(61000), "1:01");
  assert.equal(fmtTps(12.34), "12.3");
  assert.equal(fmtGb(7.25), "7.3 GB");
  assert.equal(fmtTokens(1200), "1200");
  assert.equal(fmtTokens(0), "0");
});

test("a finished step row uses the stored duration, pending shows dashes", () => {
  const done = { name: "analyse", status: "done", duration_ms: 61000, model: "details-gemma-cheap", tokens_in: 100,
    tokens_out: 50, tok_per_s: 12.5, ram_free_end_gb: 9.2, ram_free_start_gb: 12 };
  assert.deepEqual(rowCells(done, 0), ["analyse", "done", "1:01", "details-gemma-cheap", "100", "50", "12.5", "9.2 GB"]);
  assert.deepEqual(rowCells({ name: "lm_unload", status: "pending" }, 0), ["lm_unload", "pending", "-", "-", "-", "-", "-", "-"]);
});

test("a running step ticks from started_at and shows the start RAM", () => {
  const s = { name: "a", status: "running", started_at: "2026-11-04T10:00:00+00:00", duration_ms: null, ram_free_start_gb: 12 };
  const t0 = Date.parse(s.started_at);
  assert.equal(rowCells(s, t0 + 5000)[2], "5.0 s");
  assert.equal(rowCells(s, t0 + 65000)[2], "1:05");
  assert.equal(rowCells(s, t0)[7], "12.0 GB");
  assert.equal(rowCells({ ...s, status: "waiting_gpu", started_at: null }, t0)[2], "-");
});

test("level filter shows that level and above", () => {
  assert.equal(passesLevel("info", "all"), true);
  assert.equal(passesLevel("debug", "info"), false);
  assert.equal(passesLevel("warn", "info"), true);
  assert.equal(passesLevel("info", "warn"), false);
  assert.equal(passesLevel("error", "error"), true);
});

test("log line text is one readable line", () => {
  const l = { n: 3, ts: "2026-11-04T10:00:01+00:00", level: "info", step: "analyse", src: "llm", msg: "call", data: { tokens_in: 5 } };
  assert.match(logText(l), /INFO .*\[analyse\] llm: call .*tokens_in=5/);
  assert.match(logText({ ...l, step: null, data: {} }), /\[job\] llm: call$/);
});

test("auto-scroll pauses when the user scrolled up", () => {
  assert.equal(isNearBottom({ scrollTop: 900, clientHeight: 100, scrollHeight: 1000 }), true);
  assert.equal(isNearBottom({ scrollTop: 100, clientHeight: 100, scrollHeight: 1000 }), false);
});

test("new lines are appended by n without duplicates", () => {
  assert.deepEqual(mergeLines([{ n: 1 }, { n: 2 }], [{ n: 2 }, { n: 3 }]).map((x) => x.n), [1, 2, 3]);
});
