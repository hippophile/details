import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const C = createRequire(import.meta.url)("../../web/analyse/compare.js");

test("tick limit: a third box is disabled with a reason, two boxes enable Compare", () => {
  assert.deepEqual(C.toggleTick([], "a"), ["a"]);
  assert.deepEqual(C.toggleTick(["a"], "b"), ["a", "b"]);
  assert.deepEqual(C.toggleTick(["a", "b"], "c"), ["a", "b"]);           // a third tick is refused
  assert.deepEqual(C.toggleTick(["a", "b"], "a"), ["b"]);                // unticking works
  assert.equal(C.boxDisabled(["a", "b"], "c"), true);
  assert.equal(C.boxDisabled(["a", "b"], "a"), false);
  assert.equal(C.boxDisabled(["a"], "c"), false);
  assert.match(C.limitReason(["a", "b"]), /two/i);
  assert.equal(C.limitReason(["a"]), "");
});

test("Compare selected is enabled only at exactly two", () => {
  assert.equal(C.canCompare([]), false);
  assert.equal(C.canCompare(["a"]), false);
  assert.equal(C.canCompare(["a", "b"]), true);
  assert.equal(C.compareLabel(["a"]), "Compare selected (1)");
  assert.equal(C.compareLabel(["a", "b"]), "Compare selected (2)");
});

test("counts text", () => {
  assert.equal(C.countsText("checklist", { items: 5, done: 2, with_due: 3, unverified: 0 }), "5 items · 2 done · 3 with due date");
  assert.equal(C.countsText("checklist", { items: 3, done: 0, with_due: 0, unverified: 0 }), "3 items · 0 done · 0 with due date");
  assert.equal(C.countsText("checklist", { items: 1, done: 0, with_due: 1, unverified: 2 }), "1 item · 0 done · 1 with due date · 2 unverified");
  assert.equal(C.countsText("tags", { tags: 2, unverified: 0 }), "2 tags");
  assert.equal(C.countsText("notes", { sections: 1, bullets: 4, unverified: 0 }), "1 section · 4 bullets");
  assert.equal(C.countsText("debrief", { decisions: 1, action_items: 2, risks: 0, open_questions: 1, unverified: 0 }),
    "1 decision · 2 action items · 0 risks · 1 open question");
});

test("side output summary: invalid, no outputs, counts", () => {
  assert.equal(C.sideSummary("tags", null), "no outputs");
  assert.equal(C.sideSummary("debrief", { invalid: true, counts: null }), "invalid output");
  assert.equal(C.sideSummary("tags", { invalid: false, counts: { tags: 1, unverified: 0 } }), "1 tag");
});

test("header lines: AI missing -> no AI step; timings are text", () => {
  const h = C.headerLines({ label: "#1", stt: { model_label: "Whisper turbo", wall_ms: 4200, rtf: 7.1 }, ai: null, date: "2026-11-03T10:00:00+00:00" });
  assert.match(h.stt, /Whisper turbo.*4\.2 s.*7\.1×/);
  assert.equal(h.ai, "no AI step");
  const g = C.headerLines({ label: "#1", stt: { model_label: "W", wall_ms: 9700, rtf: 3.1 }, ai: { model_label: "Gemma E4B", wall_ms: 12000 }, date: "2026-11-03T10:00:00+00:00" });
  assert.match(g.ai, /Gemma E4B.*12\.0 s/);
});

test("diff counter text", () => {
  assert.equal(C.diffCounter({ delA: 3, insB: 5, nA: 100, nB: 102 }), "A: 3 of 100 words differ · B: 5 of 102");
});

test("compare url keeps rec and compare id", () => {
  assert.equal(C.compareUrl("r1", "c1"), "/analyse?rec=r1&compare=c1");
  assert.equal(C.compareUrl("r1", null), "/analyse?rec=r1");
});

test("pane header text: name, version, and the check as plain words", () => {
  assert.equal(C.promptText("checklist", { name: "checklist", version: 1, checks: [] }), "checklist v1");
  assert.equal(C.promptText("checklist", { name: "checklist", version: 2, checks: ["due_year_in_transcript"] }), "checklist v2 + year check");
  assert.equal(C.promptText("checklist", null), "");
  assert.equal(C.promptText("tags", undefined), "");
});

test("brief counts: final and replaced decisions, open and settled disagreements, the rest", () => {
  const c = { decisions_final: 2, decisions_replaced: 1, disagreements_open: 1, disagreements_settled: 3, action_items: 1, open_questions: 0, chapters: 4, unverified: 0 };
  assert.equal(C.countsText("brief", c), "2 final decisions · 1 replaced · 1 open disagreement · 3 settled · 1 action item · 0 open questions · 4 chapters");
  assert.equal(C.countsText("brief", { ...c, decisions_final: 1, unverified: 2 }), "1 final decision · 1 replaced · 1 open disagreement · 3 settled · 1 action item · 0 open questions · 4 chapters · 2 unverified");
  assert.equal(C.sideSummary("brief", { invalid: false, counts: c }).startsWith("2 final decisions"), true);
});
