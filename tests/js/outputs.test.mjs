import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { canStart, modelLabel, itemMeta, footer, groupLabel } = createRequire(import.meta.url)("../../web/analyse/outputs.js");

test("Start needs a chosen model, at least one kind, a done transcript and no running analysis", () => {
  assert.equal(canStart({ model: "", kinds: ["tags"], run: "r", busy: false }), false);
  assert.equal(canStart({ model: "m", kinds: [], run: "r", busy: false }), false);
  assert.equal(canStart({ model: "m", kinds: ["tags"], run: "", busy: false }), false);
  assert.equal(canStart({ model: "m", kinds: ["tags"], run: "r", busy: true }), false);
  assert.equal(canStart({ model: "m", kinds: ["tags"], run: "r", busy: false }), true);
});

test("model option label shows role and needs_gb, or the reason when not installed", () => {
  assert.equal(modelLabel({ label: "Gemma", role: "cheap", needs_gb: 7, installed: true }), "Gemma · cheap · ~7 GB");
  assert.match(modelLabel({ label: "Gemma", role: "nice", needs_gb: 17, installed: false, unavailable_reason: "not downloaded" }), /nice.*17 GB.*not downloaded/);
});

test("item meta lists owner, due, severity and unverified", () => {
  assert.equal(itemMeta({ owner: "Νίκος", due: "2026-11-15", verified: false }), "owner: Νίκος · due: 2026-11-15 · unverified");
  assert.equal(itemMeta({ owner: null, due: null, verified: true }), "");
});

test("footer and group label", () => {
  const g = { n: 2, model_label: "Gemma 4 E4B", wall_ms: 61000, tokens_in: 1200, tokens_out: 340 };
  assert.equal(footer(g), "Analysis #2 · Gemma 4 E4B · 1:01 · 1200 in / 340 out tokens");
  assert.equal(groupLabel(g), "Analysis #2 · Gemma 4 E4B · 1:01");
});
