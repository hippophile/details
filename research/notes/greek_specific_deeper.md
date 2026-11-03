# Greek-specific STT: deeper pass (as of 2026-11-03)

> Scope: only what is **new** compared with `greek_asr_models.md`, `mac_live_tooling.md`, `cloud_greek_asr.md` and the report. Model metadata (downloads, dates, licenses, file lists) was pulled live from the Hugging Face API on 2026-11-03. Downloads are HF's rolling 30-day count. **Every WER below is self-reported by the model author** on small test sets unless marked otherwise. Nothing here was run on the M1 Pro.

## 0. TL;DR (what changes the plan)

| # | Finding | Why it matters |
|---|---|---|
| 1 | **myned-ai/parakeet-tdt-0.6b-v3-greek** (Oct 2026, CC-BY-4.0). Full fine-tune of Parakeet v3 on 2,087 h of Greek. Fixes the base model's missing final sigma (ς). Self-reported **CV Greek 8.9% vs large-v3 16.9%**, FLEURS 11.4 vs 11.9, on the same clips. 12–15x real time **on CPU**. ONNX is ready. | It is the first fast, non-Whisper candidate that beats large-v3 on conversational Greek. It also explains the "Parakeet v3 Greek = 36% WER" result in the earlier notes: the base model **can't write ς**. |
| 2 | **Greek large-v3-turbo fine-tune with Mac formats already built**: `sam8000/whisper-large-v3-turbo-greek-greece` (MIT, FLEURS 11.06 normalized, up from 14.1 at the start of training). There is a **ggml Q5_K_M** for whisper.cpp (`nicman23/...-gguf`, 1.6 GB) and a **WhisperKit CoreML** build (`dkaloger/whisper-greek-coreml`). | You can drop it into the whisper.cpp or WhisperKit tooling with no conversion step. Caveat: it was trained on FLEURS and scored on FLEURS, so the gain may not carry over to meetings. |
| 3 | **OpenCouncil LoRA for large-v3** (Apache-2.0, Aug 2026), trained on Greek **municipal council meetings**. Held-out meeting WER is **17.95% vs 19.88%** for stock large-v3, with about 40% fewer deletions than its own v1. The card publishes the exact decode config. | This is the closest public match to "Greek meetings". The number is independently scored on 117 meetings. |

## 1. Greek fine-tuned models on Hugging Face (new ones only)

### 1a. Whisper family

| Model | Base | Greek WER (test set) | DL/30d | Created | License | Mac format? |
|---|---|---|---|---|---|---|
| [sam8000/whisper-large-v3-turbo-greek-greece](https://huggingface.co/sam8000/whisper-large-v3-turbo-greek-greece) | large-v3-turbo | **11.06** normalized / 26.87 orthographic, FLEURS el_gr (split "None") | 756 | 2025-05-21 | MIT | via the two repos below |
| [nicman23/whisper-large-v3-turbo-greek-gguf](https://huggingface.co/nicman23/whisper-large-v3-turbo-greek-gguf) | the sam8000 model | none ("excellent accuracy", no numbers) | 0 | 2026-05-21 | none set (card says MIT) | **ggml Q5_K_M, 1.6 GB, whisper.cpp** |
| [dkaloger/whisper-greek-coreml](https://huggingface.co/dkaloger/whisper-greek-coreml) | the sam8000 model (folder name) | none, no card | 0 | 2026-06-10 | none set | **CoreML `.mlmodelc` (AudioEncoder etc.), WhisperKit layout** |
| sam8000/...-5percent, ...-3percent | turbo | no card | 44 / 26 | 2025-06 | none set | — |
| [opencouncil/whisper-large-v3-el-council-lora](https://huggingface.co/opencouncil/whisper-large-v3-el-council-lora) | large-v3 + LoRA r=32 (q,v) | **17.95 vs 19.88 stock** on held-out council audio (391 windows, 117 meetings); val 13.90; deletion rate 3.1% | 152 | 2026-07-23 (v2 2026-08-23) | Apache-2.0 | PEFT only; they score via CTranslate2 fp16 after merging |
| [AliacAI/whisper-base-el-en](https://huggingface.co/AliacAI/whisper-base-el-en) | whisper-base (73M) | FLEURS el **28.65** (stock base 62.50, stock small 35.11) | 25 | 2026-10-20 | Apache-2.0 | **ggml q8_0 included** |
| [mandododo/whisper-small-greek-futo](https://huggingface.co/mandododo/whisper-small-greek-futo) (also base, tiny) | whisper-small | CV27 test **14.7**, FLEURS **17.4** (200 clips each) | 0 | 2026-09-30 | MIT | ggml `.bin` (FUTO Keyboard) |
| [ilsp/whisper-*-gad-*](https://huggingface.co/ilsp) (small/medium/large-v3, "transcribe"/"multitask" variants) | Whisper | no card, no README | ≤45 | 2026-06-18 | none set | — |
| [ilsp/whisper_greek_dialect_of_lesbos](https://huggingface.co/ilsp/whisper_greek_dialect_of_lesbos), whisper-lesbian-asr, whisper-aperathiot-asr | Whisper | dialect only | ≤11 | 2025–26 | — | — |
| [vitthalbhandari/whisper-large-v3-aft-all-el-CY](https://huggingface.co/vitthalbhandari/whisper-large-v3-aft-all-el-CY) | large-v3 | Cypriot (CV Spontaneous), no WER in the metadata | 44 | 2026-03-04 | **CC-BY-NC-4.0** | — |
| [alphaedge-ai/whisper-large-v3-turbo-ell-16384](https://huggingface.co/alphaedge-ai/whisper-large-v3-turbo-ell-16384) (also -32768) | turbo, **vocabulary trimmed** to Greek tokens (−5.6% params) | none | 5 | 2026-06-01 | Apache-2.0 | — |
| [Fotiissss/whisper-large-v3-turbo-lora-el](https://huggingface.co/Fotiissss/whisper-large-v3-turbo-lora-el) | card says large-v3 | 46.43, unknown dataset | 9 | 2025-05 | Apache-2.0 | skip |
| [mozilla-ai/whisper-small-el](https://huggingface.co/mozilla-ai/whisper-small-el) | small, 3,620 CV17 clips | 45.6 | 21 | 2025-02 | Apache-2.0 | skip |
| [Decoder953/whisper-large-v2-greek-CT2-FP16](https://huggingface.co/Decoder953/whisper-large-v2-greek-CT2-FP16) | large-v2 → CTranslate2 | none | 58 | 2024-11 | — | faster-whisper only |

Notes:
- **sam8000 caveat:** the card says only "fleurs", so the training data was FLEURS and so was the evaluation (split not given). The 14.1 → 11.06 gain is **in-domain**. Its own orthographic WER is 26.9%, which suggests punctuation and casing drift (inference). [card](https://huggingface.co/sam8000/whisper-large-v3-turbo-greek-greece)
- **The ggml/CoreML conversions are unverified third-party builds.** They have no WER, no hashes, and 0 downloads. Before relying on either, re-quantize from the sam8000 safetensors yourself with whisper.cpp's converter (my recommendation, not tested).
- **OpenCouncil:**
  - Published decode config: `language="el", beam_size=5, condition_on_previous_text=False, temperature ladder 0→1.0, no_speech_threshold=0.6, log_prob_threshold=-1.0, compression_ratio_threshold=2.4, vad_filter=False`.
  - Score on names and places (DS-WER): Soniox 0.328, Scribe v2 0.372, this LoRA 0.436, stock large-v3 0.540.
  - The v2 vs v1 overall WER gap is **not significant**: the confidence interval crosses 0. The fall in deletions is significant.
  - The author says plainly that it "loses to ElevenLabs Scribe v2 and to Soniox".
  - Its v2 training data was packed into about 22 s windows of one speaker each. That is a recipe to copy for your own meeting fine-tune.

  [card](https://huggingface.co/opencouncil/whisper-large-v3-el-council-lora)
- **AliacAI el-en** is the only public model trained **jointly on Greek and English**: 179 h Greek + 90 h English, keeping tonos, dialytika, ς, casing and punctuation. It fixes Greek-accented English being decoded as Greek.
  - Its card says **mixed Greek/English within one utterance and English tech terms inside Greek speech "were not evaluated"**.
  - It drops the final word on 7.4% of FLEURS clips.

  [card](https://huggingface.co/AliacAI/whisper-base-el-en)

### 1b. Parakeet / wav2vec2 / XLS-R / Conformer

| Model | Base | Greek WER | DL/30d | Date | License | Format |
|---|---|---|---|---|---|---|
| **[myned-ai/parakeet-tdt-0.6b-v3-greek](https://huggingface.co/myned-ai/parakeet-tdt-0.6b-v3-greek)** | Parakeet-TDT-0.6B-v3, full fine-tune, +33 vocabulary pieces (ς, ς-endings, accented capitals) | **FLEURS 11.4 / CV el 8.9 / Cypriot 25.9** (fp32); CER 3.6 / 2.3 / 8.1 | 148 | 2026-10-11 | CC-BY-4.0 | `.nemo` + **fp32 ONNX (2.5 GB)** + int8 ONNX (0.7 GB) |
| [jonatasgrosman/wav2vec2-large-xlsr-53-greek](https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-greek) | XLSR-53 (CTC) | CV 6.1 test **11.62** / CER 3.36 | **2.33M** (probably CI or pipeline pulls) | 2022 | Apache-2.0 | PyTorch/Flax; [GGUF port](https://huggingface.co/liodon-ai/wav2vec2-large-xlsr-53-greek-GGUF) (2026-06) and [ONNX port](https://huggingface.co/XDcobra/wav2vec2-large-xlsr-53-greek-ONNX) (2026-10) |
| lighteternal / vasilis / PereLluis13 xlsr-53-greek, infinitejoy / jerrychatz xls-r-300m-greek | XLSR | 2021–22 era (HF sprint) | ≤400 | 2022 | mostly Apache | — |
| ilsp/xls-r-greek-cretan, -cappadocian, -lesbian, -aivaliot, -pomak | XLS-R | dialects | ≤19 | 2024–25 | — | — |

**Parakeet-Greek details** (all from the [card](https://huggingface.co/myned-ai/parakeet-tdt-0.6b-v3-greek)):
- Test sets: 150 FLEURS clips, 150 CV clips from unseen speakers, 646 Cypriot clips.
- **Same-clip comparison** (lowercase, no punctuation, ς counted):

  | WER | FLEURS | CV | Cypriot |
  |---|---|---|---|
  | Stock Parakeet v3 | 33.6 | 23.0 | 64.6 |
  | **This model** | 11.4 | 8.9 | 25.9 |
  | Whisper large-v3 | 11.9 | 16.9 | 46.9 |
  | Whisper turbo (int8) | 12.8 | 20.8 | 48.2 |

  Stock Parakeet scores 21.0/7.9 when ς is ignored. **So the 36% third-party number in our earlier notes is mostly the ς bug.**
- **int8 hurts Greek badly:** fp32 11.4 → int8 18.7 on FLEURS. Base Parakeet loses 7–10 points in int8. The int8 file is "the common CPU download", and the card says int8 is not faster. **Use fp32 only.** This probably also explains part of FluidInference's 36.9% on Apple Silicon (inference).
- Fixes on short clips: WER on 1–3-word clips drops 28.9 → 6.0%, and Latin-letter outputs drop 19 → 0.
- The base model never writes the Greek `;`. This one writes `?`, and you replace it with `;`.
- Numbers come out as a **mix of digits and words. There is no inverse text normalization (ITN).**
- **Not measured:** noise, phone audio, far-field mics, code-switching. The card says Whisper stays stronger on "names and foreign words".
- Training: 2,003 h of auto-transcribed Greek podcasts (silver labels) + CV27 + Cypriot.
- Runtime: `onnx-asr` on CPU, or NeMo. **No MLX/CoreML build found.** On an M1 Pro the CPU ONNX route should work. Speed on M1 is not measured; the card's 12–15x real time is on an Intel i7-13800H.
- onnxruntime 1.31 refuses the 2.5 GB weights through HF cache symlinks, so pass `load_model(..., "<folder>")`.

### 1c. Not found (searched HF for "greek", "el", "ell", "parakeet greek", "canary greek", "conformer greek", "ggml greek", plus `filter=mlx`)
- **No MLX Greek ASR model** of any kind.
- **No Canary-1B-v2 Greek fine-tune** and no Greek Conformer (NeMo) checkpoint beyond the Parakeet one above.
- **No Greek↔English code-switching ASR model.** The closest is AliacAI el-en, which is untested on code-switching.
- ILSP **VoxKrikri-21** ([HF](https://huggingface.co/ilsp/VoxKrikri-21-full), Apache-2.0, 2025-09, 4 safetensors shards, empty README) is a speech + language model on Krikri. ILSP's 2025 report names "VOX-Krikri". **No ASR WER is published, so it's unverified as an STT option.** [ILSP 2025 report](https://www.ilsp.gr/?p=9553)

## 2. Greek-specific handling

### Code-switching (Greek + English tech words)
- **No public Greek–English code-switching dataset or model with scores exists.** The KIEFER paper's sets, already in our notes, are proprietary. AliacAI explicitly did not evaluate code-switching. [AliacAI card](https://huggingface.co/AliacAI/whisper-base-el-en)
- In other language pairs, stock Whisper drops English tech terms or transliterates them into the native script ("embedding" → Arabic script). The fix there was **fine-tuning, not prompting**. That source is a vendor page. [friendli/Seif-Eldeen-Sameh](https://friendli.ai/models/Seif-Eldeen-Sameh/whisper-medium-arabic-codeswitched)
- **Metric trap:** aggregate WER hides code-switching failures, because the embedded English words are a small share of all words. Score the tech terms separately, e.g. Point-of-Interest error rate or a keyword hit rate. [KIT ISL](https://isl.iar.kit.edu/english/9942.php). OpenCouncil does the same with DS-WER for names (§1a).

### Tonos and punctuation restoration
- **I found no Greek-specific tonos or punctuation restoration model in 2026.** The closest is UniForm (NeurIPS 2026), a 0.8B joint punctuation + ITN model; its Greek coverage is unknown. [NeurIPS poster](https://neurips.cc/virtual/2026/poster/153759)
- In practice the ASR model handles it: Whisper and Parakeet-Greek emit tonos and punctuation natively. **Whisper never writes the Greek `;`** and writes `?` instead, per the Parakeet-Greek card's examples. Fix with one `replace("?", ";")` in post-processing. [card](https://huggingface.co/myned-ai/parakeet-tdt-0.6b-v3-greek)
- AliacAI and Parakeet-Greek were both trained to keep tonos, dialytika and ς. Stock Parakeet v3 has **no ς token**.

### Inverse text normalization (numbers)
- **I found no open Greek ITN tool.** Parakeet-Greek outputs numbers as a mix of digits and words. OpenCouncil says numbers, dates and amounts are "not separately validated".
- NeMo's text-processing ITN grammar list does not include Greek, from my training knowledge (**unverified this session**).
- Option: an LLM post-pass (e.g. a local Greek LLM such as Meltemi or Krikri) to normalize numbers. This is untested and carries a risk of hallucinated edits.

### Filler words (εεε, λοιπόν, δηλαδή)
- **Whisper drops most hesitations by default.** This is practitioner evidence, not a benchmark: an mlx-whisper workflow reports most um/uh are dropped, and a verbatim mode works by seeding a hesitation prompt with `condition_on_previous_text=False`. [skills.sh filler-removal](https://www.skills.sh/agamm/video-agent/filler-removal); [HF whisper discussion #30](https://huggingface.co/spaces/openai/whisper/discussions/30)
- OpenAI's own guide lists "Preserving punctuation, capitalization, and **filler words**" as a prompt use case. [OpenAI STT guide](https://developers.openai.com/api/docs/guides/speech-to-text)
- **Distinction (inference):** "εεε/εμ" are acoustic hesitations, which get dropped. "λοιπόν/δηλαδή/ας πούμε" are real lexical words and should normally be transcribed. **No Greek-specific measurement exists.**
- For verbatim output, CrisperWhisper is the known fine-tune, but its Greek coverage is unverified (likely EN/DE). [Replicate](https://replicate.com/collectiveai-team/crisperwhisper/readme)

## 3. Whisper prompt tricks for Greek

- **Official guidance:**
  - The prompt helps with "product names, technical terms, and acronyms" via "a dictionary of the correct spellings".
  - It is **capped at 224 tokens**.
  - "Whisper doesn't follow instructions": a prompt is example text, not a command.

  [OpenAI STT guide](https://developers.openai.com/api/docs/guides/speech-to-text)
- **Greek budget (inference):** at **3.27 Whisper tokens per Greek word** (BuzzASR Table 6, already in our notes), 224 tokens is about **68 Greek words**. A prompt that mixes a short Greek sentence with an English term list fits roughly 1 Greek sentence plus 25–40 English terms (estimate).
- **Suggested shape** (untested). Write it as a natural transcript snippet in the target style, not an instruction:
  `"Λοιπόν, στο meeting για το deployment στο Azure, είδαμε το pull request, το Blazor component και το PostgreSQL migration."`
  This primes the model to write English terms in Latin script inside Greek and to keep tonos and punctuation.
- **Evidence level: none for Greek.** I found no paper or benchmark measuring `initial_prompt` for Greek or for Greek–English. Related work says language-prompt tricks are weak in zero-shot code-switching. [arXiv 2412.16507](https://arxiv.org/html/2412.16507v1)
- **Risk:** a prompt can induce hallucinated prompt words on silence. Pair it with VAD and with `condition_on_previous_text=False`, as OpenCouncil does.
- **Test plan:** run the same 10–20 min of your own meeting audio with no prompt vs a prompt, and count tech-term hits plus WER.

## 4. Greek datasets (test / fine-tune)

| Dataset | Size | License | Notes / source |
|---|---|---|---|
| **Common Voice Scripted 25.0 el** (2026-03-22) | 33.71 h recorded / **19.95 h validated**, 29,232 clips, 452 speakers | CC0 (standard for CV) | [Mozilla Data Collective](https://prod.datacollective.mozillafoundation.org/datasets/cmn2cx91x01dno10754vxfu3b). 24.0 had 19.63 h validated ([link](https://datacollective.mozillafoundation.org/datasets/cmj8u3p1q0071nxxbjywkh3f5)). CV27 el is referenced by the 2026 models. |
| CV Spontaneous Speech el-CY 3.0 | 10.37 h, 10 speakers | CC0 (assumed) | [link](https://datacollective.mozillafoundation.org/datasets/cmn1prgv200wdmm074tv16ejk). Cypriot only. |
| **FLEURS el_gr** | about 10–12 h per language (**unverified for el**); the test split is 649 clips per [mozilla-ai card](https://huggingface.co/mozilla-ai/whisper-small-el) | CC-BY-4.0 (Google FLEURS) | Read Wikipedia sentences; the default benchmark |
| **HParl** (Hellenic Parliament, ILSP) | **120 h** (about 99 h train / 11 h test), 387 speakers, from 303 h raw | **Conflicting:** the HF mirror's YAML says CC-BY-4.0, but its own text says **CC BY-NC 4.0**. Original at [CLARIN:EL 1602](https://inventory.clarin.gr/corpus/1602) | [HF mirror](https://huggingface.co/datasets/Elormiden/Hellenic-greek-parliamentary-speech) (13.8 GB); [arXiv 2301.00304](https://arxiv.org/pdf/2301.00304). Treat it as **non-commercial**. |
| EuroSpeech (Greek part) | 42 h used by myned-ai | see source | [disco-eth/EuroSpeech](https://huggingface.co/datasets/disco-eth/EuroSpeech) |
| VoxPopuli el | (size not checked) | CC0 (VoxPopuli, unverified) | [ilsp/voxpopuli_el](https://huggingface.co/datasets/ilsp/voxpopuli_el), 2025-10 |
| **Greek Podcast Corpus (GPC)**, ILSP | 800 h with silver labels | **not verified** | [arXiv 2406.15284](https://arxiv.org/pdf/2406.15284) |
| **myned-ai/pocket-tts-greek-data** | **2,400 h**: podcasts 2,329 h (9,862 episodes, 521 shows), parliament 42 h, CV 26 h. **Pointers + timestamps only, no audio or transcripts.** `reconstruct.py` re-downloads the audio. | CC-BY-4.0 for the index. The audio stays copyrighted by the publishers. | [HF](https://huggingface.co/datasets/myned-ai/pocket-tts-greek-data). Built for TTS. Rebuilding it means re-transcribing yourself. |
| Logotypografia | (hours unverified) | (unverified) | Read newspaper speech; references keep tonos and write numbers as words ([arXiv 2406.15284](https://arxiv.org/html/2406.15284v1)) |
| SpeechDataAI greek-conversational-speech | **1,000 h** of two-person spontaneous calls, 100 speakers, dual-channel | **Commercial (paid)**; the HF repo is likely a sample | [HF](https://huggingface.co/datasets/SpeechDataAI/greek-conversational-speech), 2026-10 |
| Speech-data/Greek-Speech-Dataset, shunyalabs/greek-speech-dataset | <1K and 1K–10K clips | CC-BY-NC-ND / unset | small samples |

**Inference:** for testing the user's use case (Greek meetings with English tech terms), none of these sets fit. **Record 30–60 min of your own meetings and hand-correct them.** That beats every public set for decision-making. FLEURS + CV are only sanity checks.

## 5. Greek-capable releases in 2026 that we hadn't covered
- myned-ai Parakeet Greek (2026-10). See §1b.
- OpenCouncil council LoRA v1/v2 (2026-07/08). See §1a.
- AliacAI whisper-base-el-en (2026-10), FUTO Greek whisper small/base/tiny (2026-09), and ILSP GAD Whisper variants (2026-06, undocumented).
- Greek-trimmed turbo (alphaedge, 2026-06). It only shrinks the vocabulary and gives no WER, so it is not useful for speed.
- wav2vec2-greek GGUF/ONNX ports (2026-06/10).
- **Not found:** any new big multilingual open model (2026) with published Greek WER beyond those already in the notes (Qwen3-ASR, Omnilingual, Canary v2, Voxtral without Greek).

## 6. Gaps / unverified
- Every WER for the new models is **self-reported on 150–650 clips**. Parakeet-Greek says differences under 2 points are noise.
- No M1 Pro speed numbers exist for Parakeet-Greek ONNX or for the sam8000 ggml/CoreML builds.
- No Greek ITN tool; NeMo Greek ITN is unverified.
- No Greek measurement of filler drop or of the `initial_prompt` effect.
- Not checked: the ILSP GAD models' purpose, VoxKrikri ASR quality, GPC and Logotypografia licenses and hours, FLEURS el hours.
