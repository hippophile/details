# detAIls

> *"Before we begin — let me get the detAIls."*

A local AI listener for meetings. It writes down live Greek + English speech on a Mac, and nothing leaves the machine.

**Also known as**, in an earlier life and several naming meetings:
- **Debrief AI**: sounded like it would make you fill in a form afterwards.
- **AI Memo**: too short to argue with.
- **AI Notes**: too honest. Nobody wants their notes to be *that* obvious.

We went with **detAIls**, because the devil is in them, and now so is the AI.

---

## What it does (v1)

```
🎙 you talk ──► Whisper (MLX, on the Mac GPU) ──► live text on screen, ~1 s behind
■ Stop     ──► one full pass over the whole take ──► cleaner final transcript, saved locally
```
- **Picker:** choose which downloaded model to use: Whisper turbo is fast, Whisper large is more accurate.
- **Level meter:** with a "silence line" that shows what the software treats as speech.
- **Test scripts:** three tabs for 2–3 people, around 30 s, 1 min and 1.5 min, with Greek, English tech words and fillers. One speaker is English-only.
- **Scorer:** shows the % of words wrong, tech words right, numbers right, and fillers kept or dropped.

## Quick start (Apple Silicon Mac)

```bash
make setup     # once: ffmpeg, Python 3.12 venv, packages, Whisper models (~4.6 GB)
make run       # open http://localhost:8765/live → pick a model → 🎙 Mic on
make test      # scorer tests
```
To score a take against the long script: `make record`, then `make score`. Takes saved from the page live in `sessions/`: `make score AUDIO=sessions/<file>.wav`.

## Measured so far (MacBook Pro M1 Pro, 32 GB)

One real take of the 1.5-min "AI plan" script, read by one person in three voices:

| model | words wrong | tech words right | final pass for ~75 s of audio |
|---|---|---|---|
| Whisper large-v3 | **18.8%** | 81% | 9.7 s |
| Whisper large-v3-turbo | 26.7% | 62% | **4.2 s** |

- **Live speed:** live text updates about every 1 s, and each update takes the model 0.7–1.5 s. Turbo uses about 1.8 GB of RAM.
- **Typical misses:** short English product names (Gemma → τζέμα, n8n → E10, triage → Trials, AI Act → IAAC).
- **Caveats:**
  - These numbers come from one speaker and one take each, so they are not a benchmark.
  - The reader added a few words that aren't in the script, and those count as errors.
  - The speed numbers for long meetings in the roadmap are estimates.

## Roadmap (v2)
1. **Save as you go:** write audio to disk every few seconds, so a crash or a closed tab loses nothing. Fix the in-memory copying so 2.5-hour meetings work.
2. **Live and accurate:** turbo for the live text, while large re-does each finished sentence in the background, so Stop is almost instant.
3. **Vocab hint per script:** automatically fill the hint with that script's tech words, then measure whether it helps.
4. **Gemma clean-up:** a local LLM fixes spellings from a glossary, showing changes side by side, never rewriting.
5. **Speaker labels:** "Speaker 1 / 2 / 3" (pyannote or FluidAudio).
6. **Debrief:** summary and action items, built in parts for long meetings. *Yes, the rejected name came back as a feature.*

## Research
`research/greek-stt-candidates.md` compares the Greek speech-to-text options (Whisper, Canary, Parakeet, Parakeet-Greek, cloud APIs) with sources. The raw notes are in `research/notes/`.

## Privacy
Everything runs locally. Recordings and transcripts go to `sessions/`, which is git-ignored and never committed.
