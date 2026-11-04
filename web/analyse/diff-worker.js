// Web Worker (module): receives {a, b, options}, posts {progress, ops} per batch, then {done, coarse, stats}.
import { diffText } from "/analyse/diff.js";

self.onmessage = (ev) => {
  const { a, b, options } = ev.data;
  try {
    const r = diffText(a, b, { ...options, onBatch: (batch) => self.postMessage({ progress: batch.progress, ops: batch.ops }) });
    self.postMessage({ done: true, coarse: r.coarse, stats: r.stats });
  } catch (e) {
    self.postMessage({ error: String(e && e.message || e) });
  }
};
