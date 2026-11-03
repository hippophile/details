# detAIls v2 — Audio engineer design

## 1. TLDR

- **Master = WAV PCM 16-bit 16 kHz while recording (crash-safe, append-only), converted to FLAC when you press Stop.** Lossless, STT-native, plays in QuickTime/Finder (FLAC: from memory, test it).
- **Export on demand with ffmpeg:** M4A (AAC 64 kbps) for sharing/playing, WAV for tools. Do not use MediaRecorder/WebM as master.
- **Mic now; meeting audio later as a 2nd channel (L = me, R = them).** A browser page can only get *tab* audio. Desktop Teams/Zoom needs a small Swift helper (Core Audio tap, macOS 14.4+).
- **Keep echo cancel / noise suppression / auto-gain OFF** (as v1 does). Use a headset. Silero VAD only as a pre-filter for STT, never on the master.
- **Import:** drop any audio/video file, keep the original untouched, ffmpeg makes `master.wav` 16 kHz (mono, or stereo if the channels differ).

## 2. Decisions (with reasons)

### 2.1 Formats: pros and cons

Sizes computed by me (bytes/s x 3600). "Measured" = one 12 s clip of your own mic, `sessions/20261103-214329.wav`, ffmpeg defaults. One short clip, so measured ratios are indicative only.

| Format | Per hour | 2.5 h | STT quality (Whisper) | Browser MediaRecorder (Brave/Chromium) | Edit | QuickTime / Finder |
|---|---|---|---|---|---|---|
| **WAV 16 kHz 16-bit mono** | 115 MB | 288 MB | Reference (no loss). | No. (We write it ourselves from the AudioWorklet, as v1.) | Perfect, any tool | Yes |
| WAV 16 kHz stereo (L me / R them) | 230 MB | 576 MB | Reference | No (same) | Perfect | Yes |
| WAV 48 kHz 16-bit mono | 346 MB | 864 MB | Same as 16 kHz: Whisper resamples to 16 kHz inside (mel features, see openai/whisper `audio.py` `SAMPLE_RATE = 16000`) | No | Perfect | Yes |
| **FLAC 16 kHz mono** | ~70 MB (measured 60% of WAV, so ~69 MB/h) | ~173 MB | Identical to WAV (lossless) | No (Chromium MediaRecorder does not offer FLAC; unverified for current versions) | Good, lossless | FLAC plays in macOS 11+ (from memory, not verified here) |
| Opus 32 kbps (WebM/Ogg) | 14.4 MB | 36 MB | Small loss. Opus is about 2% relative WER above lossless in one vendor test; flat down to 32 kbps in another test, worse at 16 kbps and below (sources below). No Whisper-specific benchmark found. | **Yes**, `audio/webm;codecs=opus` (Chromium; Chrome has no Ogg) | Re-encode on edit; WebM from MediaRecorder lacks duration/seek until remuxed | **No** for .webm/.ogg/.opus (from memory, test) |
| AAC / M4A 64 kbps | 28.8 MB | 72 MB | Small loss, similar to Opus (no Whisper data found) | Only if the OS offers an encoder; Chrome writes MP4 only then. Check `isTypeSupported` | Re-encode on edit | **Yes**, best compatibility |
| MP3 64 kbps | 28.8 MB | 72 MB | Worst of the lossy set: about 10% relative WER above lossless in one vendor test (IBM, Watson, not Whisper) | No | Re-encode on edit | Yes |

Measured on the 12 s clip (WAV 384 KB): FLAC 231 KB (60%), AAC-64k 109 KB (container overhead large on short clips), MP3-64k 97 KB, Opus-32k 43 KB.

**Does lossy hurt Whisper?** Honest answer: **no solid Whisper-specific evidence either way.** I found no rigorous benchmark of Whisper WER by codec/bitrate.
- Vendor test (IBM Watson, not Whisper): WAV/FLAC best, Opus about +2% relative WER, MP3 about +10% relative. [IBM](https://medium.com/ibm-data-ai/why-the-audio-compression-format-impacts-the-speech-to-text-transcription-accuracy-84da6438024c)
- Vendor test (AmiVoice, not Whisper): Opus flat from 256 down to 32 kbps; drops at 16 and 6 kbps. [AmiVoice](https://acp.amivoice.com/en/blog/2025-07-29/)
- Academic (multi-channel ASR, not Whisper): relative WER +2.4% at 128 kbps/channel, +6.6% at 32, +12.6% at 16. [arXiv 2106.07994](https://arxiv.org/pdf/2106.07994)
- Our 12 s clip is too short to test this. **Cheap proof we should run:** same take, encode to Opus 32k / AAC 64k / MP3 64k, run Whisper large on each, compare WER with `eval/score.py`.
- Why it matters for your data: your errors are English product names (Gemma, n8n). Codec artefacts hit short, quiet consonants first. Lossless master avoids the question.

**Recommendation**
- **Master:** WAV during recording, FLAC after Stop (about 40% saved, no loss, ffmpeg and most tools read it).
- **Export (on request):** M4A AAC 64k (play anywhere on Mac), WAV (tools), optional MP3 128k. Opus only if you want smallest size and never open it in QuickTime.
- **Sample rate:** 16 kHz. Whisper, Parakeet and pyannote all use 16 kHz input, so 48 kHz only costs 3x disk. Trade-off: bandwidth stops at 8 kHz, fine for speech, weaker for music/listening. Make it a config value `RECORD_SAMPLE_RATE` (default 16000).

### 2.2 Crash-safe recording

Design: the server writes the file while you speak. The browser is just a microphone.

```
worklet (100 ms int16) --WS--> server: append to sessions/<id>/rec.wav (flush each chunk)
                               every 5 s: fsync + patch WAV header sizes (offsets 4 and 40)
                               meta.json (atomic write): {status: "recording", started, rate, channels}
Stop    -> patch header, status "done", ffmpeg -> master.flac, verify duration, keep rec.wav until verified
WS drop -> status "interrupted"; server finalizes after 60 s grace (tab closed = file still saved)
Startup -> scan sessions/*/meta.json with status "recording": fix header from file size
           (truncate to whole frames), mark "recovered", offer in Analyse list
```

- **Loss window:** about 100 ms chunk + 5 s of unsynced OS buffer if the *whole Mac* dies. Closed tab or killed server loses at most the last chunk (estimate, not measured).
- **Server dies, tab alive:** browser keeps unsent chunks in memory (115 MB/h mono; a 10 min buffer is 19 MB) and reconnects with the same session id. Gap = only the downtime.
- **Header patch** instead of the "unknown length" trick (0xFFFFFFFF): Python `wave` and some tools reject it; patching two integers is simple. Python `wave` limit is 4 GB: stereo 16 kHz reaches that after about 9 h. Fine.
- **Mac sleep / lid closed:** AudioContext suspends and samples are lost silently. Timeline = sample count, not wall clock. Log `visibilitychange`/suspend events with wall-clock times in meta.json so the gap is visible. Tell the user in the UI to prevent sleep (`caffeinate -dis` run by the server for the session length; what + why for him: macOS command that stops idle sleep while it runs).
- Storage for a 2.5 h meeting: WAV 288 MB (mono) / 576 MB (stereo) during recording, FLAC about 170 / 350 MB after (estimate from the 60% measured ratio).

### 2.3 Capture sources on macOS 14.6

| Source | From a browser page? | Needs native helper? | Notes |
|---|---|---|---|
| Mic | Yes, `getUserMedia` | No | Today. Choose device with `enumerateDevices` (AirPods run at low sample rate, avoid for long meetings: [meetcap](https://pypi.org/project/meetcap/)) |
| Meeting in a **browser tab** (Meet, Teams web) | Yes, `getDisplayMedia({audio:true})` and tick "share tab audio" | No | Chromium on macOS shares **tab** audio only, not whole-system audio (system audio is Windows/ChromeOS only; from my knowledge, test in Brave). Picker click needed every time. Tab stays in the picker; user must not close it |
| Meeting in **desktop app** (Teams, Zoom) | **No** | **Yes** | Browser cannot see other apps' audio. Options below |
| Test scripts being read in v2 page | n/a | n/a | Mic only |

Native options for desktop apps:

| Option | Pros | Cons |
|---|---|---|
| **Swift CLI with Core Audio process tap** (`AudioHardwareCreateProcessTap` + `CATapDescription`, macOS 14.2+, publicly supported from 14.4) | No driver install. Per-app or system-wide. Apple sample + [AudioCap](https://github.com/insidegui/AudioCap) show the code. [Apple docs](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps) | Needs Xcode/Swift (new for you). Permission prompt ("Audio Recording"/system audio), no public API to check it. How a *CLI binary* gets the TCC permission (terminal vs signed .app) is unverified: must be tested |
| BlackHole + Multi-Output + Aggregate device | Works with any app, no code. [BlackHole wiki](https://github.com/ExistentialAudio/BlackHole/wiki/Use-BlackHole-as-a-Virtual-Microphone) | Manual setup in Audio MIDI Setup; you change the system output device each time; kernel-level driver install; easy to forget and record silence |
| ScreenCaptureKit audio | Exists on macOS 13+ (training knowledge, not sourced here) | Screen-recording permission, heavier |

**Recommendation:** Swift CLI `details-tap` (Core Audio tap), phase 2. It prints raw 16 kHz mono s16 PCM to stdout (or POSTs to the server WS) with a timestamp of its first sample. Phase 1 stays mic only. Phase 1.5: tab-audio checkbox in the Record page (no native code), if the user mostly uses Meet in Brave.

**Stereo L = me, R = them**
- Server writes both streams into one 2-channel WAV. Whisper gets each channel separately (or mix for a single pass), and "who talked" starts with a free me-vs-them split; diarization (pyannote) only has to split the remote side. Same trick as [Boswell](https://github.com/rohandahale/boswell).
- **Clock drift:** mic and tap have different clocks. Estimate, not measured: 50 ppm drift = 0.45 s over 2.5 h. Fix: server aligns by the timestamp of each chunk and pads/trims, or the helper captures both mic and tap in one Aggregate Device with drift compensation (single clock). Start with timestamp alignment, check with a clap test.
- **Bleed:** with speakers, the other side leaks into the mic (L). A headset avoids it. Then L contains only you.
- Mono-compat: Analyse page can downmix to mono for models that need it.

### 2.4 Signal chain

```
mic -> getUserMedia (EC/NS/AGC off, channelCount 1)
    -> AudioContext({sampleRate: 16000})  (browser resamples with a proper filter)
    -> AudioWorklet: meter (RMS, peak) + int16 100 ms chunks -> WS
```

| Item | Decision | Reason |
|---|---|---|
| Echo cancellation, noise suppression, AGC | **OFF** (as v1) | Chrome processes these for calls, not for ASR. NS can smear consonants; AGC pumps quiet pauses up and invites hallucinations. **No Whisper-specific test found**, so this is a judgement call, supported only by the fact that raw is the lossless-info option. Chrome docs on how they interact: [Chrome blog](https://developer.chrome.com/blog/disabling-hardware-noise-suppression/). **Proof to run:** A/B same 2 min script, on vs off, compare WER |
| Resampling | Use `new AudioContext({sampleRate: 16000})` instead of the current hand-made linear interpolation | **Finding in v1 code:** `live/index.html` WORKLET does linear interpolation 48k to 16k with **no low-pass filter**, so content above 8 kHz aliases into the speech band. Small for speech, but free to fix. Verify by comparing a spectrum of both, I did not measure |
| Clipping | Peak above -1 dBFS shows red (v1 does). Add a **clip counter** and "N clips" in the Stop summary | Clipping cannot be repaired afterwards |
| Level meter | Keep v1 (RMS, peak, silence line). Add: peak-hold, "no signal for 10 s" warning (dead/muted mic), one meter per channel in stereo mode | Dead mic in a 2.5 h meeting is the worst failure |
| Target level | Speech around -30 to -12 dBFS RMS, peaks below -3 dBFS | v1 text bands (-40 quiet, -8 loud) are close; keep |
| VAD | See below | |
| Headset vs laptop mic | **Wired or USB headset/boom mic for meetings**; laptop mic only for quick tests. Avoid AirPods as the recording device | Closer mic = better SNR; headset removes speaker bleed. Post-session diarization degrades with laptop speakers and crosstalk: [HushScribe](https://github.com/drcursor/HushScribe) |

**VAD: Silero vs energy**

| | Energy gate (v1: -45 dB) | Silero VAD |
|---|---|---|
| Cost | zero | small ONNX model, light CPU ([repo](https://github.com/snakers4/silero-vad)) |
| Quality | Fails on steady noise, fans, music; threshold per room | Detects speech, not loudness |
| Use | Keep for the **live** chunker (works, it is already tested in v1) | Use for the **full offline pass** and Analyse page |

- Why VAD at all: Whisper hallucinates on silence/near-silence ("Thank you", "Υπότιτλοι…", loops). The usual fix is Silero VAD + `condition_on_previous_text=False` + dropping segments by `no_speech_prob`/low logprob ([faster-whisper #843](https://github.com/SYSTRAN/faster-whisper/issues/843), [meetcap](https://pypi.org/project/meetcap/)). mlx-whisper has no built-in `vad_filter`, so **we cut speech regions ourselves** (pad about 300 ms), transcribe each, and add offsets back to timestamps.
- Never cut the master. VAD output is metadata (`vad.json`: list of [start, end]) used only as STT input. This keeps the audio complete for listening and re-runs.
- Keep the JUNK list from `server.py` as a last-resort filter.

### 2.5 Import of external files

```
POST /import (multipart, streamed to disk)  ->  sessions/<id>/original.<ext>   (never modified)
ffprobe -> duration, channels, codec, has video?
ffmpeg -i original -vn -map_metadata -1 -ar 16000 -c:a pcm_s16le [-ac 1] rec.wav -> master.flac
```
- `-ac 1` unless the channels really differ (phone calls recorded stereo with identical channels waste 2x space). Test: compare L and R energy diff with ffmpeg `astats` or a quick numpy check.
- Accept video too (Teams/Zoom `.mp4` recordings): `-vn`.
- No loudness normalisation by default (Whisper's mel features are fixed-scale; I believe level-insensitive within reason, not verified). Optional checkbox "normalise" (`loudnorm`) if a phone clip is very quiet.
- Variable frame rate / odd formats: ffmpeg handles .m4a/.mp3/.ogg/.opus/.flac/.wma/.mp4/.mov.
- ffmpeg is already installed (`/opt/homebrew/bin/ffmpeg`); the server checks `shutil.which("ffmpeg")` (path from env `FFMPEG`, not hardcoded) and shows a clear error if missing.
- Cap: size limit from env `IMPORT_MAX_MB`; stream to disk, not into RAM.

### 2.6 Session folder layout (shared with other roles)

```
sessions/<id>/ meta.json  rec.wav (deleted after FLAC verified; config KEEP_WAV)  master.flac
               original.<ext> (imports)  vad.json  exports/<name>.m4a
```
`sessions/` stays out of git (already true; confirm `.gitignore`).

## 3. Options rejected

| Option | Why not |
|---|---|
| MediaRecorder WebM/Opus as master | Lossy; mono-only per stream; WebM often lacks duration; Opus is 48 kHz internally; no QuickTime. Fine as a quick preview, not for a 2.5 h archive |
| MP3 as master or default export | Worst WER of the lossy set in the one test found; M4A is as compatible and smaller at same quality |
| 48 kHz master | 3x disk, zero STT gain |
| WAV with "unknown size" header | Python `wave`/some tools reject; header patch is simple |
| Browser-side file save (File System Access API / blob download at end) | Whole recording lives in RAM until Stop; a crash loses everything |
| BlackHole as the default | Manual audio routing, driver install, easy to record silence. Keep as documented fallback only |
| Cloud/online VAD or diarization | Violates local-only |
| AEC on "because I use speakers" | The browser's AEC knows only audio the browser plays, not Teams desktop output. Use a headset instead |

## 4. Risks and caveats that change a decision

- **No evidence that lossy hurts or does not hurt Whisper.** Therefore master stays lossless; run the codec A/B before offering Opus/AAC as "good enough for STT".
- **Tab audio only in browser.** If most meetings are in desktop Teams/Zoom, the Swift helper is required, not optional. You are new to Swift/macOS: it is the biggest effort here (estimate: 1-2 days, not measured).
- **TCC permission for a CLI tap is unverified.** Prototype first: 20-line tap that writes 5 s to a file, before designing around it.
- **Core Audio tap needs macOS 14.4+.** Check `sw_vers`; you are on 14.6, fine. Do not rely on 14.2 behaviour.
- **Sleep / lid close drops audio silently.** Needs `caffeinate` and a visible gap log.
- **Clock drift** between mic and tap, up to about 0.45 s over 2.5 h (estimate). Matters for aligning "me" and "them" in the transcript, not for STT.
- **Recording meetings: legal/consent** (GDPR; other people's voices). Add a visible "recording" indicator and a note in the UI. Not a technical risk, but your management will ask.
- **Disk:** `rec.wav` + `master.flac` coexist until verified; 2.5 h stereo needs about 1 GB headroom. Check free space at Start (warn below `MIN_FREE_GB`, env).
- Safari is out of scope (Brave only); WebM/MP4 differences do not matter since we do not use MediaRecorder.

## 5. Open questions (max 5)

1. Where do your real meetings run: **desktop Teams/Zoom** or **in a browser tab**? (decides if the Swift helper is phase 2 or phase 1.5)
2. Is a **16 kHz master** OK, or do you also want to listen to recordings as music-quality audio (then 48 kHz, 3x disk)?
3. Delete `rec.wav` after FLAC is verified (saves 40% disk), or keep both for the first weeks?
4. Do you usually wear a **headset**? If yes, no echo handling is needed at all.
5. Are Opus/WebM exports useful to you, or is "M4A for sharing, FLAC/WAV for archive" enough?

## 6. Proposed OpenSpec change slices (audio angle)

| # | Name | One line |
|---|---|---|
| 1 | `record-streaming-wav` | Server appends WS chunks to `sessions/<id>/rec.wav`, periodic header patch + fsync, `meta.json` status |
| 2 | `record-crash-recovery` | Startup scan fixes interrupted sessions; WS-drop grace finalize; browser reconnect with in-memory buffer |
| 3 | `record-finalize-flac` | On Stop: ffmpeg to `master.flac`, verify duration, optional delete of `rec.wav` (env `KEEP_WAV`) |
| 4 | `record-meter-and-clip` | Clip counter, peak-hold, dead-mic warning, per-channel meters; 16 kHz `AudioContext` instead of linear resampler |
| 5 | `audio-import-ffmpeg` | Drag-and-drop import, ffprobe, normalise to 16 kHz WAV/FLAC, keep original |
| 6 | `audio-export` | Export M4A/WAV/MP3 from master via ffmpeg, filename + download |
| 7 | `audio-vad-prefilter` | Silero VAD regions as STT input, offset-corrected timestamps, `vad.json`, hallucination filters |
| 8 | `codec-wer-experiment` | Script: encode one take to Opus/AAC/MP3, score with `eval/score.py`, and an on/off A/B for EC/NS/AGC (evidence for section 2.1/2.4) |
| 9 | `record-tab-audio` | Optional tab-audio capture via `getDisplayMedia` into the R channel (no native code) |
| 10 | `tap-helper-spike` | 20-line Swift Core Audio tap writes 5 s to a file; confirms permission flow on 14.6 |
| 11 | `record-stereo-me-them` | Swift helper stream + mic merged into stereo with timestamp alignment, clap-test check |

## Sources

- IBM, audio format vs STT accuracy: https://medium.com/ibm-data-ai/why-the-audio-compression-format-impacts-the-speech-to-text-transcription-accuracy-84da6438024c
- AmiVoice sampling/compression test: https://acp.amivoice.com/en/blog/2025-07-29/
- Opus multi-channel ASR: https://arxiv.org/pdf/2106.07994
- MediaRecorder support notes: https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/isTypeSupported_static
- Chrome noise suppression: https://developer.chrome.com/blog/disabling-hardware-noise-suppression/
- Core Audio taps: https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps , https://github.com/insidegui/AudioCap
- BlackHole: https://github.com/ExistentialAudio/BlackHole/wiki/Use-BlackHole-as-a-Virtual-Microphone
- Boswell (stereo mic/system): https://github.com/rohandahale/boswell
- Whisper hallucination/VAD: https://github.com/SYSTRAN/faster-whisper/issues/843 , https://pypi.org/project/meetcap/
- Silero VAD: https://github.com/snakers4/silero-vad
- Internal: `research/notes/mac_live_tooling.md`, `live/index.html`, `live/server.py`
