import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const L = createRequire(import.meta.url)("../../web/record/live.js");

const fresh = () => L.initialState();

test("a commit is appended and clears the partial", () => {
  let s = fresh();
  s = L.apply(s, { type: "live_partial", text: "γεια σ" });
  assert.equal(s.partial, "γεια σ");
  s = L.apply(s, { type: "live_commit", idx: 0, start_ms: 0, end_ms: 1000, text: "γεια σας" });
  s = L.apply(s, { type: "live_commit", idx: 1, start_ms: 1000, end_ms: 2000, text: "καλημέρα" });
  assert.deepEqual(s.lines.map((l) => l.text), ["γεια σας", "καλημέρα"]);
  assert.equal(s.partial, "");
});

test("a repeated commit idx is not appended twice (resume)", () => {
  let s = fresh();
  s = L.apply(s, { type: "live_commit", idx: 0, start_ms: 0, end_ms: 1, text: "a" });
  s = L.apply(s, { type: "live_commit", idx: 0, start_ms: 0, end_ms: 1, text: "a" });
  assert.equal(s.lines.length, 1);
});

test("snapshot restores lines and the last notes", () => {
  const notes = { seq: 2, at_ms: 5000, notes: [], action_items: [], tags: [], verified: 0, unverified: 0 };
  const s = L.apply(fresh(), { type: "live_snapshot", state: "listening", message: "", lines: [{ idx: 0, start_ms: 0, end_ms: 1, text: "x" }], notes });
  assert.deepEqual(s.lines.map((l) => l.text), ["x"]);
  assert.equal(s.notes.seq, 2);
  assert.equal(s.state, "listening");
});

test("status lines are per part and an error does not touch the others", () => {
  let s = fresh();
  s = L.apply(s, { type: "live_status", part: "notes", level: "error", message: "LM Studio down" });
  s = L.apply(s, { type: "live_state", state: "listening", message: "" });
  assert.equal(s.status.notes.level, "error");
  assert.equal(s.status.notes.message, "LM Studio down");
  assert.equal(s.state, "listening");
});

test("items carry an unverified marker; verified ones do not", () => {
  assert.deepEqual(L.itemView({ text: "Στείλε", quote: "q", verified: false, owner: "Νίκος" }), { text: "Στείλε", owner: "Νίκος", unverified: true });
  assert.deepEqual(L.itemView({ name: "gdpr", quote: "q", verified: true }), { text: "gdpr", owner: null, unverified: false });
  assert.equal(L.itemView({ text: "x", quote: "q" }).unverified, true);       // no flag = not proven
});

test("updated ago", () => {
  assert.equal(L.agoText(65), "updated 01:05 ago");
  assert.equal(L.agoText(3), "updated 00:03 ago");
  assert.equal(L.agoText(null), "no update yet");
});

test("state text names the waiting part", () => {
  assert.match(L.stateText({ state: "waiting_gpu", message: "waiting for the running job step" }), /waiting for the running job step/);
  assert.match(L.stateText({ state: "listening", message: "" }), /listening/i);
});

class FakeEl {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ""; this.className = ""; this.hidden = false; }
  append(...c) { this.children.push(...c); }
  replaceChildren(...c) { this.children = c; }
}
const fakeDoc = () => { const made = []; return { made, createElement(t) { const e = new FakeEl(t); made.push(e); return e; } }; };

test("render sets text only: markup in a line stays text and creates no element", () => {
  const doc = fakeDoc();
  const evil = "<img src=x onerror=alert(1)>";
  const s = L.apply(fresh(), { type: "live_commit", idx: 0, start_ms: 0, end_ms: 1, text: evil });
  const root = { lines: new FakeEl("div"), partial: new FakeEl("div"), status: new FakeEl("div"), notes: new FakeEl("ul"), actions: new FakeEl("ul"), tags: new FakeEl("ul"), updated: new FakeEl("div") };
  L.render(doc, root, s, Date.now());
  assert.equal(root.lines.children[0].textContent, evil);
  assert.ok(doc.made.every((e) => ["div", "p", "li", "span", "ul"].includes(e.tag)), doc.made.map((e) => e.tag).join());
});

test("render marks unverified items with the word unverified", () => {
  const doc = fakeDoc();
  let s = L.apply(fresh(), { type: "live_notes", seq: 1, at_ms: 1000, notes: [{ text: "a", quote: "q", verified: false }], action_items: [], tags: [], verified: 0, unverified: 1 });
  const root = { lines: new FakeEl("div"), partial: new FakeEl("div"), status: new FakeEl("div"), notes: new FakeEl("ul"), actions: new FakeEl("ul"), tags: new FakeEl("ul"), updated: new FakeEl("div") };
  L.render(doc, root, s, Date.now());
  const li = root.notes.children[0];
  assert.ok(JSON.stringify(li).includes("unverified"));
});
