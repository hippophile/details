# Open-weight Greek ASR models: measured Greek accuracy (as of 2026-11-03)

> **Read this before using any number.** Greek WER changes a lot with **text normalization**: whether tonos and diacritics are stripped, whether casing and punctuation are removed, and whether numbers are written as digits or words. One example: Whisper-large-v3 on FLEURS Greek is **11.02** in one paper and **27.03** in another. Only compare numbers that come from the same table. CER (character error rate) is often 3–4x lower than WER for Greek, so never compare a CER with a WER.

## 1. Greek WER for OpenAI Whisper variants (large-v3, turbo, medium, small)

### Takeaway
Out of the box, Whisper-large-v3 gets about **11–14% WER on FLEURS/Common Voice Greek** under Greek-aware normalization. Turbo is close behind on clean read speech (about 12–14%) but falls off on real-world audio. Medium is about 19–21% and small about 33–34%. Distil-Whisper models are English-only, so they don't apply (gap, see below).

### Cited Findings
**Paper by the Athena RC Institute for Speech and Language Processing (ILSP), "Greek Podcast Corpus", arXiv 2406.15284, June 2024. Zero-shot Whisper, Table 3, WER %:**

| Model | FLEURS el | Common Voice el | Logotypografia | HParl (parliament) |
|---|---|---|---|---|
| whisper-small | 33.99 | 33.37 | 30.35 | 71.45 |
| whisper-medium | 19.45 | 21.30 | 17.03 | 39.27 |
| whisper-large-v2 | 12.95 | 16.03 | 12.63 | 27.09 |
| whisper-large-v3 | **11.02** | **13.98** | **10.86** | **16.99** |
— [Paraskevopoulos et al., arXiv 2406.15284](https://arxiv.org/html/2406.15284v1). The paper says it normalizes references and hypotheses but does not say how.

- NVIDIA's Canary/Parakeet paper (Sept 2025, Table 11, FLEURS) gives **whisper-large-v3 = 27.03% WER on Greek**. Its multilingual normalizer removes punctuation, lowercases, and maps or strips diacritics. This conflicts with the 11.02 above, and the cause is not explained. Possible causes are a different decoding setup or language-ID handling (my inference). — [Sekoyan et al., arXiv 2509.14128](https://arxiv.org/html/2509.14128v2)
- BuzzASR paper (Sept 2026), FLEURS + Common Voice v25 combined Greek test, greedy decoding: **Whisper (large-v3) CER 4.37 / WER 13.58**. The BuzzASR model card gives zero-shot large-v3 CER of 4.34 on FLEURS and 4.47 on CV25. — [arXiv 2609.09554](https://arxiv.org/html/2609.09554v2); [BuzzASR/greek card](https://huggingface.co/BuzzASR/greek)
- **Measured on a Mac.** whispernotes.app (Aug 2026, updated 2026-10-31) ran a FLEURS test of about 150 Greek sentences on an **M5 MacBook Air with 32 GB**. Results: **Whisper Large V3 Turbo (whisper.cpp ggml, Metal) = 13.94% WER** and Qwen3-ASR-1.7B (MLX 8-bit) = 30.22% WER. Turbo had no empty outputs or failures. — [whispernotes.app](https://whispernotes.app/blog/qwen3-asr-vs-whisper)
- KIEFER SA / Sophea AI Lab paper (Sept 2026), Whisper-based **Large vs Turbo** tiers, WER %. These may be their adapted builds rather than stock weights (see §5):
  - FLEURS Greek: 9.13 vs 11.53
  - Real-world Greek aggregate: 15.97 vs 20.65
  - Parliamentary: 20.81 vs 24.98
  - Community-sourced: 1.73 vs 21.61. That gap is suspiciously large and not explained.

  — [arXiv 2609.13498](https://arxiv.org/html/2609.13498)
- Greek medical dictation (Sept 2025), zero-shot WER: Whisper Small 43.62, Medium 34.71, Large-v2 26.41. — [Georgilas & Stafylakis, arXiv 2509.23550](https://arxiv.org/pdf/2509.23550)
- Cypriot Greek dialect: whisper-medium best WER 37.85, whisper-large-v3 33.93. — [ACL Anthology listing](https://aclanthology.org/people/spyros-armostis/unverified/)
- Tokenizer cost: Whisper-large-v3 needs **3.27 tokens per Greek word**, compared with 1.30 for a Greek-specific tokenizer. More tokens per word makes decoding slower and gives repetition loops more room. — [BuzzASR arXiv 2609.09554, Table 6](https://arxiv.org/html/2609.09554v2)

### Inferences
- Default choice for local use: **whisper-large-v3** gives the best zero-shot accuracy. **large-v3-turbo** is the speed option and is fine for clean single-speaker audio. Expect turbo to lose 2–5 WER points on meetings and other real-world audio.
- Sizes from the OpenAI model card, which I did not re-fetch this session: large-v3 is about 1.55B params (about 3 GB fp16) and turbo about 809M (about 1.6 GB). Both fit easily in 32 GB unified memory.

### Gaps
- I could not extract the per-language Greek FLEURS/CV9 table from the original Whisper paper (Appendix D, arXiv 2212.04356) because the PDF is compressed. Those numbers cover v2 and earlier only.
- Distil-Whisper: I found no distil variant with Greek support in my searches.
- OpenAI did not publish a per-language large-v3 Greek figure. The model card has only an aggregate chart.

## 2. Greek fine-tuned Whisper models (Hugging Face, Greek institutions)

### Takeaway
Fine-tuning helps a lot **in-domain**, but the gains on FLEURS/Common Voice are small or zero once you start from large-v3. The best open Greek fine-tune with real numbers is **BuzzASR/greek** (large-v3, MIT): 11.0 WER on FLEURS. The Athena RC/ILSP podcast models are on GitHub, not Hugging Face, and are small/medium only.

### Cited Findings
| Model | Base / size | License | Greek WER (test) | Source |
|---|---|---|---|---|
| **BuzzASR/greek** | whisper-large-v3, about 1.5B (card says "~2B", F16) | MIT | FLEURS **11.0** (CER 4.1); CV25 16.48 (CER 5.04); combined 15.91 | [HF card](https://huggingface.co/BuzzASR/greek), [arXiv 2609.09554](https://arxiv.org/html/2609.09554v2) |
| BuzzASR "SFTMTL" variant (paper) | — | — | combined FLEURS+CV25 **WER 10.17 / CER 3.21**, the best Greek row in the paper | [arXiv 2609.09554 Tables 7–8](https://arxiv.org/html/2609.09554v2) |
| Greek Podcast Corpus models (Athena RC / ILSP) | whisper-small / medium fine-tuned on 800 h of podcasts with silver labels | not stated | medium: FLEURS 14.03, CV 12.8, Logotypografia 11.84, HParl 30.65, GPC-50 9.95 | [arXiv 2406.15284](https://arxiv.org/html/2406.15284v1); checkpoints at github.com/georgepar/greek_podcasts_asr |
| Vardis/Whisper-Large-v2-Greek | large-v2 LoRA | see card | WER 12.06 / CER 6.20 (medical dictation domain) | [HF](https://huggingface.co/Vardis/Whisper-Large-v2-Greek) |
| Medical dictation paper | large-v2 fine-tuned | — | 26.41 → **14.90** (medical); medium 34.71 → 19.45 | [arXiv 2509.23550](https://arxiv.org/pdf/2509.23550) |
| farsipal/whisper-md-el-intlv-xs | whisper-medium, CV11 + FLEURS | see card | 11.37 (CV11 test) | [HF](https://huggingface.co/farsipal/whisper-md-el-intlv-xs/blob/56c1a48f8526d140dcaacffe612a0cc7db950cdc/README.md?code=true) |
| emilios/whisper-medium-el | whisper-medium | see card | 15.58 (FLEURS el_gr) | [HF](https://huggingface.co/emilios/whisper-medium-el/blob/c8796a607cfa44f3c0e762f0e7b6942f466a9e44/README.md) |
| KIEFER "Whisper FT" | Whisper fine-tune, possibly not public | — | FLEURS 5.23, TEDx 6.73, **business meetings 15.73**, noisy 24.99 | [arXiv 2609.13498](https://arxiv.org/html/2609.13498) |

- BuzzASR card inconsistency: it claims "~0.9x CER reduction over Whisper zero-shot on combined", but its combined CER (5.05) is **worse** than zero-shot large-v3 (4.37). On CV25 it is also worse (5.04 vs 4.47). — [HF card](https://huggingface.co/BuzzASR/greek)
- Recommended BuzzASR decode settings: greedy, no_repeat_ngram_size=3, repetition_penalty=1.2, 16 kHz mono input, Greek-only. — [HF card](https://huggingface.co/BuzzASR/greek)

### Inferences
- On FLEURS/CV, no public Greek fine-tune clearly beats stock large-v3. The 11.0 vs 11.02 "win" depends on normalization. Fine-tuning pays off only for **your** domain (meetings, medical). That points to a later fine-tune on the user's own meeting audio, not to swapping in a community model.
- HF fine-tunes of small/medium (11–16 WER on CV) are only worth trying if large-v3 is too slow.

### Gaps
- I found no official ILSP/Athena RC Greek Whisper on Hugging Face. Their checkpoints are linked from GitHub only, and I did not check the license.
- KIEFER's fine-tuned models: release status unknown. The paper describes a production system.

## 3. Newer multilingual ASR models: Greek support and Greek WER

### Takeaway
**NVIDIA Canary-1B-v2** (CC-BY-4.0, about 1B params) has the best published open-model Greek FLEURS number: **9.21%**, compared with 27.03 for large-v3 in the same table. Parakeet-v3 scores 20.7 in that table, and third-party runs put it at about 36. **Voxtral, Phi-4-multimodal, Kyutai and Granite do not support Greek.** Qwen3-ASR does support Greek but is weak at it: 28–50% WER. Meta Omnilingual 7B is competitive, at about 14.5 WER.

### Cited Findings
**Same-table comparison: NVIDIA paper Table 11, FLEURS Greek WER %, diacritics stripped, Sept 2025:**

| Model | Params | License | FLEURS el WER |
|---|---|---|---|
| **Canary-1B-v2** | ~1B | CC-BY-4.0 | **9.21** |
| Parakeet-TDT-0.6B-v3 | 0.6B | CC-BY-4.0 | 20.70 |
| seamless-m4t-v2-large | — | (Meta, non-commercial) | 25.68 |
| whisper-large-v3 | 1.55B | MIT | 27.03 |
| seamless-m4t-medium | — | — | 29.49 |
| Voxtral-Mini-3B-2507 | 3B | Apache-2.0 | – (no Greek) |
| Phi-4-multimodal-instruct | — | MIT | – (no Greek) |
— [arXiv 2509.14128 Table 11](https://arxiv.org/html/2509.14128v2). The 25 supported languages are bg, cs, da, de, **el**, en, es, et, fi, fr, hr, hu, it, lt, lv, mt, nl, pl, pt, ro, ru, sk, sl, sv, uk. The Greek benchmark is FLEURS only, with no CoVoST/MLS Greek.

- Parakeet-TDT-0.6B-v3 card: FLEURS el_gr 20.7 (greedy, no LM). Output has punctuation and capitalization, which the WER calculation strips. It detects language automatically and handles up to about 24 min of audio with full attention, or up to 3 h with local attention (measured on an A100). — [HF card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/blob/main/README.md)
- **Third-party Parakeet-v3 Greek is much worse:** FluidInference measured **36.9% WER** on an **Apple M4 Pro** over 650 FLEURS Greek files, the second-worst of 25 languages. Spokenly/Moondream measured **35.71%** and called Greek V3's weakest language. — [FluidInference benchmarks](https://docs.fluidinference.com/reference/benchmarks); [Spokenly](https://spokenly.app/blog/parakeet-models)
- **Qwen3-ASR** (Apache-2.0, tech report v2 dated 2026-01-30): Greek is one of its 30 languages. Official FLEURS Greek WER is **0.6B = 49.67, 1.7B = 28.08**. The API-only Flash-1208 gets 13.85. Qwen3-ForcedAligner does **not** support Greek. — [arXiv 2601.21337](https://arxiv.org/html/2601.21337). An independent Mac run gave 1.7B = 30.22. — [whispernotes.app](https://whispernotes.app/blog/qwen3-asr-vs-whisper). BuzzASR reports Qwen3 at 42.07 WER, but that is raw and unnormalized. — [arXiv 2609.09554](https://arxiv.org/html/2609.09554v2)
- **BuzzASR baselines, combined FLEURS+CV25 Greek, WER (CER):**
  - Whisper-large-v3: 13.58 (4.37)
  - **Omnilingual 7B: 14.50 (6.57)**
  - Omnilingual 1B: 21.95 (8.84)
  - MMS: 20.29 (5.52)
  - Cohere: 23.36 (6.42), raw
  - Qwen3: 42.07 (16.29), raw

  The paper does not say whether "Omni" means the CTC or LLM variant. — [arXiv 2609.09554 Tables 7–8](https://arxiv.org/html/2609.09554v2)
- **Meta Omnilingual ASR** memory: omniASR-LLM-7B has about 7.8B params, about 30 GiB on disk, and needs about **17 GiB inference memory** (BF16, batch 1, A100). The CTC/LLM models accept audio **under 40 seconds** only, so long audio must be chunked. — [HF commit / model card](https://huggingface.co/facebook/omniASR-LLM-7B/commit/4b0bada258b398cb7e6c5b3a6ed5448fb914385b); [PyPI README](https://pypi.org/project/presight-omnilingual-asr/)
- **Mistral Voxtral:** Voxtral Transcribe 2 (2026) covers 13 languages and **Greek is not one of them**. The only open weights are Voxtral Realtime (`voxtral-mini-transcribe-realtime-2602`, Apache-2.0); the batch model is API-only. — [Mistral news](https://mistral.ai/news/voxtral-transcribe-2); [Mistral docs](https://docs.mistral.ai/studio-api/audio); [aitoolsatlas](https://aitoolsatlas.ai/tools/voxtral-transcribe-2/pricing). A "Voxtral Small Transcribe 3" (Oct 2026, closed weights) appears only on a directory site and is unverified. — [llmreference](https://www.llmreference.com/model/voxtral-small-transcribe-3)
- Orukeet (Sept 2026 research model) scores 30.81 WER / 9.18 CER on FLEURS Greek, against 21.07 WER / 9.01 CER for Parakeet in the same paper. — [arXiv 2609.10054](https://arxiv.org/pdf/2609.10054)

### Inferences
- Shortlist for Greek on an M1 Pro with 32 GB:
  1. **whisper-large-v3 / turbo**: mature on Mac (whisper.cpp / MLX), 11–14 WER.
  2. **Canary-1B-v2**: best published FLEURS Greek, but its Mac runtime is NeMo/PyTorch and less mature (my inference; MPS support not verified).
  3. **Omnilingual 7B**: about 17 GiB, fits in 32 GB. The 40 s input limit means chunking.
- Canary's 9.21 comes from NVIDIA's own table, the same one where large-v3 got 27.03, much worse than elsewhere. **The relative ranking may be inflated.** Benchmark both locally before choosing.
- Parakeet-v3 is fast but its Greek accuracy is unreliable: about 21 by NVIDIA's numbers and about 36 by third parties.

### Gaps
- I did not verify these this session: **Phi-4-multimodal** speech languages (NVIDIA's table shows no Greek entry), **IBM Granite Speech**, **Kyutai STT** (my recollection is English/French only), **SeamlessM4T** license details, and the Omnilingual paper's own per-language Greek table (arXiv 2511.09690). Treat Greek as unsupported until a model card says otherwise.
- I did not identify or verify the "Cohere" ASR model in BuzzASR's table.
- I found no Greek row for the multilingual tab of the HF Open ASR Leaderboard; I did not fetch it.
- Canary-1B-v2 memory on Apple Silicon: not measured. Estimate about 2–4 GB at fp16–fp32, not measured.

## 4. Code-switching: Greek mixed with English tech words

### Takeaway
Greek–English code-switching is a **major weak spot** for every open model. Whisper's error rate on code-switched speech is more than double its monolingual rate, and the Greek/English script mix makes it worse. The one Greek production paper reports **24–60% WER on code-switched audio** and **16–20% WER on Greek business meetings**, even for its best systems.

### Cited Findings
- CS-FLEURS (Sept 2025): Whisper's CER on code-switched speech is **more than twice** its monolingual FLEURS CER. **Pairs that use different scripts, such as Greek–English, do much worse** than same-script pairs. — [arXiv 2509.14161](https://arxiv.org/pdf/2509.14161)
- KIEFER SA / Sophea AI Lab (Athens, Sept 2026), production Greek–English recognizer:
  - Code-switched WER: **59.74** for the compact 1.7B bilingual model and **23.84** for a three-model ensemble vote.
  - Turbo's code-switched WER went from 10.78 to 9.79 with decode-time changes only (a different test set, not in its Table 2).
  - "Turbo and the compact model show weak code-switching language ID."
  - **Two fine-tuning attempts made code-switching worse** than the untrained baseline, so the shipped system uses stock weights plus decode-time fixes.
  - Business meetings WER: compact 20.53, Canary FT 18.27, Whisper FT 15.73, three-model vote 16.59.
  - Routing: forced Greek language decoding for Greek-heavy domains, automatic language ID elsewhere.

  — [arXiv 2609.13498](https://arxiv.org/html/2609.13498)
- Greek Podcast Corpus: mixed English–Greek segments (e.g. "ακολουθήστε με στο Instagram") were **dropped** from training because alignment failed. Public Greek fine-tunes therefore probably saw little code-switched data. — [arXiv 2406.15284](https://arxiv.org/html/2406.15284v1)
- Other language pairs show the same pattern: Whisper-large-v3 often drops code-switched words, and sometimes **translates instead of transcribing** even when told to transcribe. — [Casablanca, arXiv 2410.04527](https://arxiv.org/pdf/2410.04527)

### Inferences
- With `language="el"` forced, English tech words may come out transliterated into Greek script or be mistranscribed. With auto-detect, a Greek-heavy meeting may get labeled as English and translated (inferred from the findings above, not measured on Greek). For meetings, test both modes and try an `initial_prompt` listing the expected English terms.
- **Greeklish output** (Greek written in Latin letters): I found no source that documents it as a common failure mode.

### Gaps
- I found no public Greek–English code-switching test set with open-model scores. KIEFER's sets are proprietary apart from FLEURS and TEDx.
- I found no source documenting how individual models write English terms inside Greek output (Latin vs Greek script).

## 5. Known Greek failure modes

### Takeaway
The documented failures are **hallucinated boilerplate subtitle text on music or silence**, **dropped overlapping speech**, **under-generation in noise**, and **number format mismatches** (digits vs words). Tonos/diacritics mostly affect *scoring*: most benchmarks strip them.

### Cited Findings
- **Hallucination on music or silence:** Whisper large-v3 produced the fake caption "Υπότιτλοι AUTHORWAVE" on music segments that VAD did not filter out. — [arXiv 2406.15284](https://arxiv.org/html/2406.15284v1)
- **Hallucination on non-speech in fine-tunes:** KIEFER's Whisper FT hallucinated on **100%** of a non-speech test. An earlier training-data package taught the model to emit fluent boilerplate phrases. Their fixes:
  - VAD plus no-speech-probability and log-probability thresholds
  - A boilerplate phrase blocklist (two English words were removed from it because of false positives)
  - Suppressing output when the ensemble detects silence

  — [arXiv 2609.13498](https://arxiv.org/html/2609.13498)
- **Overlapping speech:** about **40%** of overlapped speech is deleted on average. **Noisy audio:** hypothesis/reference length ratio stuck around 0.93, i.e. under-generation. — [arXiv 2609.13498](https://arxiv.org/html/2609.13498)
- **Numbers:** a phone number transcribed as digits instead of words was scored as a big regression. Their Greek normalizer folds diacritics and case and turns spoken numbers and units into a standard form. — [arXiv 2609.13498](https://arxiv.org/html/2609.13498). The Logotypografia references write numbers out as words and keep stress marks. — [arXiv 2406.15284](https://arxiv.org/html/2406.15284v1)
- **Tonos/diacritics:** NVIDIA's multilingual normalizer maps or strips diacritics before scoring, so reported WER hides tonos errors. — [arXiv 2509.14128](https://arxiv.org/html/2509.14128v2)
- **Repetition loops:** BuzzASR decodes every system with repetition_penalty 1.2 and no_repeat_ngram_size 3, and KIEFER uses repetition penalty 1.1 with Turbo. Both point to known repetition problems. — [BuzzASR card](https://huggingface.co/BuzzASR/greek); [arXiv 2609.13498](https://arxiv.org/html/2609.13498)
- **Turbo on informal audio:** it stays close to large on clean read speech but falls well behind on informal or community-sourced Greek. — [arXiv 2609.13498](https://arxiv.org/html/2609.13498)
- **Dialects:** Cypriot Greek gets 34–38 WER even with large-v3. — [ACL Anthology](https://aclanthology.org/people/spyros-armostis/unverified/)
- **Reverberant rooms:** parliament audio (HParl) is hard: 17 WER for large-v3, 39 for medium. — [arXiv 2406.15284](https://arxiv.org/html/2406.15284v1)

### Inferences
- For Greek meeting recordings run locally, plan on **VAD (e.g. Silero) before Whisper**, a no-speech threshold, a filter for "Υπότιτλοι…"-style boilerplate, and a repetition penalty. Expect about 15–20% WER on meetings, not the FLEURS 10–14% (estimate based on KIEFER's business-meeting numbers).
- If diarization or overlapping speakers matter, no model here handles them. That needs a separate step.

### Gaps
- I found no source quantifying tonos errors when scored *with* diacritics.
- I found no Greek-specific measurement of silence hallucination for stock large-v3 vs turbo vs Canary.
