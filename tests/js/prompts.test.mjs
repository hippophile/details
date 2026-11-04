import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const P = createRequire(import.meta.url)("../../web/prompts/prompts.js");

test("version label marks the latest and counts runs", () => {
  assert.equal(P.versionLabel({ version: 2, runs: 3 }, 2), "v2 (latest) · 3 runs");
  assert.equal(P.versionLabel({ version: 1, runs: 1 }, 2), "v1 · 1 run");
  assert.equal(P.versionLabel({ version: 1, runs: 0 }, 2), "v1 · 0 runs");
});

test("checks text uses plain names", () => {
  assert.equal(P.checksText(["due_year_in_transcript"]), "year check");
  assert.equal(P.checksText([]), "");
  assert.equal(P.checksText(["other_check"]), "other_check");
});

test("Save needs a changed, non-empty body and no save in flight", () => {
  assert.equal(P.canSave({ body: "x {{transcript}}", base: "{{transcript}}", busy: false }), true);
  assert.equal(P.canSave({ body: "{{transcript}}", base: "{{transcript}}", busy: false }), false);
  assert.equal(P.canSave({ body: "  \n", base: "{{transcript}}", busy: false }), false);
  assert.equal(P.canSave({ body: "x", base: "y", busy: true }), false);
});

test("save payload carries base_version and body, the note only when typed", () => {
  assert.deepEqual(P.savePayload(2, "b", ""), { base_version: 2, body: "b" });
  assert.deepEqual(P.savePayload(2, "b", "  "), { base_version: 2, body: "b" });
  assert.deepEqual(P.savePayload(2, "b", " stricter "), { base_version: 2, body: "b", note: "stricter" });
});

test("result text shows the new version, its file and the commit reminder", () => {
  const t = P.resultText({ kind: "checklist", version: 3, path: "prompts/checklist/v3.md" });
  assert.match(t, /checklist v3/);
  assert.match(t, /prompts\/checklist\/v3\.md/);
  assert.match(t, /commit/i);
});
