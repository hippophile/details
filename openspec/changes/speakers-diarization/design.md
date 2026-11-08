# Design: speakers-diarization

Only the parts that need arguing. Decision numbers refer to `proposal.md`.

## Pipeline and memory
```
job kind=diarize, steps=[diarize]      (own job; STT and AI jobs are separate and sequential on the GpuLease)
  diarize: release_for_stt() -> write spec.json -> spawn_child(diar_worker) -> read turns.json -> ingest tx -> done
  diar_worker: set offline env, drop HF tokens -> load pyannote (device) -> ffmpeg pipe -> pipeline(hook) -> turns.json
```
Peak memory is the pipeline plus the decoded audio, never summed with an LM Studio model the app loaded, because `release_for_stt()` unloads the app's `details-*` models first and the child exits afterwards (DESIGN.md section 2; models the user loaded themselves stay, see Memory claim). A later "STT + speakers + AI in one click" is the deferred v2.1 item and would only add `diarize` to a `steps` list.

## Why read-time alignment (decision 2)
| | Stored on `transcript_segment.speaker` | **Computed at read time** |
|---|---|---|
| Re-transcribe with another model | needs another diarization or a copy step | works at once, diarization is about audio |
| Fix the overlap rule later | migration + backfill | edit one function |
| Append-only rule for result rows | breaks it (UPDATE) | kept |
| Cost | none per read | O(segments + turns) per request; 60 min is about 600 segments (estimate), trivial |

## Why `diarization_run` has no `stt_run_id`
`design/v2/architect-data.md` also has `recording_id` only. The DESIGN chain `recording -> stt_run -> (diarization_run) -> analysis_run` is lineage of use, and it is kept where it matters: `analysis_run` records both `stt_run_id` and `diarization_run_id`.

## Token flow (decision 6)
```
.env: HF_TOKEN  --load_settings--> settings.diarization.hf_token (repr=False)
                                      |-- joblog.secrets_from  (masking)
                                      '-- diar_fetch only      (download into HF cache)
server run time: model in cache -> worker is offline, token not passed on
```
Only the one-off download needs the token. Making the cached model depend on the token forever would add a failure mode and a longer-lived secret in the child for no gain. If the reviewer prefers the literal reading ("token missing -> unavailable"), change the condition in the availability check and the "Cached model, no token" scenario; nothing else moves.

## Device (decision 8)
Default `cpu`. The model card (as read in `ml-engineer.md`) documents CPU and does not mention MPS; PyTorch's MPS backend does not implement every operator. MPS is opt-in and the worker then enables PyTorch's CPU fallback for missing operators. `torch.backends.mps.is_available()` is true in this venv (torch 2.15.0, measured 2026-11-08), so the opt-in is testable. A flip of the default needs the task 12 comparison.

## Unverified assumptions (each is a task, nothing is built on them silently)
| Assumption | Where it matters | Checked in |
|---|---|---|
| `pyannote.audio` 4.x installs next to `mlx-whisper` in `.venv` | setup | 9.1 |
| community-1 snapshot has a `config.yaml` at its root, weight files (`*.bin`, `*.safetensors` or `*.ckpt`) and bundles its sub-models, so a local snapshot path loads offline | `model_cached`, worker load | 9.2 |
| `Pipeline.__call__(..., hook=)` calls `hook(step_name, step_artifact, file=None, total=None, completed=None)` | progress lines | 9.2 |
| `min_speakers` / `max_speakers` keyword arguments and `output.exclusive_speaker_diarization` exist in the installed version | worker | 9.2 |
| A preloaded `{"waveform": Tensor(1, T), "sample_rate": 16000}` is accepted (no torchcodec needed) | audio decode | 9.2 |
| Speed on M1 Pro CPU is "minutes for 2.5 h" | user expectation only | 12.2 |
If 9.2 shows a different API, only `engines/pyannote_diar.py` changes; the worker, step, API and tests use the engine interface `load(device)`, `diarize(audio_path, min_speakers, max_speakers, progress)`.

## Quote check with labels (decision 16)
Labelled lines are passed as the existing `segments`; `generate_kind` verifies quotes against the same text. A quote inside one line verifies as before. A quote that crosses a line break now also contains the next line's label and shows "unverified" (kept, never dropped). The inserted instruction sentence tells the model not to put names in quotes. Verifying against a label-free copy would need a new parameter in `generate_kind`; not worth the blast radius for a cosmetic case.

## Names
`speaker_name` is keyed by run because raw labels (`SPEAKER_00`) are only stable within one run. Names in prompts: a user-chosen name is user content on a local machine; control characters and newlines are refused so a name cannot add prompt lines.

## Rejected
- WhisperX (second Whisper stack), FluidAudio and sherpa-onnx (fallbacks in `ml-engineer.md`; none needed unless 9.1 fails; a later change can add an engine id).
- A `speakers` row in `output`: the table requires an `analysis_run`, which would be a fake LLM run.
- Separate venv for pyannote: only if 9.1 shows a resolver conflict.

## DDL (migration `0009_diarization.sql`, as it will be written)
Style follows `0004_stt_runs.sql`.
```sql
CREATE TABLE diarization_run (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id uuid NOT NULL REFERENCES recording (id) ON DELETE CASCADE,
    job_id       uuid REFERENCES job (id) ON DELETE SET NULL,
    engine       text NOT NULL,                       -- registry engine id, e.g. pyannote
    model_id     text NOT NULL,                       -- models.toml [diar.<id>]
    settings     jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {min_speakers, max_speakers, device, merge_gap_ms}
    status       text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    started_at   timestamptz,
    finished_at  timestamptz,
    wall_ms      integer,
    audio_ms     integer,                             -- recording.duration_ms read at step start (same source as stt_run.audio_ms)
    rtf          real,                                -- audio seconds / wall seconds
    speakers     jsonb,                               -- [{speaker, talk_ms, share_pct, turns}], computed by code at completion
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX diarization_run_recording_idx ON diarization_run (recording_id, created_at);

CREATE TABLE diar_turn (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    diarization_run_id uuid NOT NULL REFERENCES diarization_run (id) ON DELETE CASCADE,
    idx                integer NOT NULL,
    start_ms           integer NOT NULL,
    end_ms             integer NOT NULL,
    speaker            integer NOT NULL CHECK (speaker >= 1),   -- 1..n by first appearance
    CHECK (end_ms > start_ms),
    UNIQUE (diarization_run_id, idx)
);
CREATE INDEX diar_turn_run_start_idx ON diar_turn (diarization_run_id, start_ms);

CREATE TABLE speaker_name (
    diarization_run_id uuid NOT NULL REFERENCES diarization_run (id) ON DELETE CASCADE,
    speaker            integer NOT NULL CHECK (speaker >= 1),
    name               text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 60),
    PRIMARY KEY (diarization_run_id, speaker)
);

ALTER TABLE analysis_run
    ADD COLUMN diarization_run_id uuid REFERENCES diarization_run (id) ON DELETE SET NULL;
```
Notes: `speaker_name` is the only mutable table (UPDATE/upsert/DELETE by PATCH). The API does the NFC and case-fold uniqueness check (the database cannot). `diar_turn.idx` follows the stored order after normalisation. `analysis_run` gets only the new nullable column; its `stt_run_id` stays NOT NULL. Existing tests that list tables or copy the migrations folder are updated in task 1.2b.

## Process environment (decision 6)
```
.env / shell ──load_settings──> os.environ (HF_TOKEN merged in)  ──scrub_secret_env──> removed
                                settings.diarization.hf_token (kept)
diar worker argv: /usr/bin/env -u HF_TOKEN -u HUGGING_FACE_HUB_TOKEN -u DETAILS_DIARIZATION_HF_TOKEN
                  HF_HUB_OFFLINE=1 HF_HUB_CACHE=<settings.paths.hf_hub> python -m details.engines.diar_worker spec.json
```
`spawn_child(argv)` has no `env` parameter, hence the `env` wrapper. Scrubbing happens in `__main__` (not inside `load_settings`) so tests that pass an explicit environment mapping, and `diar_fetch`, still see the token. A shell-exported token is also removed from the server process; that is intended.

## Chunked transcripts and the legend
`generate_kind(..., preface="")` puts `preface` before the transcript text in `prompt(text)`, which is used for the single pass and for every map chunk, so the legend is present in each. The reduce call (summary/title only) gets no transcript and no preface. Chunk cache keys already include the prompt text (`cache_key(..., text)`), so the cache key must include the preface; the implementation passes the prefaced text as the key input (tested: a plain and a labelled run never share a cache entry).

## GPU lease and Live assist
`diarize` holds the `job` lease for its whole step. A Live assist session wants owner `live`: it waits for the running step (never interrupts it), then is granted ahead of other queued jobs. A 60 minute recording on CPU may hold the lease for many minutes (speed unmeasured), which is why the button carries a hint.

## Memory claim
The `diarize` step releases only `details-*` models. A model the user loaded in LM Studio stays unless `lmstudio.exclusive` is true, so "never summed with an LM Studio model" holds for the app's own models only.

## Whole-segment attribution
One STT segment (often 5 to 30 s) gets one speaker, the one with the largest overlap. Talk-time % comes from the turns and counts the minority speaker's seconds, so the labelled text can attribute those seconds to the other person. This is shown on the card. Splitting at turn boundaries would need word times, which no current engine gives.
