import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const A = createRequire(import.meta.url)("../../web/analyse/api.js");

const segs = [{ start_ms: 0, text: "Καλημέρα" }, { start_ms: 65000, text: "ok" }];

test("plain: one line per segment, no stamps", () => {
  assert.equal(A.transcriptText(segs, { timestamps: false }), "Καλημέρα\nok");
});

test("timestamps on: [mm:ss] prefix", () => {
  assert.equal(A.transcriptText(segs, { timestamps: true }), "[00:00] Καλημέρα\n[01:05] ok");
});

test("hour clock after 1 h", () => {
  assert.equal(A.transcriptText([{ start_ms: 3725000, text: "x" }], { timestamps: true }), "[1:02:05] x");
});

test("speaker name prefixes the text", () => {
  const s = [{ start_ms: 0, text: "hi", speaker_name: "Manos" }, { start_ms: 1000, text: "yo", speaker_name: null }];
  assert.equal(A.transcriptText(s, { timestamps: true }), "[00:00] Manos: hi\n[00:01] yo");
});

test("empty list gives empty text", () => {
  assert.equal(A.transcriptText([], { timestamps: false }), "");
});

test("text is trimmed and does not mutate input", () => {
  const s = [{ start_ms: 0, text: "  a  " }];
  assert.equal(A.transcriptText(s, {}), "a");
  assert.equal(s[0].text, "  a  ");
});
