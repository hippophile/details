import test from "node:test";
import assert from "node:assert/strict";
import { tokenize, wordKey, diffText } from "../../web/analyse/diff.js";

const rebuild = (ops) => ({
  a: ops.flatMap((o) => (o.t === "eq" || o.t === "del" ? o.a : [])),
  b: ops.flatMap((o) => (o.t === "eq" || o.t === "ins" ? o.b : [])),
});
const flat = (ops) => ops.map((o) => [o.t, (o.t === "ins" ? o.b : o.a).join(" ")]);

// small deterministic PRNG so the synthetic tests are repeatable
function rng(seed) { let s = seed >>> 0; return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 2 ** 32); }

test("word key: lowercase, surrounding punctuation removed, tonos kept", () => {
  assert.equal(wordKey("Gemma,"), "gemma");
  assert.equal(wordKey("«Καλημέρα!»"), "καλημέρα");
  assert.notEqual(wordKey("πότε"), wordKey("ποτε"));
  assert.equal(wordKey("—"), "—");
  assert.equal(wordKey("n8n."), "n8n");
});

test("replace: eq, del, ins, eq", () => {
  const r = diffText("το Γκέμα και το n8n", "το Gemma και το n8n");
  assert.deepEqual(flat(r.ops), [["eq", "το"], ["del", "Γκέμα"], ["ins", "Gemma"], ["eq", "και το n8n"]]);
  assert.equal(r.coarse, false);
  assert.deepEqual(r.stats, { delA: 1, insB: 1, nA: 5, nB: 5 });
});

test("insert and delete", () => {
  assert.deepEqual(flat(diffText("a b c", "a b x c").ops), [["eq", "a b"], ["ins", "x"], ["eq", "c"]]);
  assert.deepEqual(flat(diffText("a b x c", "a b c").ops), [["eq", "a b"], ["del", "x"], ["eq", "c"]]);
  assert.deepEqual(flat(diffText("", "a b").ops), [["ins", "a b"]]);
  assert.deepEqual(flat(diffText("a b", "").ops), [["del", "a b"]]);
  assert.deepEqual(diffText("", "").ops, []);
});

test("case and punctuation are not differences, but Greek accents are", () => {
  assert.deepEqual(flat(diffText("Hello, world.", "hello world").ops).map((x) => x[0]), ["eq"]);
  const r = diffText("πότε θα έρθει", "ποτε θα έρθει");
  assert.deepEqual(flat(r.ops), [["del", "πότε"], ["ins", "ποτε"], ["eq", "θα έρθει"]]);
});

test("ops rebuild both word sequences exactly (including punctuation and case of each side)", () => {
  const A = "Καλημέρα, ξεκινάμε το meeting. Θα κάνουμε deploy στις 12 Νοεμβρίου!";
  const B = "καλημέρα ξεκινάμε το Meeting Θα κάνουμε deploy 12 Νοεμβρίου και ΤΕΛΟΣ";
  const r = diffText(A, B);
  assert.deepEqual(rebuild(r.ops), { a: tokenize(A), b: tokenize(B) });
});

test("repeated words (no unique anchors) still diff correctly", () => {
  const r = diffText("a a a b a a", "a a b a a a");
  assert.deepEqual(rebuild(r.ops), { a: tokenize("a a a b a a"), b: tokenize("a a b a a a") });
  assert.ok(r.stats.delA <= 1 && r.stats.insB <= 1);
});

test("synthetic one-hour pair: 10k words, 5% edits, under 10 s, >= 10 progress batches, exact rebuild", () => {
  const rand = rng(7), vocab = Array.from({ length: 3000 }, (_, i) => "w" + i);
  const A = Array.from({ length: 10000 }, () => vocab[Math.floor(rand() * vocab.length)]);
  const B = [];
  for (const w of A) {
    const x = rand();
    if (x < 0.017) continue;                         // delete
    if (x < 0.034) { B.push(w, "ins" + Math.floor(rand() * 1e6)); continue; }   // insert
    if (x < 0.05) { B.push("sub" + Math.floor(rand() * 1e6)); continue; }       // replace
    B.push(w);
  }
  const batches = [];
  const t0 = performance.now();
  const r = diffText(A.join(" "), B.join(" "), { onBatch: (b) => batches.push(b) });
  const ms = performance.now() - t0;
  console.log(`# 10k-word diff: ${ms.toFixed(0)} ms, ${batches.length} batches, delA=${r.stats.delA} insB=${r.stats.insB}`);
  assert.ok(ms < 10000, `took ${ms} ms`);
  assert.ok(batches.length >= 10, `only ${batches.length} batches`);
  assert.equal(batches.at(-1).progress, 1);
  assert.ok(batches.every((b, i) => i === 0 || b.progress >= batches[i - 1].progress));
  assert.deepEqual(rebuild(r.ops), { a: A, b: B });
  assert.deepEqual(rebuild(batches.flatMap((b) => b.ops)), { a: A, b: B });   // the streamed batches carry everything
  assert.equal(r.coarse, false);
  assert.ok(r.stats.delA > 200 && r.stats.delA < 1500, "5% edits should show up as roughly 5% differing words");
});

test("huge region is split by proportion (maxRegionWords) and still rebuilds", () => {
  const rand = rng(3), vocab = Array.from({ length: 40 }, (_, i) => "v" + i);   // all words repeat: no anchors
  const A = Array.from({ length: 3000 }, () => vocab[Math.floor(rand() * 40)]);
  const B = A.map((w) => (rand() < 0.05 ? "z" + Math.floor(rand() * 9) : w));
  const t0 = performance.now();
  const r = diffText(A.join(" "), B.join(" "), { maxRegionWords: 500 });
  assert.ok(performance.now() - t0 < 10000);
  assert.deepEqual(rebuild(r.ops), { a: A, b: B });
});

test("no shared anchors above the cell cap: finishes, flagged coarse, still rebuilds", () => {
  const rand = rng(11), vocab = Array.from({ length: 30 }, (_, i) => "c" + i);
  const A = Array.from({ length: 2000 }, () => vocab[Math.floor(rand() * 30)]);
  const B = Array.from({ length: 2000 }, () => vocab[Math.floor(rand() * 30)]);
  const t0 = performance.now();
  const r = diffText(A.join(" "), B.join(" "), { maxRegionWords: 5000, cellCap: 100000 });
  assert.ok(performance.now() - t0 < 5000);
  assert.equal(r.coarse, true);
  assert.deepEqual(rebuild(r.ops), { a: A, b: B });
});

test("texts with no common words at all are exact delete+insert, not coarse", () => {
  const A = Array.from({ length: 3000 }, (_, i) => "a" + (i % 7)), B = Array.from({ length: 3000 }, (_, i) => "b" + (i % 7));
  const r = diffText(A.join(" "), B.join(" "), { maxRegionWords: 5000 });
  assert.equal(r.coarse, false);
  assert.deepEqual(flat(r.ops).map((x) => x[0]), ["del", "ins"]);
  assert.deepEqual(rebuild(r.ops), { a: A, b: B });
});
