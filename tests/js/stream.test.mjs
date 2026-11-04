import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { newState, apply, tokPerS, header, visible } = createRequire(import.meta.url)("../../web/analyse/stream.js");

const run = (events) => events.reduce((s, [type, data, t]) => apply(s, type, data, t), newState());

test("deltas accumulate text and token count", () => {
  const s = run([["attempt_start", { kind: "debrief", attempt: 1, chunk: null }, 0],
    ["delta", { text: "{\"a", n: 2 }, 1000], ["delta", { text: "\":1}", n: 3 }, 1100]]);
  assert.equal(s.text, "{\"a\":1}");
  assert.equal(s.tokens, 5);
  assert.equal(s.kind, "debrief");
  assert.equal(visible(s), true);
});

test("tok/s is client-side from the second delta on, null before", () => {
  let s = run([["attempt_start", { kind: "tags", attempt: 1, chunk: null }, 0], ["delta", { text: "a", n: 1 }, 1000]]);
  assert.equal(tokPerS(s), null);
  s = apply(s, "delta", { text: "b", n: 40 }, 2000);
  assert.equal(tokPerS(s), 40);
});

test("header: kind, attempt, tokens, tok/s", () => {
  const s = run([["attempt_start", { kind: "debrief", attempt: 1, chunk: null }, 0],
    ["delta", { text: "a", n: 1 }, 1000], ["delta", { text: "b", n: 319 }, 8000]]);
  assert.equal(header(s), "debrief · attempt 1 · 320 tokens · 46 tok/s");
});

test("header without a rate yet and with a map-reduce chunk", () => {
  const s = run([["attempt_start", { kind: "notes", attempt: 1, chunk: [2, 5] }, 0]]);
  assert.equal(header(s), "notes · chunk 2/5 · attempt 1 · 0 tokens");
});

test("invalid end then a new attempt resets the text and shows the retrying marker", () => {
  let s = run([["attempt_start", { kind: "tags", attempt: 1, chunk: null }, 0], ["delta", { text: "oops", n: 1 }, 10],
    ["attempt_end", { valid: false, finish_reason: "stop", tokens_out: 1 }, 20]]);
  assert.equal(s.text, "oops");                       // kept on screen until the retry begins
  assert.match(header(s), /retrying/);
  s = apply(s, "attempt_start", { kind: "tags", attempt: 2, chunk: null }, 30);
  assert.equal(s.text, "");
  assert.equal(s.tokens, 0);
  assert.match(header(s), /attempt 2 · 0 tokens · retrying/);
  s = apply(s, "delta", { text: "ok", n: 1 }, 40);
  assert.doesNotMatch(header(s), /retrying/);
});

test("a valid end clears the box", () => {
  const s = run([["attempt_start", { kind: "tags", attempt: 1, chunk: null }, 0], ["delta", { text: "{}", n: 1 }, 10],
    ["attempt_end", { valid: true, finish_reason: "stop", tokens_out: 1 }, 20]]);
  assert.equal(s.text, "");
  assert.equal(visible(s), false);
});

test("a snapshot restores text, counts and the attempt for a late page", () => {
  const s = run([["snapshot", { kind: "checklist", attempt: 2, chunk: null, text: "{\"title\":", pieces: 4, active: true,
    truncated: false, valid: null }, 5000]]);
  assert.equal(s.text, "{\"title\":");
  assert.equal(s.tokens, 4);
  assert.match(header(s), /^checklist · attempt 2 · 4 tokens/);
  assert.equal(visible(s), true);
});

test("an empty inactive snapshot is invisible", () => {
  const s = run([["snapshot", { kind: null, attempt: 0, chunk: null, text: "", pieces: 0, active: false, truncated: false }, 0]]);
  assert.equal(visible(s), false);
});

test("a fallback event does not touch the text", () => {
  const s = run([["attempt_start", { kind: "tags", attempt: 1, chunk: null }, 0], ["fallback", { reason: "http 400" }, 5]]);
  assert.equal(s.text, "");
  assert.equal(s.fallback, "http 400");
});
