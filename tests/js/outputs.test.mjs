import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { canStart, modelLabel, itemMeta, footer, groupLabel, pickerKinds, promptVersionsBody, promptsText, quoteLabel, countsLine, retryJob } = createRequire(import.meta.url)("../../web/analyse/outputs.js");

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

test("item meta lists owner, due, severity and quote not found", () => {
  assert.equal(itemMeta({ owner: "Νίκος", due: "2026-11-15", verified: false }), "owner: Νίκος · due: 2026-11-15 · quote not found");
  assert.equal(itemMeta({ owner: null, due: null, verified: true }), "");
});

test("footer and group label", () => {
  const g = { n: 2, model_label: "Gemma 4 E4B", wall_ms: 61000, tokens_in: 1200, tokens_out: 340 };
  assert.equal(footer(g), "Analysis #2 · Gemma 4 E4B · 1:01 · 1200 in / 340 out tokens");
  assert.equal(groupLabel(g), "Analysis #2 · Gemma 4 E4B · 1:01");
});

const VERSIONS = { checklist: [1, 2], tags: [1], notes: [1], debrief: [1] };

test("a version picker exists only for ticked kinds that have more than one version", () => {
  assert.deepEqual(pickerKinds(VERSIONS, ["checklist", "tags"]), ["checklist"]);
  assert.deepEqual(pickerKinds(VERSIONS, ["tags", "notes"]), []);
  assert.deepEqual(pickerKinds(VERSIONS, []), []);
  assert.deepEqual(pickerKinds({}, ["checklist"]), []);
});

test("request body: only the picked versions of ticked kinds that have a picker", () => {
  assert.deepEqual(promptVersionsBody(VERSIONS, { checklist: "1", tags: "1" }, ["checklist", "tags"]), { checklist: 1 });
  assert.deepEqual(promptVersionsBody(VERSIONS, { checklist: "2" }, ["tags"]), {});
  assert.deepEqual(promptVersionsBody(VERSIONS, {}, ["checklist"]), {});
});

test("analyses list shows the used version for kinds with more than one version", () => {
  assert.equal(promptsText({ checklist: 1, tags: 1 }, VERSIONS), "checklist v1");
  assert.equal(promptsText({ checklist: 2 }, VERSIONS), "checklist v2");
  assert.equal(promptsText({ tags: 1 }, VERSIONS), "");
  assert.equal(promptsText(null, VERSIONS), "");
});

test("a located quote is labelled with its time, one without stays plain", () => {
  assert.equal(quoteLabel({ quote: "θα κάνουμε deploy", at_ms: 161000 }), "▶ 02:41 “θα κάνουμε deploy”");
  assert.equal(quoteLabel({ quote: "x", at_ms: 3723000 }), "▶ 1:02:03 “x”");
  assert.equal(quoteLabel({ quote: "x", at_ms: 0 }), "▶ 00:00 “x”");
  assert.equal(quoteLabel({ quote: "θα κάνουμε", verified: false }), "“θα κάνουμε”");
});

test("the count line is shown whenever a kind has quotes, even when none is missing", () => {
  assert.equal(countsLine({ verified: 9, unverified: 0 }),
    "9 quotes found in the transcript, 0 not found. Found means the words were said, not that the point is right.");
  assert.equal(countsLine({ verified: 1, unverified: 2 }),
    "1 quote found in the transcript, 2 not found (greyed out). Found means the words were said, not that the point is right.");
  assert.equal(countsLine({ verified: 0, unverified: 0 }), "");
});

test("Retry is offered for a failed kind with a job, not for a cancelled or finished one", () => {
  assert.equal(retryJob({ status: "failed" }, { job_id: "j1" }), "j1");
  assert.equal(retryJob({ status: "failed" }, { job_id: null }), null);
  assert.equal(retryJob({ status: "cancelled" }, { job_id: "j1" }), null);
  assert.equal(retryJob({ status: "done" }, { job_id: "j1" }), null);
});
