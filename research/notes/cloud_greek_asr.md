# Cloud / Commercial Speech-to-Text APIs for Greek (quality baseline vs local models) — as of 2026-11-03

> Main caveat: **I found no independent, multi-vendor Greek benchmark** (Artificial Analysis or anyone else). Almost every Greek WER below is **vendor-published** and uses a different dataset and model version. The one independent, real-world Greek number is from OpenCouncil (Greek municipal councils, June 2026): ElevenLabs Scribe v2 had ~13% WER and Whisper ~15% WER. On real meeting audio, cloud was only about 2 points better.

## 1. Which APIs support Greek (batch + real-time streaming)?

### Takeaway
All the major vendors list Greek (el / el-GR) for batch. Streaming support for Greek is confirmed in the sources for Deepgram Nova-3 (July 2026), AWS Transcribe, Speechmatics, Soniox and Mistral Voxtral Realtime 3. It is unclear or unverified for AssemblyAI, Google Chirp 3, Gladia, Azure and ElevenLabs in this pass.

### Cited Findings
- **ElevenLabs Scribe**: supports 99 languages, and Greek is listed under "Excellent Accuracy (≤5% WER)". The Greek page names only Scribe v1 and says a real-time version "will be released soon". The page is undated and stale, because the Pipecat benchmark (Aug 2026) already tests `scribe_v2_realtime` — [ElevenLabs Greek](https://elevenlabs.io/speech-to-text/greek); [Soniox benchmarks](https://soniox.com/benchmarks)
- **Deepgram Nova-3**: Greek (`el`) was added as a monolingual Nova-3 language on 2025-12-08, alongside lt, lv, ms, sk, ca, et, nl-BE, de-CH and ro. The post says the new languages gained accuracy in batch and/or streaming but gives no Greek number. Keyterm prompting allows up to 500 tokens, about 100 words — [Deepgram blog](https://deepgram.com/learn/deepgram-expands-nova-3-with-10-new-languages-and-multilingual-keyterm-prompting)
- **Deepgram streaming Greek**: the 2026-07-21 changelog lists Greek (el) under new streaming models, with no API change needed — [Deepgram changelog 2026-07-21](https://developers.deepgram.com/changelog/2026/7/21)
- **AWS Transcribe**: the supported-languages table lists el-GR for both batch and streaming. No other language-specific features are listed for Greek — [AWS docs](https://docs.aws.amazon.com/transcribe/latest/dg/supported-languages.html)
- **Speechmatics**: Greek is supported for real-time and batch, with on-prem, on-device and cloud deployment — [Speechmatics Greek](https://www.speechmatics.com/speech-to-text/greek)
- **Soniox (stt-rt-v5)**: Greek is supported for real-time over WebSocket and for async — [Soniox Greek](https://soniox.com/speech-to-text/greek)
- **Mistral Voxtral Transcribe 3**: covers 50+ languages including all 24 official EU languages. Greek ("EL") is in the FLEURS evaluation list. The realtime model (Voxtral Mini Transcribe Realtime 3, under 500 ms) covers 35–40+ languages, and the page does not explicitly confirm Greek for realtime. The evaluation is dated 2026-10-20 — [Mistral Voxtral 3](https://mistral.ai/fr/news/voxtral-3/)
- **AssemblyAI**: the marketing page claims Greek transcription, including streaming. The docs conflict. The model-selection page lists the Universal-Streaming Multilingual languages as EN/ES/DE/FR/PT/IT only, while the async Universal list includes Greek. An older models reference says languages outside the 6 must use Universal-2 (async) or Whisper-Streaming — [AssemblyAI Greek](https://www.assemblyai.com/languages/greek); [AssemblyAI streaming model selection](https://www.assemblyai.com/docs/streaming/universal-streaming); [AssemblyAI models ref](https://www.assemblyai.com/llms/models.md)
- **Google Chirp 3**: available only in the Speech-to-Text V2 API, GA in the `eu` multi-region. The page excerpts were truncated, so el-GR could not be confirmed — [Google Chirp 3](https://cloud.google.com/speech-to-text/docs/models/chirp-3)
- **OpenAI**: offers gpt-4o-transcribe ($0.36/h), gpt-4o-mini-transcribe ($0.18/h) and gpt-realtime-whisper ($1.02/h, real-time). These prices come from the competitor calculator on Soniox's Greek page, not from OpenAI's own docs — [Soniox vs OpenAI Greek](https://soniox.com/compare-stt/soniox-vs-openai/greek)

### Inferences
- If you need Greek streaming that is confirmed in the docs, the safest options are **Deepgram Nova-3, AWS Transcribe, Speechmatics and Soniox**.
- The AssemblyAI docs suggest Greek may be async-only, or available only through its Whisper-based streaming tier. Do not plan on Greek streaming there without testing it.

### Gaps
- I did not check Azure Speech's el-GR real-time and batch status in this pass. A Speechmatics table lists an Azure Greek WER, which implies batch support.
- Gladia Solaria Greek support: no source found.
- Google Chirp 3: the excerpts did not confirm el-GR.
- ElevenLabs Scribe v2 / v2 realtime: the docs do not confirm Greek explicitly.
- OpenAI: no Greek-specific docs consulted. The Whisper heritage suggests Greek is supported, but this is unverified.

## 2. Published Greek WER (vendor + independent)

### Takeaway
Vendor FLEURS figures for Greek range from about 3% (ElevenLabs' claim) to about 18% (Deepgram Nova-2, as measured by competitors). On **real meeting audio**, the only independent test found that even the best cloud model (Scribe v2) had about 13% WER, against about 15% for Whisper. **FLEURS (read speech) numbers overstate real-world quality by roughly 3–4×.** Several "Greek WER" figures on Soniox's comparison pages are actually English benchmark numbers mislabeled as Greek.

### Cited Findings
**Vendor FLEURS Greek tables (self-published, no dates):**

| Source | Speechmatics | ElevenLabs Scribe | AWS | Whisper L-v3 | Azure | Google | Deepgram | Gemini |
|---|---|---|---|---|---|---|---|---|
| [Speechmatics Greek](https://www.speechmatics.com/speech-to-text/greek) | **6.58%** (Enhanced) | – | 10.66% | 12.32% | 13.21% | 14.91% (Chirp 2) | 17.97% (Nova-2) | – |
| [ElevenLabs Greek](https://elevenlabs.io/speech-to-text/greek) | – | **4.2%** (v1, table) / 3.1% (headline) | – | 13.8% | – | – | 18.5% (Nova-2) | 6.6% (2.0 Flash) |

- ElevenLabs also claims **5.5% WER on Common Voice** for Greek. Its page contradicts itself, giving 3.1% in the headline and 4.2% in the table — [ElevenLabs Greek](https://elevenlabs.io/speech-to-text/greek)
- On Speechmatics' page, the FAQ claims "up to 96% accuracy", which does not match its own 6.58% WER (about 93.4% accuracy) — [Speechmatics Greek](https://www.speechmatics.com/speech-to-text/greek)
- Both tables compare against **Deepgram Nova-2**, not Nova-3. Neither covers Nova-3 Greek (added Dec 2025), so Deepgram's real current Greek WER is unknown — [Deepgram blog](https://deepgram.com/learn/deepgram-expands-nova-3-with-10-new-languages-and-multilingual-keyterm-prompting)

**Soniox real-world YouTube-audio study (2025, 60 languages, vendor):**
- Greek WER: **Soniox 7.4%**, Google 10.3%, OpenAI 15.9%. Speechmatics is 9.5% in the study text and 17.4% in another section of the same page (an internal conflict) — [Soniox vs Speechmatics Greek](https://soniox.com/compare/soniox-vs-speechmatics/greek); [Soniox Greek](https://soniox.com/speech-to-text/greek); [Soniox 2025 benchmark PDF](https://soniox.com/media/SonioxSTTBenchmarks2025.pdf) (I did not see the Greek row in the PDF myself)

**Warning: mislabeled "Greek" numbers.** Search snippets of Soniox compare pages claim Greek WER of Soniox 1.25%, AssemblyAI 1.74%, Deepgram 1.71%, Google 2.84% and OpenAI 3.24% — [Soniox vs AssemblyAI Greek](https://soniox.com/compare-stt/soniox-vs-assemblyai/greek); [Soniox vs Deepgram Greek](https://soniox.com/compare-stt/soniox-vs-deepgram/greek); [Soniox vs Google Greek](https://soniox.com/compare-stt/soniox-vs-google/greek). These match the **English Pipecat streaming benchmark** almost exactly: AssemblyAI u3-rt-pro 1.74%, Deepgram nova-3-general 1.71%, Google latest-long 2.84%, OpenAI gpt-4o-transcribe 3.24%. The Pipecat benchmark page has no Greek breakdown — [Soniox benchmarks](https://soniox.com/benchmarks). **Do not use these as Greek numbers.**

**Pipecat STT benchmark (Aug 2026, real-time, semantic WER, 1,000 samples, mostly English):** Azure 1.21%, Soniox 1.34%, Speechmatics 1.40%, AssemblyAI U-3.5 Pro 1.44%, AWS 1.68%, Deepgram Nova-3 1.71%, Google Gemini-3.5-transcribe-live 2.24%, OpenAI gpt-realtime-whisper 2.92%, ElevenLabs scribe_v2_realtime 3.16%, gpt-4o-transcribe 3.24%, Voxtral mini realtime 2602 4.44%. **These are not Greek numbers**; they show general streaming ranking only — [Soniox benchmarks](https://soniox.com/benchmarks)

**Independent real-world Greek (best evidence found):**
- OpenCouncil benchmark run on 2026-06-10, on older Greek municipal-council sessions with corrected transcripts. **ElevenLabs Scribe v2 had about 13% WER (best closed model) and Whisper about 15% (best open model).** An earlier Greek-specialized open fine-tune was "about 3× worse than Whisper". The team picked Scribe v2 for the roughly 2-point margin and is now LoRA-fine-tuning Whisper-large-v3 on its corrections, with "encouraging" early results and no numbers yet. Error sources named: noisy rooms, overlapping speech, legal terms, names and document numbers. Published 2026-07-21 by Angelos Papamichail — [Schema Labs / OpenCouncil](https://schemalabs.substack.com/p/asr-finetuning)

**Mistral Voxtral 3**: publishes only macro-average WER across 45 languages (batch) and 35 (realtime), with no Greek breakdown. The original Voxtral (Jul 2025) FLEURS average excluded Greek — [Mistral Voxtral 3](https://mistral.ai/fr/news/voxtral-3/); [Voxtral arXiv 2507.13264](https://arxiv.org/pdf/2507.13264)

### Inferences
- Ranking signal (low confidence, vendor-mixed): **ElevenLabs Scribe, Speechmatics and Soniox are at the top for Greek. AWS is next, then Azure, Google Chirp 2 and Whisper-v3. Deepgram Nova-2 was weakest**, and Nova-3 is untested.
- On clean read speech, the best cloud models (about 3–7%) beat Whisper-large-v3 (about 12–14%) by roughly 2×. On real, noisy multi-speaker Greek, the gap shrank to about 2 points (13% vs 15%). **If the target audio is meetings or calls, local Whisper-class models (especially fine-tuned ones) are "close enough" in this one data point. If it is clean dictation, cloud is clearly better.** This is an estimate from one independent study, not measured by us.
- The only trustworthy comparison is to run the candidates on 30–60 minutes of your own Greek audio, with a corrected reference transcript.

### Gaps
- No Artificial Analysis or other independent leaderboard with a Greek row was found.
- No published Greek WER for Deepgram Nova-3, AssemblyAI Universal-3.x, Google Chirp 3, Azure (current), Gladia, OpenAI gpt-4o-transcribe (official), Voxtral 3, or Soniox v5 beyond Soniox's own 2025 study.
- I could not verify the Soniox 2025 PDF's Greek row directly.

## 3. Diarization for Greek, price per hour, EU residency / no-training

### Takeaway
On price, **Soniox (about $0.10–0.12/h) and Voxtral 3 (about $0.21–0.48/h) are cheapest**, with Gladia next. ElevenLabs costs $0.40/h, and OpenAI is $0.18–1.02/h. EU-hosted processing is documented for Soniox (with no-training stated), Mistral (EU endpoints plus self-deploy), and Google Chirp 3 (`eu` multi-region). **Speechmatics offers on-prem**, the strongest privacy option among the cloud vendors.

### Cited Findings
| Vendor | Greek diarization | Price (per audio hour) | EU / privacy | Source |
|---|---|---|---|---|
| Soniox stt-rt-v5 | Yes, RT + async (generic, not Greek-specific) | $0.12 RT, ~$0.10 async (updated Jun 2026) | Regions US, **EU**, JP, IN. Audio and transcripts stay in region, **not used for training** | [Soniox Greek](https://soniox.com/speech-to-text/greek) |
| Mistral Voxtral 3 | Yes (Mini, Small and Realtime) | Mini $0.0035/min (**$0.21/h**), Small $0.004/min ($0.24/h), Realtime $0.008/min ($0.48/h) | **EU endpoints**, self-deployment available. Open weights not mentioned | [Mistral Voxtral 3](https://mistral.ai/fr/news/voxtral-3/) |
| ElevenLabs Scribe | "Smart speaker diarization" (generic) | **$0.40/h**, enterprise discounts | Not stated on page | [ElevenLabs Greek](https://elevenlabs.io/speech-to-text/greek) |
| Speechmatics | Yes, including overlapping and multilingual speech | Not listed; $100 free credit | **On-prem / on-device / own cloud** | [Speechmatics Greek](https://www.speechmatics.com/speech-to-text/greek) |
| OpenAI | n/a in sources | gpt-4o-mini-transcribe $0.18, gpt-4o-transcribe $0.36, gpt-realtime-whisper $1.02 (Sep 2026) | Not checked | [Soniox vs OpenAI Greek](https://soniox.com/compare-stt/soniox-vs-openai/greek) |
| Gladia | n/a | Self-serve async 0.16€/h (Solaria-3), RT 0.68€/h, volume down to 0.12€/h (third-party tracker, 2026-10-15) | Not found | [usagepricing.com Gladia](https://www.usagepricing.com/blueprint/activity/gladia-2026-10-15-self-serve-euro-repricing) |
| Google Chirp 3 | Diarization only in BatchRecognize (and Recognize per one doc version), for a language subset. Greek not shown in excerpt | n/a | GA in **`eu` multi-region** | [Google Chirp 3](https://cloud.google.com/speech-to-text/docs/models/chirp-3) |
| AssemblyAI | n/a | n/a | Page claims "cloud or self-hosted" | [AssemblyAI Greek](https://www.assemblyai.com/languages/greek) |
| Deepgram | n/a | n/a ($200 sign-up credit) | n/a | [Deepgram blog](https://deepgram.com/learn/deepgram-expands-nova-3-with-10-new-languages-and-multilingual-keyterm-prompting) |
| AWS Transcribe | AWS table shows "no" in the extra-feature columns for el-GR (column headers not visible) | n/a | n/a | [AWS docs](https://docs.aws.amazon.com/transcribe/latest/dg/supported-languages.html) |

- Gladia pricing conflicts across sources. A July 2026 tracker entry lists $0.61/h async and $0.75/h RT for Starter, and $0.20/0.25 for Growth — [usagepricing.com Gladia Jul 2026](https://www.usagepricing.com/blueprint/activity/gladia-2026-07-30-growth-tier-uptime-sla)

### Inferences
- For a **privacy-first** user, the cloud options that best match a "local/private" preference are **Speechmatics on-prem**, **Mistral Voxtral self-deploy or EU endpoint**, and **Soniox EU region with no-training**. US-centric APIs (OpenAI, Deepgram, AssemblyAI) need their DPA or zero-retention terms checked before any real data is sent.
- Cost is not the deciding factor: all of these cost well under $1/h, so 100 h/month would be about $10–100 (estimate from list prices, not measured).

### Gaps
- No Greek-specific diarization accuracy (DER) was found for any vendor; all diarization claims are generic.
- Per-hour list prices are missing for Speechmatics, Deepgram Nova-3, AssemblyAI, Azure, AWS and Google Chirp 3 (not fetched in this pass).
- Not checked: no-training, DPA and zero-retention terms for ElevenLabs, OpenAI, Deepgram, AssemblyAI and Gladia; AWS and Azure EU regions for Greek.

## 4. Greek-specific providers (parliament, courts, Greek startups)

### Takeaway
I found no Greek ASR vendor with published WER that beats the global APIs. The Hellenic Parliament is rolling out "AI@Parliament" (speaker ID plus speech-to-text, Oct–Nov 2026), but the vendor is not named. Aseto AI (Cyprus) claims improved Greek and Cypriot-dialect ASR without published numbers. OpenCouncil (an open project) uses ElevenLabs Scribe v2 and is fine-tuning Whisper.

### Cited Findings
- **Hellenic Parliament, AI@Parliament**: speaker identification, speech-to-text, and linking each phrase to its point in the session video. Preparation runs to September 2026, with gradual rollout in October–November 2026. No vendor is named — [newsbeast.gr](https://www.newsbeast.gr/politiki/arthro/13271210/i-techniti-noimosyni-bainei-sti-vouli-se-ti-tha-chrisimevoun-oi-nees-efarmoges); [Fortune Greece](https://www.fortunegreece.com/article/aiparliament-i-nea-platforma-pou-tha-analiei-omilies-kai-sinedriaseis-tis-voulis/)
- **Aseto AI (Cyprus)**: announced improved STT for standard Greek and the Cypriot dialect (2025-12-09), targeting conversational speech (phone numbers, addresses). No WER or comparisons are given; a whitepaper at aseto.ai/whitepaper was not accessed — [Economy Today / Sigmalive](https://economytoday.sigmalive.com/oikonomia/kypros/118236-i-aseto-ai-fernei-tin-epanastasi-stin-anagnorisi-omilias-gia-tin-elliniki/)
- **OpenCouncil**: open platform transcribing Greek municipal councils with speaker and topic identification. It uses Scribe v2 (about 13% WER) and found a Greek-specialized open fine-tune about 3× worse than plain Whisper — [Schema Labs](https://schemalabs.substack.com/p/asr-finetuning)
- **Academic Greek parliament data**: Hellenic Parliament plenary video (from 2015) with verbatim transcripts has been used to build Greek ASR training sets ([arXiv 2301.00304](https://arxiv.org/pdf/2301.00304)). The GREC corpus (Univ. of Athens thesis, 2023) has about 120 h of parliament speech, with diarization added to a Kaldi server ([UoA Pergamos](https://pergamos.lib.uoa.gr/uoa/dl/object/3262636/file.pdf))
- **Human transcription**: Sound and Visual offers human-edited Greek transcription of proceedings (a manual service, not ASR) — [soundandvisual.gr](https://www.soundandvisual.gr/apomagnitofonisi-el)

### Inferences
- There is no evidence of a Greek-only commercial ASR that outperforms ElevenLabs, Speechmatics or Soniox. The Greek ecosystem mostly builds on global models (Scribe, Whisper fine-tunes).
- The Parliament corpus (2015+ video with verbatim transcripts) is a realistic source of free Greek fine-tuning and eval data for local models.

### Gaps
- The AI@Parliament vendor is unknown; the tender documents (διακήρυξη) were not searched.
- Not searched: Greek court transcription systems, Omilia (Greek/Cypriot conversational AI), ILSP/Athena RC Greek ASR services.
- Aseto AI's whitepaper metrics were not retrieved.
