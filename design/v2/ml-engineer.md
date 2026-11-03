# detAIls v2 — ML engineer design (speech + LLM)

Evidence tags: **[verified]** = I ran/read it this session. **[cited]** = from a source URL. **[estimate, not measured]**. Nothing here was run on STT/diarization candidates; only LM Studio was tested live.

## 1. TLDR
- **STT: ship a model registry (config, not code). Add in this order:** (1) myned-ai Parakeet-Greek fp32 ONNX, (2) OpenCouncil LoRA merged into large-v3, (3) sam8000 turbo Greek. Skip Canary on Mac.
- **Who talked:** pyannote **community-1** run as an offline batch step AFTER STT, then align by time overlap. Needs a free HF token once (gated), then runs offline. FluidAudio = backup (Swift, no Python).
- **LLM:** one `/v1/chat/completions` client, `response_format: json_schema` **[verified works on Gemma E4B, live]**. Map-reduce over ~8k-token chunks. Every item must carry a transcript quote (hallucination check).
- **"Cheap vs nice" gets measured** with a hand-made key (action-item recall/precision, quote-validity %), plus the existing `eval/score.py` for STT.
- **Biggest risk:** all Greek numbers for new models are self-reported by authors, none on M1 Pro. First slice must be "run candidates on the user's own recording and score".

## 2. STT candidates

Baseline today (measured, BRIEF): large-v3 18.8% WER / 81% tech terms / 9.7 s per 77 s; turbo 26.7% / 62% / 4.2 s.

| # | Model | How it runs on M1 Pro | Mac feasibility | Greek WER (source) | Speed / RAM |
|---|---|---|---|---|---|
| 0 | Whisper large-v3 / turbo | `mlx-whisper`, already working | Done | FLEURS 11.0 (large-v3) [cited](https://arxiv.org/html/2406.15284v1); turbo 13.9 on M5 Mac [cited](https://whispernotes.app/blog/qwen3-asr-vs-whisper) | measured above; ~3.5 GB / 1.8 GB |
| 1 | **myned-ai/parakeet-tdt-0.6b-v3-greek** | `pip install onnx-asr onnxruntime`, load **fp32** ONNX (2.5 GB) from a local folder (HF-cache symlinks fail for 2.5 GB weights). CPU provider. | Good. No MLX/CoreML build exists. Output is NeMo-style, no ITN, writes `?` not `;` | CV-el **8.9** vs large-v3 16.9; FLEURS 11.4 vs 11.9; Cypriot 25.9. Self-reported, 150 clips each [cited](https://huggingface.co/myned-ai/parakeet-tdt-0.6b-v3-greek). Card: Whisper stays better on names/foreign words | Card: 12-15x realtime on Intel i7 CPU. M1 Pro: **estimate, not measured** ~10-20x. RAM ~3-4 GB (estimate) |
| 2 | **OpenCouncil LoRA** (`opencouncil/whisper-large-v3-el-council-lora`, Apache-2.0) | PEFT adapter. Merge once: `peft` `merge_and_unload()` into `openai/whisper-large-v3` -> save HF safetensors -> `mlx_examples/whisper/convert.py` -> MLX dir. One-time offline script. | Probable, **unverified** (LoRA targets q,v only so merge is plain; conversion path is the standard mlx-whisper one) | Held-out council meetings **17.95 vs 19.88** stock [cited](https://huggingface.co/opencouncil/whisper-large-v3-el-council-lora). Only ~2 points gain | Same as large-v3 (9.7 s / 77 s, 3.5 GB) |
| 3 | **sam8000/whisper-large-v3-turbo-greek-greece** (MIT) | Same merge-free route: HF safetensors -> `convert.py` -> MLX. (ggml/CoreML third-party builds exist but are unverified; do not use.) | Probable, unverified | FLEURS 11.06 normalized, trained AND scored on FLEURS, so may not carry to meetings [cited](https://huggingface.co/sam8000/whisper-large-v3-turbo-greek-greece) | ~turbo: 4.2 s / 77 s, 1.8 GB |
| 4 | NVIDIA Canary-1B-v2 | NeMo `nemo_toolkit[asr]` + PyTorch. Card lists NVIDIA GPUs, Linux preferred; Apple not mentioned [cited](https://huggingface.co/nvidia/canary-1b-v2). 6 GB RAM min | **Not realistic** for a simple local app (heavy dep, CPU-only/MPS untested; WhisperLiveKit runs it CPU-only on Apple Silicon per research note) | 9.21 FLEURS in NVIDIA table (inflated: same table has Whisper at 27%). Fine-tuned Canary behind Whisper on meetings (18.3 vs 15.7) [cited](https://arxiv.org/html/2609.13498) | unknown |

**Rank to add first:**
1. **Parakeet-Greek fp32.** Different architecture = real diversity in the comparison, fast, cheap deps (`onnx-asr`), and the only candidate with a big claimed gain on conversational Greek. Weak spot to test: English tech words.
2. **OpenCouncil LoRA.** Closest to "Greek meetings", but gain is small and needs a one-time convert script. Do after (1).
3. **sam8000 turbo.** Cheap to add once the convert script from (2) exists; test whether it beats stock turbo's 26.7%.
4. Canary: only if the user later gets a GPU box. Not on this Mac.

**Interface (illustrative):** each model = one entry in `models.json` (path from env `DETAILS_MODELS_JSON`):
```json
{"id":"parakeet-el-fp32","engine":"onnx-asr","path":"${DETAILS_MODEL_DIR}/parakeet-el","lang":"el","ram_gb":4}
{"id":"large-v3","engine":"mlx-whisper","repo":"mlx-community/whisper-large-v3-mlx"}
```
Engines return the same shape: `[{start,end,text}]`. Parakeet gives token/word timestamps; Whisper gives segments.

**Post-processing for all STT (cheap, deterministic):** `?` -> `;` for Greek question marks (Parakeet card); Whisper `initial_prompt` with glossary (<=224 tokens ~ 68 Greek words) [cited](https://developers.openai.com/api/docs/guides/speech-to-text), paired with `condition_on_previous_text=False` and the OpenCouncil decode config (beam 5, temp ladder, no_speech 0.6, compression 2.4). No Greek evidence that the prompt helps: the existing `turbo+prompt` scorer mode will tell us.

**Memory rule:** run STT, unload, then run diarization, unload, then LLM. Never two loaded at once with Gemma 26B (21.6 GB system).

## 3. Diarization ("who talked")

**Recommendation: pyannote community-1, offline, after STT.** Not live (live labels from WhisperLiveKit/Sortformer are a later extra, max 4 speakers).

| Option | Install | Mac | Quality | Verdict |
|---|---|---|---|---|
| **pyannote community-1** | `pip install pyannote.audio` (pulls PyTorch) | CPU default; MPS not mentioned on card | DER: AMI-IHM 17.0, AMI-SDM 19.9, DIHARD3 20.2, VoxConverse 11.2 [cited](https://huggingface.co/pyannote/speaker-diarization-community-1). Beats 3.1 on all | **Pick.** CC-BY-4.0. **Gated:** accept terms + free HF token once; then clone/cache and load from a local path, works offline. Token stays in env var, never in config |
| FluidAudio (Swift) | `swift run fluidaudiocli process meeting.wav --mode offline --output r.json` | macOS only, CoreML/ANE, Apache-2.0. Python: none official [cited](https://github.com/FluidInference/FluidAudio) | Offline mode uses the same pyannote community-1 pipeline, so quality ~equal | **Backup/speed path**: call CLI via `subprocess`, parse JSON. Needs Swift 6 toolchain build. No DER/RTFx published on README |
| sherpa-onnx diarization | `pip install sherpa-onnx` + pyannote-segmentation-3.0 ONNX + speaker-embedding ONNX | Works on CPU, no PyTorch | Same segmentation family; clustering simpler. **Unverified this session** | Good "no PyTorch" fallback. Not first |
| WhisperX | PyTorch + faster-whisper (CTranslate2, no Metal) + pyannote | Slow on Mac, wraps pyannote anyway | n/a | **Reject**: duplicates our STT, adds a CPU-only Whisper |

DER ~17-20% on meeting corpora means roughly 1 in 5-6 seconds mislabeled. For 2-3 speakers, **expect good for clear turn-taking, poor in overlap and short interjections** (estimate, not measured). Greek vs English language does not matter to diarization (acoustic).
RAM/speed: **estimate, not measured**: ~1-2 GB RAM; minutes, not hours, for 2.5 h on M1 CPU (pyannote community-1 reports no speed on card). Test it.

**Aligning speakers with transcript (algorithm, ~30 lines):**
1. Use `output.exclusive_speaker_diarization` (no overlapping turns; the card says it exists to ease alignment with transcripts).
2. For each STT segment [s,e]: speaker = the diarization label with max time overlap. If a segment spans a speaker change and word timestamps exist, split at the change point.
3. Merge adjacent same-speaker segments (gap < 1.5 s). Output `Speaker 1/2/3`; the user renames them in the UI (stored as a map in the analysis JSON).
4. Segments with no overlap -> `Unknown`, never guessed.
Transcript sent to the LLM looks like `[00:12:31] Speaker 2: text`.

**Single-mic caveat:** the mic+system audio recording is mixed to mono for diarization. If the recorder (other role) can save mic and system audio as separate channels, "me vs remote" is free and exact; then run pyannote only on the remote channel. Raise with the audio role.

## 4. LLM post-processing (LM Studio)

**Verified live this session** (`localhost:1234`, `google/gemma-4-e4b`): `response_format:{type:"json_schema",...}` with `reasoning_effort:"none"` returned valid schema-conforming JSON with a quote field, Greek in, Greek out. LM Studio docs: structured output works via grammar sampling (GGUF) / Outlines (MLX); warns models under ~7B may be unreliable [cited](https://lmstudio.ai/docs/developer/openai-compat/structured-output). So E4B is the one to stress-test. Models loaded now: `google/gemma-4-e4b`, `gemma-4-12b-it-mlx`, `gemma-4-26b-a4b-it-qat-mlx`, `qwen/qwen3.6-35b-a3b`. Not verified: schema behaviour on MLX 26B and on Qwen with thinking ON.

### 4.1 Outputs (one prompt+schema per product, not one mega-prompt)
| Product | Output | Schema notes |
|---|---|---|
| Debrief | JSON: `summary` (5-8 lines), `decisions[]`, `open_questions[]`, `risks[]` | each with `quote` |
| Notes | Markdown by topic with timestamps | built from `topics[]` JSON, rendered by code |
| Action items | `{task, owner|null, due|null, quote, ts}` | owner/due **null if not said**; never infer |
| Checklist | `[{text, done:false, source_ts}]` | derived from action items + decisions |
| Tags | 3-8 from a fixed vocabulary in config + free `other[]` | fixed vocab keeps tags comparable across recordings |
| Diagram | JSON `{type:"flowchart|sequence", nodes, edges}`; **code builds the Mermaid string** | LLM-written Mermaid often has syntax errors (labels with quotes/parens). Validate by parsing; on failure, fall back to a text list |
Rule: the LLM returns data, our code renders Markdown/Mermaid. This keeps outputs diffable and testable.

### 4.2 Prompt strategy
- Prompts written in **English** with explicit "Output language: same as transcript segment (Greek). Keep English terms in English." (Estimate, not measured: English instructions are more reliable on small models; test vs Greek instructions in the rubric.)
- Always inject: a glossary (product names), speaker map, recording date (so "μέχρι Παρασκευή" resolves to a date only if the date is given; else keep the literal phrase).
- Hard rules in system prompt: "Use only the transcript. Each item MUST include `quote` copied verbatim from the transcript. If unsure, omit."
- Temperature 0-0.2. `max_tokens` set per product.

### 4.3 Transcript cleanup with glossary, without rewriting
- Do **not** ask the LLM to return the full text. Ask for **edits only**: `[{"find":"Γκέμα","replace":"Gemma","ts":"00:03:12"}]`, `find` must occur verbatim in the segment.
- Code applies edits, then shows a **diff** (before/after, per segment) to the user; stores raw and cleaned transcript separately. Reject any edit where `find` is not found, or where edit distance > glossary-term length x 2 (blocks rewriting).
- Cheaper first pass with no LLM: fuzzy-match glossary terms (Levenshtein on Latin transliteration) and propose edits. LLM only for what fuzzy misses. (Design choice, not measured.)
- Safety: the raw transcript is the source of truth for the quote check.

### 4.4 Long transcripts (2.5 h ~ 70k tokens, estimate, not measured; Greek tokenizes poorly so could be higher, count with the model tokenizer/`usage`)
Map-reduce, 32k context:
1. **Chunk** by time at speaker-turn boundaries: ~6-8k tokens each + 500 tokens overlap (~10 chunks). Leaves room for prompt (~1.5k) + output (~2k).
2. **Map** (per chunk, JSON): `topics`, `decisions`, `action_items(+quote,ts)`, `open_questions`, `names_mentioned`. 
3. **Reduce** (text is now small, ~5-8k): merge, **dedupe** action items (same owner+similar task; overlap zone duplicates), write debrief, tags, diagram. Reduce works on the map JSON, not raw text; quotes are carried through.
4. **Verify** (code): each `quote` must be a substring of the transcript after normalization (same `norm()` as `eval/score.py`) or fuzzy ratio >= 0.9. Failing items go to a "unverified" bucket, shown greyed out, not deleted.
5. Cost (estimate, not measured): 10 map calls x ~8k tokens prompt. Gemma 26B at 41 tok/s output; prompt processing speed unknown, so measure. Plan on minutes to tens of minutes for 2.5 h. Progress per chunk in UI; results cached per chunk (idempotent resume).
Short recordings (< ~20k tokens): single pass, no map-reduce.

### 4.5 Thinking on/off
- Default **off** (`reasoning_effort:"none"`, [verified accepted]) for map step and cleanup: extraction, not reasoning; thinking burns tokens and time for no gain on quote-extraction (estimate, not measured).
- **On** only for reduce/dedupe and diagrams on Qwen3.6 / Gemma 26B, if the rubric shows gain. Make it a per-run option ("thinking: off/on") so the comparison measures it.
- Risk: with thinking on, check that `reasoning_content` does not break JSON parsing: always parse `message.content` only. Not tested on Qwen.

### 4.6 Model roles (to be settled by measurement)
| Model | Expected role | Caveat |
|---|---|---|
| Gemma E4B (32 tok/s) | "cheap": tags, short debrief, cleanup edits | small-model structured output; hallucinated owners likely (estimate) |
| Gemma 26B QAT (41 tok/s, ~21.6 GB system) | "nice" default | forces other apps/STT unloaded |
| Qwen3.6-35B-A3B (49 tok/s, swaps) | optional | swap = slow + risk to the user's other work; run only when nothing else open |
| Gemma 12B | middle point | cheap to include |
Model list = LM Studio `GET /v1/models` filtered by config, not hardcoded.

## 5. Evaluation (so "cheap vs nice" is a number)

### 5.1 STT (extend existing `eval/score.py`; it already does WER with jiwer, tonos-stripped norm, tech-term recall, numbers, fillers)
- Add engines to its model list from `models.json` (small change slice).
- Metrics per model per recording: WER, tech-term recall, number recall, RTF (audio s / wall s), peak RAM, plus **Greek-specific: final-sigma and `;` handling** (norm already maps ς->σ, so WER ignores it; report separately because it matters to readers, estimate).
- Keep the scripted meeting as a *regression set* (reference known, free). Add one **real** 10-20 min take hand-corrected once (research notes' own conclusion). Public sets (FLEURS) are only a sanity check.
- Report a 3-run table; one recording is anecdote: say so in the UI.

### 5.2 Diarization
- Hand-label 5 min of a 2-3 voice take (speaker, start, end), compute DER with `pyannote.metrics` (or simple frame accuracy, simpler). The scripted meeting (3 voices by one person) is a poor test: **one real voice cannot test diarization**. Need 2+ real people for 5 min. Open question Q4.

### 5.3 LLM rubric (per model x thinking on/off, same transcript, same prompt)
| Metric | How | Pass bar (proposal) |
|---|---|---|
| Action-item recall | hand key `key.json` (~10-15 items for the scripted meeting: `task, owner, due`); match by LLM-free fuzzy + human tick | track, compare |
| Action-item precision | % returned items that match key or are judged valid by human | track |
| Owner / due accuracy | exact on matched items; **null is correct when not said** | track |
| Quote validity (hallucination) | % of items whose `quote` is a verbatim substring (code) | >= 95%; any invented item = fail flag |
| Support check | for valid quotes, human spot-check 10 items: does the quote really support the item? | manual, 10 min |
| Schema validity | % calls returning valid JSON on first try | 100% |
| Language | Greek stays Greek, English terms untouched (code check: glossary terms preserved) | pass/fail |
| Cost | wall time, tokens, tok/s from `/api/v0` stats, peak RAM | numbers |
| Edit safety (cleanup) | % edits where `find` exists; number of edits touching non-glossary words | 0 unsafe |
Scripted meeting has a known script, so a key is nearly free: the script `eval/script_meeting_el_en.md` already lists the decisions/tasks. **Run LLM tests on the reference script text first** (isolates LLM from STT errors), then on each STT output (end-to-end). Final table: STT x LLM grid is too big; do best-STT x all LLMs, and all-STT x one LLM.
Avoid LLM-as-judge from the same family (bias); the rubric above is mostly code + human.

## 6. Rejected
- Canary on Mac (NVIDIA-first stack, no Apple support stated). WhisperX (duplicate engine). Third-party ggml/CoreML Greek builds (no WER, no hashes). Parakeet int8 (fp32 11.4 -> int8 18.7 FLEURS per card). Voxtral (Greek unconfirmed locally). LLM writes full Mermaid/Markdown directly (fragile). LLM rewrites the whole transcript (silent rewriting). Cloud STT/LLM (hard constraint).
- Live diarization in v2 first cut (4-speaker cap, extra moving part).

## 7. Risks that change decisions
1. **Self-reported WER, tiny test sets, none on code-switching or M1 Pro.** Parakeet-Greek may lose on English terms; decide only after scoring on the user's audio.
2. **pyannote needs a one-time gated HF token.** Without it: sherpa-onnx or FluidAudio fallback. Warn: don't screenshot the token.
3. **Memory:** sequential pipeline mandatory. Qwen 35B swaps.
4. **Gemma E4B structured output** below the ~7B guidance; the verify step is the safety net.
5. **70k-token estimate** may be low for Greek. Chunker must count real tokens.
6. LoRA/turbo MLX conversion is unverified; a failed conversion drops candidates 2-3 (Parakeet unaffected).
7. Diarization quality ~17-20% DER at best; UI must let the user rename/merge speakers and must mark them "auto".

## 8. Open questions (max 5)
1. OK to create a free Hugging Face account/token once for pyannote (gated model)?
2. Can the user record a 5-min test with a second real person (needed to test diarization)?
3. Can the recorder keep mic and system audio on separate channels? (makes "me vs them" exact)
4. Which hand-corrected real meeting (10-20 min) may be used as the private test set (never in git)?
5. Is a 10-30 min wait for a 2.5 h analysis on "nice" acceptable, or should long runs default to E4B?

## 9. OpenSpec change slices
1. `stt-model-registry` — `models.json` + engine interface (`[{start,end,text}]`), mlx-whisper adapter only.
2. `stt-parakeet-greek` — onnx-asr fp32 adapter + `?`->`;` postfix.
3. `stt-whisper-convert-script` — one-time merge+convert for OpenCouncil LoRA and sam8000 into MLX dirs.
4. `eval-stt-multi-engine` — score.py reads registry, adds RTF/RAM, per-engine table.
5. `diarize-pyannote` — offline batch step, exclusive output, local model path, token via env.
6. `transcript-speaker-align` — overlap alignment + speaker rename map (pure function, unit-tested first).
7. `llm-client-structured` — LM Studio client, json_schema, thinking flag, model list from `/v1/models`.
8. `llm-extract-actions-debrief` — schemas + prompts + quote verifier (the core).
9. `llm-longform-mapreduce` — token-aware chunking, per-chunk cache, reduce + dedupe.
10. `llm-glossary-cleanup` — edit-only prompt, fuzzy pre-pass, diff view, raw kept.
11. `llm-tags-checklist-diagram` — tag vocab, checklist, JSON->Mermaid with parse-validate.
12. `eval-llm-rubric` — hand key for scripted meeting, scorer for recall/precision/quote validity, comparison table.
13. `diarize-fluidaudio-fallback` (optional) — CLI wrapper.
