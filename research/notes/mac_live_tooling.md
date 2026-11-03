# Live Greek speech-to-text on Apple Silicon (M1 Pro, 32 GB, macOS 14 Sonoma): runtimes, streaming, diarization, audio capture, apps

Research date: 2026-11-03. About 24 tool calls. Most numbers come from vendor or community self-benchmarks on M1 Ultra/M4 Pro hardware. **No source I found gives an M1 Pro number for large-v3-turbo**, so every M1 Pro speed below is an estimate, not a measurement.

## Runtimes for Apple Silicon and their speed (whisper.cpp, WhisperKit, mlx-whisper, faster-whisper, parakeet-mlx, FluidAudio, Voxtral)

### Takeaway
On Apple Silicon, mlx-whisper ran large-v3-turbo about 2x faster than whisper.cpp in a January 2026 benchmark. WhisperKit (MIT, needs macOS 14.0+) and FluidAudio (Apache-2.0, Neural Engine) are the native Swift options. Parakeet TDT 0.6b v3 supports Greek and is very fast, but its Greek FLEURS WER of 20.7% is weak. Whisper models remain the main path for Greek accuracy. Voxtral's realtime model does not clearly list Greek.

### Cited Findings
**mlx-whisper vs whisper.cpp**
- Jan 9, 2026 benchmark (hyperfine, 10 runs) of large-v3-turbo on the same file: whisper.cpp took 26.704 s ± 0.625 s and mlx_whisper took 13.135 s ± 0.280 s, a **2.03 ± 0.06x** speedup for MLX. The 2024 run of the same pair gave 1.78x. Models: `mlx-community/whisper-large-v3-turbo` and `ggml-large-v3-turbo.bin`. The post states neither the hardware nor the audio length, and passes no whisper.cpp Metal/CoreML flags. — [Bill Mill, Jan 2026](https://notes.billmill.org/dev_blog/2026/01/updated_my_mlx_whisper_vs._whisper.cpp_benchmark.html)
- A search summary of the same post says it ran on an "M1 Ultra 32GB". I could not confirm that on the fetched page, so treat the hardware as unverified. — [Bill Mill](https://notes.billmill.org/dev_blog/2026/01/updated_my_mlx_whisper_vs._whisper.cpp_benchmark.html)
- The same author says turbo "seems to be somewhat slower than the distil models" (no numbers). — [Bill Mill](https://notes.billmill.org/dev_blog/2026/01/updated_my_mlx_whisper_vs._whisper.cpp_benchmark.html)
- **Conflicting evidence on M1 Pro (14", large-v2 8-bit, 2024):** whisper.cpp beat mlx-whisper (1m00 vs 1m36). The same tester later reported mlx-whisper 30–40% faster on a better test. — via [Simon Willison, Aug 2024](https://simonwillison.net/2024/Aug/13/mlx-whisper) / [Bill Mill 2024](https://notes.billmill.org/link_blog/2024/08/mlx-whisper.html)
- A community benchmark on an M1 Max (64 GB) reports large-v3-turbo at about **14.3x real-time** (batch, MLX-based stack, not streaming). — [vllm-mlx benchmarks](https://vllm-mlx.is-a.dev/zh/benchmarks/audio/)

**WhisperKit (Argmax)**
- Requires **macOS 14.0+**. SpeakerKit needs macOS 13+, and TTSKit needs macOS 15+ (TTS is irrelevant here). License is MIT. — [WhisperKit GitHub](https://github.com/argmaxinc/WhisperKit)
- Models include `large-v3-v20240930_turbo` and the compressed `large-v3-v20240930_626MB`, which is recommended for multilingual accuracy. — [WhisperKit GitHub](https://github.com/argmaxinc/WhisperKit)
- Published LibriSpeech (English) WER for turbo is about 2.44%, and about 2.49% for the ~1 GB compressed variant. The "_turbo" suffix marks optimisations that enable streaming. — [argmaxinc/whisperkit-coreml README](https://huggingface.co/argmaxinc/whisperkit-coreml/blob/49ff53dfe561f4556c6279bfa23ac39d01e2e833/README.md)
- The paper (arXiv 2507.10860, Jul 2025) claims **0.46 s latency** with accuracy matching or beating gpt-4o-transcribe and Deepgram nova-3. The device and model behind that figure were not confirmed. — [WhisperKit paper](https://arxiv.org/html/2507.10860v1)

**FluidAudio (FluidInference)**
- Swift SDK. Docs list **macOS 14+ / iOS 17+**, Apple Silicon recommended, Swift 5.10+. — [FluidAudio docs](https://docs.fluidinference.com) / [CocoaPods](https://cocoapods.org/pods/FluidAudio)
- Some newer models require **macOS 15**: Kokoro ANE v3, Phonon 31m, Parakeet Redux, Phonon-2, Phonon-2.5, Bark 3.0. Package traits need Swift 6.2 / Xcode 26. The core Parakeet v3 ASR and diarization are not on that list. — [FluidAudio GitHub](https://github.com/FluidInference/FluidAudio)
- ASR runs on the Neural Engine. The default is Parakeet TDT v3 (0.6b, 25 European languages), and "Parakeet Ultra" is described as "more accurate than v3 at the same speed". Speed is **~190x real-time on M4 Pro** (1 h of audio in ~19 s). No M1 numbers are given. Repo license is Apache-2.0. — [FluidAudio GitHub](https://github.com/FluidInference/FluidAudio)
- Apps built on it include VoiceInk, Spokenly, Hex, Meeting Transcriber, OpenOats, Summit AI Notes, Goodmeet, MimicScribe, TypeWhisper, Muesli and ~40 others. — [FluidAudio GitHub](https://github.com/FluidInference/FluidAudio)

**Parakeet TDT 0.6b v3 (also used by parakeet-mlx, meetcap, tome, Meetily)**
- Its 25 languages **include Greek (el)**. Greek **FLEURS WER is 20.70%**, the only Greek metric reported. License is CC-BY-4.0, commercial use allowed. Released Aug 14, 2025. — [HF model card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)

**Voxtral (Mistral)**
- Mistral's language page names Greek **only for TTS**. Transcription models list only counts: realtime-3-0 covers "45 languages and dialects", and mini/small-transcribe-3-0 cover 50, with no lists. — [Mistral docs: languages](https://docs.mistral.ai/studio/audio/specs-and-limits/languages)
- The Voxtral Transcribe 2 / Realtime announcement names 13 strong languages (en, zh, hi, es, ar, fr, pt, ru, de, ja, ko, it, nl). Greek is not among them. Realtime open weights are Apache-2.0 on HF, and the API costs $0.006/min. — [Mistral news](https://mistral.ai/fr/news/voxtral-transcribe-2)
- WhisperLiveKit ships a `voxtral-mlx` backend, recommended as its native MLX path, and claims Voxtral handles "100+ languages". That claim is not backed by Mistral's own docs. — [WhisperLiveKit GitHub](https://github.com/QuentinFuxa/WhisperLiveKit)

### Inferences
- Estimate, not measured: on an M1 Pro (16-core GPU, about half an M1 Max), mlx-whisper large-v3-turbo should run well above 1x real-time, roughly 5–8x, extrapolated from the M1 Max 14.3x figure. That leaves headroom for live transcription plus diarization.
- For Greek, Whisper large-v3/turbo is likely more accurate than Parakeet v3 (20.7% FLEURS WER). Parakeet is the speed option. Another researcher should verify Whisper's Greek WER.
- Voxtral Realtime should be treated as **unproven for Greek** until tested.
- macOS 14 works for WhisperKit and FluidAudio's core ASR and diarization. Only some newer FluidAudio models need macOS 15.

### Gaps
- No primary-source benchmark of M1 Pro speed for large-v3 or large-v3-turbo on any runtime.
- **whisper.cpp:** I did not fetch the README, so the Metal/CoreML build flags, the `whisper-stream` example and its latency, and current speed are unsourced here. From training knowledge, whisper.cpp supports Metal by default and CoreML for the encoder, and ships a `stream` example (sliding window, roughly 0.5–3 s step). Verify before relying on it.
- **faster-whisper:** I found no source confirming it is CPU-only on macOS. My understanding is that CTranslate2 has no Metal backend, so it would run on CPU only, which makes it slow on Mac. Unverified.
- **lightning-whisper-mlx and parakeet-mlx:** no benchmarks fetched.
- Greek WER on Whisper large-v3 vs turbo was not researched here (out of scope).

## Streaming / live transcription: true live support and typical latency

### Takeaway
WhisperLiveKit (Apache-2.0) is the most complete open-source live server for Mac: SimulStreaming or LocalAgreement policies, an mlx-whisper or voxtral-mlx backend, Sortformer live diarization, and a native macOS client. WhisperKit has `--stream` in its free CLI, but sells "real-time transcription with speakers" in its Pro SDK. FluidAudio's dedicated streaming ASR (Parakeet EOU) is English-only, so live Greek there means sliding-window Parakeet v3.

### Cited Findings
- **WhisperLiveKit**
  - License is Apache-2.0. Default policy is SimulStreaming (AlignAtt); WhisperStreaming LocalAgreement is an option.
  - Backend auto-selects mlx-whisper on macOS, then faster-whisper, then vanilla Whisper. Other backends include `voxtral-mlx`, plus `canary` (25 EU languages, CPU-only on Apple Silicon).
  - Interfaces: a browser UI, a WebSocket `/asr` endpoint (raw PCM via `--pcm-input`), an OpenAI-compatible REST endpoint, a Chrome extension that captures web-page audio, and a native SwiftUI client at `macos/WhisperLiveKitMac`.
  - Language defaults to `auto`, which "tends to bias toward English for Whisper", so **set `--language el` explicitly**.
  - No latency figures in milliseconds. Published benchmarks are on an H100 only, and the authors ask for Apple Silicon results.
  - [WhisperLiveKit GitHub](https://github.com/QuentinFuxa/WhisperLiveKit) / [PyPI](https://pypi.org/project/whisperlivekit/)
- A third-party guide claims 200–800 ms first-word latency for WhisperLiveKit and ~2 GB extra memory for Sortformer. This is unverified against primary sources. — via search summary of [WhisperLiveKit docs index](https://docsearch.algolia.com/mcp/docs/repo/quentinfuxa/whisperlivekit)
- An unverified docs-index claim says SimulStreaming with the MLX encoder takes ~0.07 s vs 0.35 s for base.en on an M4. — [WhisperLiveKit docs index](https://docsearch.algolia.com/mcp/docs/repo/quentinfuxa/whisperlivekit)
- **WhisperKit:** `argmax-cli transcribe --model-path ... --stream` does live mic streaming. The local server supports SSE for real-time output. The **Pro (paid) SDK** adds "Real-time transcription with speakers", custom vocabulary, and an "Argmax Local Server for non-native apps". — [WhisperKit GitHub](https://github.com/argmaxinc/WhisperKit)
- **FluidAudio:**
  - Streaming ASR is **Parakeet EOU (120m, English only)** with end-of-utterance detection.
  - "Nemotron 3.5 Transcribe Lite" is listed for meeting transcripts with speakers. Language is not stated.
  - Generic real-time streaming runs via `SlidingWindowAsrManager`.
  - [FluidAudio GitHub](https://github.com/FluidInference/FluidAudio)

### Inferences
- The most realistic open-source **live Greek** stack on macOS 14 is WhisperLiveKit with the mlx-whisper backend, `--language el`, large-v3-turbo, and Sortformer diarization. Second choice is FluidAudio's SlidingWindowAsrManager with Parakeet v3 (Greek-capable, fast, lower accuracy) plus LS-EEND diarization, but that requires writing Swift.
- Expect live latency of roughly 1–3 s for Whisper-class models with LocalAgreement or SimulStreaming. This is an estimate from the policy design, not measured on M1 Pro.

### Gaps
- No measured live-latency numbers on M1/M2 for any stack.
- I did not verify the whisper.cpp `stream` example or the WhisperLive (Collabora) / ufal whisper_streaming repos directly.
- It is unknown whether the WhisperLiveKit macOS client captures system audio or only the mic.

## Speaker diarization on Mac: pyannote, FluidAudio, WhisperX, sherpa-onnx; accuracy, speed, Greek

### Takeaway
pyannote community-1 is the accuracy reference: 7.2% DER on VoxConverse at ~24x real-time on M4 Pro MPS, but only 1.5–2x on CPU. FluidAudio runs the same community-1 pipeline on the Neural Engine at 65–122x, and adds live diarization (LS-EEND, up to 10 speakers). Diarization models work on voice acoustics, so Greek is not a blocker. Live diarization is the weak spot: Sortformer caps at 4 speakers.

### Cited Findings
- **Pyannote community-1 speed and accuracy:**
  - On **M4 Pro MPS: 7.2% DER, 24x real-time** on VoxConverse dev (216 files, 1217.8 min), macOS 26.3. — [speakrs README](https://docs.rs/crate/speakrs/latest/source/README.md) (a competitor's benchmark)
  - FluidAudio's figures: pyannote community-1 runs at **1.5–2x RTFx on CPU and 20–25x on MPS**, while FluidAudio on the Neural Engine runs at **65–122x RTFx**. — [FluidAudio benchmarks](https://docs.fluidinference.com/reference/benchmarks.md)
  - speakrs (Rust) claims 312–912x real-time on Apple Silicon. — [speakrs](https://docs.rs/crate/speakrs/latest/source/README.md)
- **FluidAudio diarization:**
  - Offline: pyannote community-1 (powerset segmentation, WeSpeaker, VBx).
  - Online default: **LS-EEND**, up to 10 speakers, 100 ms frame updates, 900 ms tentative preview.
  - Online alternative: **Sortformer**, max 4 speakers, NVIDIA Open Model License.
  - Legacy online: pyannote 3.1, "much slower", needs ≥5 s chunks.
  - The README carries no DER numbers, and CI does not gate on DER.
  - [FluidAudio GitHub](https://github.com/FluidInference/FluidAudio)
- **WhisperKit SpeakerKit** (free, MIT) runs pyannote v4 (community-1) on-device via `argmax-cli diarize`, needs macOS 13+, and can be combined with transcription. — [WhisperKit GitHub](https://github.com/argmaxinc/WhisperKit)
- **WhisperLiveKit** live diarization: Sortformer by default (1–4 speakers via `--sortformer-max-speakers`). Diart is marked "Not recommended" and needs pyannote gating plus Python 3.11/3.12. — [WhisperLiveKit GitHub](https://github.com/QuentinFuxa/WhisperLiveKit)
- **meetcap** offers sherpa-onnx or Vosk for speaker identification alongside Parakeet or Whisper. **ownscribe** uses pyannote (needs an HF token). — [meetcap PyPI](https://pypi.org/project/meetcap/) / [ownscribe PyPI](https://pypi.org/project/ownscribe/0.13.1/)
- **MacWhisper diarization quality** (one reviewer's own test, not a benchmark): 91.3% attribution accuracy for 2 speakers and 79.6% for 3. The feature is labelled beta. — via [getvoibe MacWhisper review](https://www.getvoibe.com/resources/macwhisper-review/) / [usevoicy review](https://usevoicy.com/blog/macwhisper-review/)
- **HushScribe**: post-session diarization works well with headset mics but degrades with laptop speakers and crosstalk. — [HushScribe GitHub](https://github.com/drcursor/HushScribe)

### Inferences
- Diarization is effectively language-independent, because the models use speaker embeddings and segmentation on acoustics, not words. That makes Greek fine. This is an inference; no source tested Greek specifically.
- Recommended split: live captions with rough speaker labels (LS-EEND or Sortformer), then **re-run offline community-1 after the meeting** for an accurate "who said what" transcript. Most local apps already diarize post-session (HushScribe, tome, ownscribe).
- Practical trick: record **mic and system audio as separate channels**, as Boswell does. "Me vs. them" then comes free with no diarization, which only needs to split the remote side.
- Estimate: pyannote community-1 on M1 Pro MPS should be roughly 8–15x real-time, scaled down from the M4 Pro's 24x. Fine post-meeting, marginal for live use.

### Gaps
- No DER numbers on Greek audio, and no M1-specific diarization speeds.
- WhisperX on Apple Silicon was not researched. Its alignment step needs a Greek wav2vec2 model, which may or may not exist. Unverified.

## Capturing meeting audio (Teams/Zoom/Meet) on macOS 14: BlackHole, Core Audio taps, ScreenCaptureKit

### Takeaway
On macOS 14.4+, the Core Audio process tap API captures system or per-app audio natively, with no virtual driver. BlackHole remains the fallback for any app and any macOS version. **Make sure Sonoma is updated to at least 14.4.**

### Cited Findings
- **Core Audio taps:**
  - Apple's API for capturing audio from other apps or the whole system, with user permission. Flow: `AudioHardwareCreateProcessTap` with a `CATapDescription`, wrapped in an aggregate device, then start the IO proc. — [AudioCap README](https://github.com/insidegui/AudioCap)
  - Apple's sample says macOS **14.2+**; AudioCap cites **14.4**. One source says the API appeared in 14.2 but is only publicly supported from 14.4. — [Apple docs](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps.md) / [AudioCap](https://github.com/insidegui/AudioCap)
  - Taps can be public or private, and can mute the original output. There is no public API to request or check audio-recording permission. — [Apple docs](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps.md) / [AudioCap](https://github.com/insidegui/AudioCap)
- **BlackHole:**
  - To hear audio and capture it, route output to a **Multi-Output Device** (speakers + BlackHole). To combine mic and system audio, create an **Aggregate Device** (mic + BlackHole). — [BlackHole wiki](https://github.com/ExistentialAudio/BlackHole/wiki/Use-BlackHole-as-a-Virtual-Microphone)
  - Enable drift correction on every device except the clock source, and make the mic the clock source. — [BlackHole](https://github.com/existentialaudio/blackhole) / [meetcap](https://pypi.org/project/meetcap/)
  - **AirPods** mics run at a lower sample rate and should not be the clock or primary device. — [meetcap](https://pypi.org/project/meetcap/)
- **Zoom example:** Speaker is set to the Multi-Output Device with BlackHole, input to the Aggregate Device. Boswell records a stereo file (mic on L, BlackHole on R) and transcribes each channel with Whisper. It requires Apple Silicon and macOS 14+. — [Boswell GitHub](https://github.com/rohandahale/boswell)
- **Whisper hallucination:** near-silent audio makes Whisper emit "Thank you" or "test test test…" loops. A VAD front-end is needed. — [meetcap](https://pypi.org/project/meetcap/)
- **Granola:** works best on macOS 14.2+, minimum macOS 13. — [Granola help](https://help.granola.ai/article/setting-up-granola-for-the-first-time)

### Inferences
- If the app being used has native tap support (Meetily, MacWhisper, Granola-style apps), no BlackHole is needed on 14.4+. For DIY Python stacks such as WhisperLiveKit, BlackHole plus an Aggregate Device is the simplest way to feed meeting audio in.
- ScreenCaptureKit audio capture also exists on macOS 13+. This is training knowledge, not sourced here.

### Gaps
- No source fetched on ScreenCaptureKit audio specifics.
- Which method each named app uses (Meetily, MacWhisper, Char) was not found.

## Ready-made local meeting apps supporting Greek (local, open source, live, diarization, cost)

### Takeaway
No app was found with documentation explicitly confirming **local + live + diarization + Greek on macOS 14**. Closest candidates:
- **MacWhisper Pro** (paid, roughly €59–80 one-time): local Whisper, meeting recording, live transcription, beta diarization.
- **Meetily Community** (MIT, free): live transcription with Whisper or Parakeet. Diarization looks like a $10/month PRO feature.
- **Char/Hyprnote** (now "anarlog", MIT): live transcription is documented only on its cloud Pro tier.

Granola is cloud-based and lists no Greek. Dictation apps (Superwhisper, VoiceInk) are not meeting diarizers.

### Cited Findings
- **MacWhisper**
  - Pro adds meeting recording and detection (Zoom, Teams, Webex, Skype, Discord), real-time transcription from mic or system audio, and speaker diarization (beta). — [usevoicy review](https://usevoicy.com/blog/macwhisper-review/) / [getvoibe pricing](https://www.getvoibe.com/resources/macwhisper-pricing/)
  - Pro price is inconsistent across sources: €59, ~$69, or $79.99 lifetime, with a $6.99/month option. Gumroad and App Store prices differ. — [getvoibe pricing](https://www.getvoibe.com/resources/macwhisper-pricing/) / [makerstack](https://makerstack.co/reviews/macwhisper-review/)
  - One reviewer calls live dictation "secondary to file transcription". — [getvoibe review](https://www.getvoibe.com/resources/macwhisper-review/)
  - MacWhisper 12 introduced on-device speaker recognition. — [heise](https://heise.de/-10322414) / [CO/AI](https://getcoai.com/news/macwhisper-12-brings-first-ever-on-device-speaker-recognition-to-mac-transcription)
- **Meetily (Zackriya-Solutions/meeting-minutes)**
  - MIT, local by default: Whisper or Parakeet with Metal/CoreML on Apple Silicon, and a "live transcript … as it happens". Captures mic and system audio simultaneously.
  - Summaries can use Ollama (local) or cloud LLMs; only text is sent.
  - Speaker identification is listed as a **PRO** feature ($10/user/month annual, 14-day trial), even though the About blurb mentions diarization.
  - Version 0.4.1, pre-release. Minimum macOS and languages not stated.
  - [Meetily GitHub](https://github.com/Zackriya-Solutions/meeting-minutes)
- **Char / Hyprnote → anarlog**
  - The repo says it "started as Hyprnote and briefly used the char name". anarlog is the MIT open-source local-first notetaker; char.com is the team's new productivity app. Code in `enterprise/` is commercially licensed.
  - Local options named: Soniqo and Apple Speech "when available". Live transcription is stated only for the **Pro (Cloud)** route, using Soniox.
  - A diarization crate exists, but it is not marketed as a feature. No bot joins the call.
  - [fastrepl/hyprnote GitHub](https://github.com/fastrepl/hyprnote)
  - Char docs say 45+ languages, with local Whisper at "50+". Greek is not named explicitly. — [Char FAQ](https://char.com/docs/faq/features) / [Char languages](https://char.com/docs/developers/languages)
- **Granola:** multi-language transcription uses "our transcription provider's" languages. Older lists name en, fr, de, es, it, pt, nl, ja, ru, hi, and Greek appears on no list. The troubleshooting page mentions a "connection to our transcription service", which points to cloud transcription. — [Granola multi-language](https://docs.granola.ai/help-center/customising-granola/multi-language) / [Granola transcription issues](https://docs.granola.ai/article/transcription-issues)
- **Open-source CLIs and small apps:**
  - **HushScribe** (MIT): local, post-session diarization, **requires macOS 26+ (dealbreaker)**. — [HushScribe](https://github.com/drcursor/HushScribe)
  - **meetcap**: offline CLI, Parakeet TDT or Whisper, sherpa-onnx/Vosk speaker ID, BlackHole capture. — [meetcap](https://pypi.org/project/meetcap/)
  - **ownscribe**: local CLI, pyannote diarization. — [ownscribe](https://pypi.org/project/ownscribe/0.13.1/)
  - **tome**: Obsidian-oriented, Parakeet-TDT v3 (25 EU languages, so Greek), post-session pyannote. — [tome](https://www.sourcepulse.org/projects/27165723)
  - **Kleoth** (Apache-2.0): local by default, optional ElevenLabs cloud. — [Kleoth](https://github.com/ofcRS/kleoth)
  - **Agrapha**: ELv2 source-available, pyannote. — [Agrapha](https://github.com/tstapler/agrapha)
  - **Boswell**: macOS 14+, stereo mic/system channels, Whisper. — [Boswell](https://github.com/rohandahale/boswell)
- **Dictation apps:**
  - **Buzz**: free, open source, local; files and recordings, SRT/VTT export. No live system-audio capture or diarization found; one source says diarization is in MacWhisper Pro, not Buzz. — [buildbetter](https://blog.buildbetter.ai/best-superwhisper-alternatives-in-2026-top-local-ai-dictation-tools-for-mac/) / [lumevoice](https://lumevoice.com/fr/blog/meilleures-alternatives-macwhisper/)
  - **VoiceInk**: dictation-focused, uses FluidAudio. Open-source status conflicts across sources (GPL v3 vs "No"), and price reports differ ($39.99 one-time vs $25 Solo). — [getvoibe](https://www.getvoibe.com/resources/superwhisper-vs-voiceink/) / [FluidAudio showcase](https://github.com/FluidInference/FluidAudio)
  - **Superwhisper**: $249.99 lifetime, has "meeting recording", no confirmed speaker identification. — [getvoibe Superwhisper review](https://www.getvoibe.com/resources/superwhisper-review/.) / [buildbetter](https://blog.buildbetter.ai/best-superwhisper-alternatives-in-2026-top-local-ai-dictation-tools-for-mac/)

### Inferences
- Any app running Whisper large-v3/turbo or Parakeet v3 locally can in principle transcribe Greek, provided the UI allows selecting Greek or auto-detect. Test with a 2-minute Greek clip before committing.
- Best fit for "free + open source + live + diarization + Greek": **WhisperLiveKit (DIY) or Meetily Community**. With Meetily, you may need to do diarization post-meeting yourself, because it is a PRO feature. Best paid, no-fuss option: **MacWhisper Pro** (one-time payment, local, meeting detection, beta diarization).
- Avoid for this goal: Granola (cloud, no Greek listed), HushScribe (macOS 26), Superwhisper and VoiceInk (dictation-oriented, no diarization confirmed).

### Gaps
- Not covered: Aiko, Notesnook, Spokenly, OpenOats, Summit AI Notes, Goodmeet, MimicScribe (no time left in the call budget).
- Not found for Meetily, MacWhisper or anarlog: the minimum macOS version, and whether Greek is selectable in the UI.
- MacWhisper's official Gumroad page returned no content, so its features and price rest on third-party reviews.
- Whether the free WhisperKit CLI plus SpeakerKit can produce live speaker-labelled output, or only offline output, is unverified. Argmax markets "real-time transcription with speakers" as Pro-only.
