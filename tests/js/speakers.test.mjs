import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { fmtPct, fmtTalk, barRows, groupTurns, turnText, chipText, colorClass, timelineBlocks, unavailableText,
  nameToSend, emptyText } = createRequire(import.meta.url)("../../web/analyse/speakers.js");

const SP = [{ speaker: 1, display: "Speaker 1", talk_ms: 7000, share_pct: 70.0, turns: 2 },
  { speaker: 2, display: "Νίκος", talk_ms: 3000, share_pct: 30.0, turns: 1 }];

test("percent is shown as a whole number, time as mm:ss", () => {
  assert.equal(fmtPct(70.0), "70%");
  assert.equal(fmtPct(33.3), "33%");
  assert.equal(fmtPct(0.4), "0%");
  assert.equal(fmtPct(null), "-");
  assert.equal(fmtTalk(7000), "00:07");
  assert.equal(fmtTalk(3725000), "1:02:05");
});

test("each bar row carries the name, the percent and the talk time as text, plus a colour class", () => {
  const rows = barRows(SP);
  assert.deepEqual(rows.map((r) => [r.name, r.pct, r.time, r.width]), [["Speaker 1", "70%", "00:07", 70], ["Νίκος", "30%", "00:03", 30]]);
  assert.deepEqual(rows.map((r) => r.cls), ["spk-c1", "spk-c2"]);
});

test("bar width stays inside 0..100", () => {
  assert.equal(barRows([{ speaker: 1, display: "a", talk_ms: 1, share_pct: 140, turns: 1 }])[0].width, 100);
  assert.equal(barRows([{ speaker: 1, display: "a", talk_ms: 1, share_pct: -5, turns: 1 }])[0].width, 0);
});

test("colours cycle over six and unknown has its own class", () => {
  assert.equal(colorClass(1), "spk-c1");
  assert.equal(colorClass(6), "spk-c6");
  assert.equal(colorClass(7), "spk-c1");
  assert.equal(colorClass(null), "spk-c0");
});

const S = (a, b, speaker, name, text) => ({ start_ms: a, end_ms: b, speaker, speaker_name: name, text });

test("consecutive segments of one speaker group into one turn line when the gap is small", () => {
  const segs = [S(0, 1000, 1, "Speaker 1", "a"), S(1500, 2500, 1, "Speaker 1", "b"), S(2600, 3000, 2, "Νίκος", "c"),
    S(6000, 7000, 2, "Νίκος", "d")];
  const g = groupTurns(segs, 1500);
  assert.deepEqual(g.map((x) => [x.start_ms, x.name, x.text]), [[0, "Speaker 1", "a b"], [2600, "Νίκος", "c"], [6000, "Νίκος", "d"]]);
  assert.equal(groupTurns(segs, null).length, 2);
});

test("a turn line reads [mm:ss] Name text", () => {
  assert.equal(turnText({ start_ms: 65000, name: "Νίκος", text: "Γεια" }), "[01:05] Νίκος Γεια");
});

test("the transcript chip shows the display name, Unknown when there is none", () => {
  assert.equal(chipText({ speaker: 2, speaker_name: "Νίκος" }), "Νίκος");
  assert.equal(chipText({ speaker: null, speaker_name: null }), "Unknown");
  assert.equal(chipText({ speaker: null }), "Unknown");
});

test("timeline blocks are percentages of the recording", () => {
  const b = timelineBlocks([{ start_ms: 0, end_ms: 25000, speaker: 1 }, { start_ms: 25000, end_ms: 100000, speaker: 2 }], 100000);
  assert.deepEqual(b.map((x) => [x.left, x.width, x.cls, x.start_ms]), [[0, 25, "spk-c1", 0], [25, 75, "spk-c2", 25000]]);
  assert.deepEqual(timelineBlocks([], 0), []);
});

test("the disabled-state message carries the reason and the hint", () => {
  const m = { label: "Speaker model", installed: false, unavailable_reason: "pyannote.audio is not installed", hint: "run `make setup-diar`" };
  assert.equal(unavailableText(m), "Speaker model is not available: pyannote.audio is not installed (run `make setup-diar`)");
  assert.equal(unavailableText({ ...m, hint: undefined }), "Speaker model is not available: pyannote.audio is not installed");
  assert.equal(unavailableText(undefined), "No speaker model is configured.");
  assert.equal(unavailableText({ label: "X", installed: true }), "");
});

test("an empty name means reset", () => {
  assert.equal(nameToSend("  Ελένη "), "Ελένη");
  assert.equal(nameToSend("   "), null);
  assert.equal(nameToSend(""), null);
});

test("the empty-card text says why there are no bars", () => {
  assert.equal(emptyText({ available: false }), "The speaker model is unavailable.");
  assert.equal(emptyText({ available: true, run: { status: "done", speakers: [] } }), "No speech was found in this recording.");
  assert.equal(emptyText({ available: true, run: null }), "No speaker run yet. Press Detect speakers.");
  assert.equal(emptyText({ available: true, run: { status: "done", speakers: SP } }), "");
  assert.equal(emptyText({ available: true, run: null, running: true }), "Speakers are being detected.");
});
