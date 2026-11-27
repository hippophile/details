import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { canStart, modelLabel, itemMeta, footer, groupLabel, pickerKinds, promptVersionsBody, promptsText, quoteLabel, countsLine, retryJob, briefView, tabKinds } = createRequire(import.meta.url)("../../web/analyse/outputs.js");

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

// ---- Brief (monday-brief) -----------------------------------------------------------------------------------
const BRIEF = {
  kind_of_recording: "meeting", summary: "Συζήτηση.", links: "ok",
  chapters: [{ title: "Intro", quote: "q", at_ms: 0 }, { title: "Budget", quote: "q", at_ms: 3900000 }, { title: "Lost", quote: "q" }],
  decisions: [
    { id: "d1", text: "Ship to all", quote: "q1", at_ms: 2102000, status: "replaced", replaces: [], replaced_by: "d2" },
    { id: "d2", text: "Ship to 2 first", quote: "q2", at_ms: 9670000, status: "final", replaces: ["d1"], replaced_by: null, verified: false }],
  disagreements: [
    { id: "x1", topic: "Budget", settled_by: null, positions: [{ who: "Filip", stance: "too much", quote: "a", at_ms: 4323000 }, { who: null, stance: "fine", quote: "b" }] },
    { id: "x2", topic: "Tool", settled_by: "d2", positions: [{ who: "A", stance: "x", quote: "c" }, { who: "B", stance: "y", quote: "d" }] }],
  action_items: [{ text: "Write docs", owner: "Nikos", due: "2026-12-01", done: false, quote: "e", at_ms: 10212000 }],
  open_questions: [{ text: "Which model", quote: "f", at_ms: 6055000 }],
};
const sec = (v, key) => v.sections.find((x) => x.key === key);

test("brief tab comes first whatever order the kinds arrive in", () => {
  assert.deepEqual(tabKinds(["tags", "debrief", "brief", "checklist"]), ["brief", "debrief", "checklist", "tags"]);
  assert.deepEqual(tabKinds(["notes"]), ["notes"]);
});

test("brief view: title, summary and sections in reading order", () => {
  const v = briefView(BRIEF);
  assert.equal(v.title, "BRIEF · meeting");
  assert.equal(v.summary, "Συζήτηση.");
  assert.deepEqual(v.sections.map((x) => x.key), ["decided", "disagreed", "todo", "questions", "chapters"]);
  assert.deepEqual(v.notes, []);
});

test("only the final decision is listed, the replaced one hangs under it as changed from with its time", () => {
  const d = sec(briefView(BRIEF), "decided").items;
  assert.equal(d.length, 1);
  assert.equal(d[0].text, "✓ Ship to 2 first");
  assert.equal(d[0].quote.quote, "q2");
  assert.equal(d[0].unverified, true);
  assert.equal(d[0].sub.length, 1);
  assert.equal(d[0].sub[0].text, "changed from: Ship to all");
  assert.equal(d[0].sub[0].jump, 2102000);
});

test("a chain of changes nests", () => {
  const c = structuredClone(BRIEF);
  c.decisions[1].status = "replaced";
  c.decisions.push({ id: "d3", text: "Only one", quote: "q3", at_ms: 9999000, status: "final", replaces: ["d2"], replaced_by: null });
  const d = sec(briefView(c), "decided").items;
  assert.equal(d.length, 1);
  assert.equal(d[0].sub[0].text, "changed from: Ship to 2 first");
  assert.equal(d[0].sub[0].sub[0].text, "changed from: Ship to all");
});

test("disagreements say still open or settled by the decision, and list each position with its quote", () => {
  const x = sec(briefView(BRIEF), "disagreed").items;
  assert.equal(x[0].text, "⚡ Budget — still open");
  assert.deepEqual(x[0].sub.map((s) => s.text), ["Filip: too much", "Unknown speaker: fine"]);
  assert.equal(x[0].sub[0].quote.at_ms, 4323000);
  assert.equal(x[1].text, "⚡ Tool — settled by: Ship to 2 first");
});

test("to do shows owner and due, questions and chapters jump to their time", () => {
  const v = briefView(BRIEF);
  const t = sec(v, "todo").items[0];
  assert.equal(t.text, "☐ Write docs");
  assert.equal(t.meta, "owner: Nikos · due: 2026-12-01");
  assert.equal(sec(v, "questions").items[0].text, "? Which model");
  const ch = sec(v, "chapters").items;
  assert.deepEqual(ch.map((c) => [c.text, c.jump]), [["00:00 Intro", 0], ["1:05:00 Budget", 3900000], ["Lost", null]]);
});

test("a lesson says so in every empty section and hides empty chapters", () => {
  const v = briefView({ ...BRIEF, kind_of_recording: "lesson", decisions: [], disagreements: [], action_items: [], open_questions: [], chapters: [], links: "skipped" });
  assert.equal(v.title, "BRIEF · lesson");
  assert.equal(sec(v, "decided").empty, "Nothing was decided.");
  assert.equal(sec(v, "disagreed").empty, "No disagreements.");
  assert.equal(sec(v, "todo").empty, "Nothing to do.");
  assert.equal(sec(v, "questions").empty, "No open questions.");
  assert.equal(sec(v, "chapters"), undefined);
  assert.deepEqual(v.notes, []);
});

test("failed links and foreign-alphabet warnings are said on the page, and every decision stays listed as final", () => {
  const c = structuredClone(BRIEF);
  c.links = "failed";
  c.script_warnings = ["summary"];
  c.decisions.forEach((d) => { d.status = "final"; d.replaces = []; d.replaced_by = null; });
  const v = briefView(c);
  assert.deepEqual(v.notes, ["Could not work out which decisions replaced earlier ones.", "Some text is in an unexpected alphabet."]);
  assert.equal(sec(v, "decided").items.length, 2);
});

test("a brief that had items dropped says so in one line", () => {
  const v = briefView({ ...BRIEF, dropped_not_meeting: { decisions: 2, disagreements: 1, action_items: 1 } });
  assert.deepEqual(v.notes, ["Not a meeting: 4 items the model proposed were left out."]);
  assert.deepEqual(briefView({ ...BRIEF, dropped_not_meeting: { decisions: 1, disagreements: 0, action_items: 0 } }).notes,
    ["Not a meeting: 1 item the model proposed was left out."]);
  assert.deepEqual(briefView({ ...BRIEF, dropped_not_meeting: { decisions: 0, disagreements: 0, action_items: 0 } }).notes, []);
});
