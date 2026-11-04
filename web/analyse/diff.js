// Word diff for the compare view. Pure ES module (no DOM): used by diff-worker.js and by `node --test`.
// Anchors on words that occur exactly once in both texts (patience style, longest increasing subsequence), splits the
// regions between anchors by proportion when they are larger than maxRegionWords, and runs an LCS per region with a
// cell cap (a region over the cap becomes one delete + one insert block and the result is flagged `coarse`).
// Ops are runs: {t:"eq", a:[words], b:[words]} | {t:"del", a:[words]} | {t:"ins", b:[words]}.

export const DEFAULTS = { maxRegionWords: 1500, cellCap: 4000000, batchWords: 300 };
const EDGE = /^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu;

export function tokenize(text) { return String(text || "").split(/\s+/).filter(Boolean); }

// lowercase, surrounding punctuation removed, tonos kept (an accent change is a real difference)
export function wordKey(w) { return w.toLowerCase().replace(EDGE, "") || w; }

function anchors(ka, kb, lo1, hi1, lo2, hi2) {
  const countA = new Map(), posB = new Map(), countB = new Map();
  for (let i = lo1; i < hi1; i++) countA.set(ka[i], (countA.get(ka[i]) || 0) + 1);
  for (let j = lo2; j < hi2; j++) { countB.set(kb[j], (countB.get(kb[j]) || 0) + 1); posB.set(kb[j], j); }
  const pairs = [];                                   // (i, j) ascending in i
  for (let i = lo1; i < hi1; i++) if (countA.get(ka[i]) === 1 && countB.get(ka[i]) === 1) pairs.push([i, posB.get(ka[i])]);
  // longest increasing subsequence on j
  const tails = [], prev = new Array(pairs.length).fill(-1);
  for (let p = 0; p < pairs.length; p++) {
    let lo = 0, hi = tails.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (pairs[tails[mid]][1] < pairs[p][1]) lo = mid + 1; else hi = mid; }
    if (lo > 0) prev[p] = tails[lo - 1];
    tails[lo] = p;
  }
  const out = [];
  for (let p = tails.length ? tails[tails.length - 1] : -1; p >= 0; p = prev[p]) out.push(pairs[p]);
  return out.reverse();
}

// LCS table on suffixes, then a forward walk (deletions win ties so a replace reads del, ins).
function lcsOps(A, B, ka, kb, i0, i1, j0, j1, push) {
  const n = i1 - i0, m = j1 - j0, w = m + 1;
  const dp = new (Math.max(n, m) < 65535 ? Uint16Array : Uint32Array)((n + 1) * w);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * w + j] = ka[i0 + i] === kb[j0 + j] ? dp[(i + 1) * w + j + 1] + 1
        : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1]);
    }
  }
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (ka[i0 + i] === kb[j0 + j]) { push("eq", A[i0 + i], B[j0 + j]); i++; j++; }
    else if (dp[(i + 1) * w + j] >= dp[i * w + j + 1]) { push("del", A[i0 + i]); i++; }
    else { push("ins", undefined, B[j0 + j]); j++; }
  }
  while (i < n) { push("del", A[i0 + i]); i++; }
  while (j < m) { push("ins", undefined, B[j0 + j]); j++; }
}

export function diffText(textA, textB, options = {}) {
  const { maxRegionWords, cellCap, batchWords } = { ...DEFAULTS, ...options };
  const onBatch = options.onBatch;
  const A = tokenize(textA), B = tokenize(textB);
  const ka = A.map(wordKey), kb = B.map(wordKey);
  const ops = [], stats = { delA: 0, insB: 0, nA: A.length, nB: B.length };
  let coarse = false, batch = [], batchSize = 0, done = 0;
  const total = Math.max(1, A.length + B.length);

  // adjacent ops of the same type are merged into runs (also across regions, but never across a batch boundary)
  const push = (t, a, b) => {
    const last = batch[batch.length - 1];
    if (last && last.t === t) { if (a !== undefined) last.a.push(a); if (b !== undefined) last.b.push(b); }
    else batch.push({ t, a: a === undefined ? [] : [a], b: b === undefined ? [] : [b] });
    if (t === "del") stats.delA++; else if (t === "ins") stats.insB++;
    batchSize++;
  };
  const flush = (final) => {
    if (!batch.length && !final) return;
    const clean = batch.map((o) => (o.t === "eq" ? o : o.t === "del" ? { t: "del", a: o.a } : { t: "ins", b: o.b }));
    ops.push(...clean);
    if (onBatch) onBatch({ progress: final ? 1 : Math.min(0.999, done / total), ops: clean });
    batch = []; batchSize = 0;
  };
  const step = (n) => { done += n; if (batchSize >= batchWords) flush(false); };

  function block(i0, i1, j0, j1) {                      // one delete run then one insert run
    for (let i = i0; i < i1; i++) push("del", A[i]);
    for (let j = j0; j < j1; j++) push("ins", undefined, B[j]);
  }
  function chunk(i0, i1, j0, j1) {
    while (i0 < i1 && j0 < j1 && ka[i0] === kb[j0]) { push("eq", A[i0], B[j0]); i0++; j0++; }
    let t1 = i1, t2 = j1;
    while (t1 > i0 && t2 > j0 && ka[t1 - 1] === kb[t2 - 1]) { t1--; t2--; }
    if (i0 < t1 && j0 < t2) {
      const setB = new Set(kb.slice(j0, t2));
      let shared = false;
      for (let i = i0; i < t1 && !shared; i++) shared = setB.has(ka[i]);
      if (!shared) block(i0, t1, j0, t2);               // nothing in common: exact without a table
      else if ((t1 - i0) * (t2 - j0) > cellCap) { coarse = true; block(i0, t1, j0, t2); }
      else lcsOps(A, B, ka, kb, i0, t1, j0, t2, push);
    } else block(i0, t1, j0, t2);
    for (let i = t1, j = t2; i < i1; i++, j++) push("eq", A[i], B[j]);
  }
  function region(i0, i1, j0, j1) {
    const la = i1 - i0, lb = j1 - j0;
    const parts = Math.max(1, Math.ceil(Math.max(la, lb) / maxRegionWords));
    for (let p = 0; p < parts; p++) {                   // split by proportion; each part is diffed on its own
      const a0 = i0 + Math.floor((la * p) / parts), a1 = i0 + Math.floor((la * (p + 1)) / parts);
      const b0 = j0 + Math.floor((lb * p) / parts), b1 = j0 + Math.floor((lb * (p + 1)) / parts);
      chunk(a0, a1, b0, b1);
      step(a1 - a0 + b1 - b0);
    }
  }

  let pi = 0, pj = 0;
  for (const [i, j] of anchors(ka, kb, 0, A.length, 0, B.length)) {
    region(pi, i, pj, j);
    push("eq", A[i], B[j]); step(2);
    pi = i + 1; pj = j + 1;
  }
  region(pi, A.length, pj, B.length);
  flush(true);
  return { ops, coarse, stats };
}
