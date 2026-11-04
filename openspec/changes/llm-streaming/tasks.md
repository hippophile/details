## 1. Spike (read-only, before any code)
- [x] 1.1 Against the running LM Studio with the already-loaded `google/gemma-4-e4b` (do NOT load, unload or switch any model; `lms ps` before and after must be identical): record to `tests/fixtures/lmstudio_stream_ok.sse` a streamed `json_schema` reply (checklist-sized, `max_tokens` about 400). Already confirmed 2026-11-04 for a tiny reply: chunks, finish chunk, usage chunk, `[DONE]`.
- [x] 1.2 Prove cancel: start a long streamed request (`max_tokens` 1500), close the connection after about 1 s, and observe that generation stops (LM Studio log or a follow-up request answering at normal speed). Record the result in an optional `design.md` of this change.
- [x] 1.3 Prove `finish_reason:"length"` arrives on a cut-off stream (`max_tokens` 20) and record `tests/fixtures/lmstudio_stream_length.sse`; capture what a rejected `stream_options` returns, if the server rejects it.
- [x] 1.4 If 1.2 shows the server keeps generating after disconnect, STOP and report: cancel would free the app but not the GPU, and the "stops generating" wording in the spec must change before implementing.

## 2. Tests first (red)
- [x] 2.1 Extend `tests/fakes/lmstudio.py` HTTP mode: `stream:true` replies as SSE chunks (scripted pieces, optional usage chunk, per-chunk delay, error chunk, drop after N, reject-with-400 mode, observed disconnects). No new fake module.
- [x] 2.2 `tests/test_stream_client.py`: parse the recorded fixtures, same result as plain, reasoning ignored, no-usage estimate, idle timeout, early end, fallback (400 / non-SSE) with exactly one extra request, no fallback after the first delta.
- [x] 2.3 `tests/test_stream_step.py`: assert ALL rows `cancelled` after cancel; pipeline `on_event` default None leaves existing llm tests green; analyse step with streaming on: invalid then valid retry, unverified quote, map-reduce chunks produce attempts, cancel mid-stream (under 1 s, no output, rows cancelled), `llm.stream=false`.
- [x] 2.4 `tests/test_stream_hub.py` and `tests/test_stream_api.py`: snapshot for a late subscriber, coalescing, retry reset, cap, terminal close, 404, two subscribers.
- [x] 2.5 Negative tests: no delta or prompt text in the log; `password=SeCr3tPw` in a stream error chunk absent from log, snapshot and job error.
- [x] 2.6 `tests/js/stream.test.mjs` (delta accumulator, tok/s header, retry reset) and a page test for ids and `textContent`-only.
- [x] 2.7 Run them; **observe and keep the failing output**.

## 3. Implement
- [x] 3.1 `settings.py` + `config.toml`: `[llm] stream = true`, `stream_idle_timeout_s = 60`, env overrides, validation.
- [x] 3.2 `llm/client.py`: `chat_stream` (SSE parser tolerant of `:` comments and split lines, `[DONE]`), cancel race, error redaction via `runner/joblog.redact`.
- [x] 3.2b `llm/pipeline.py`: optional `on_event` keyword in `call_validated`/`generate_kind` (attempt_end with validity, chunk i/n); `steps_analyse.py`: `StreamCancelled` is not caught as `LLMError`, and the failed-UPDATE is skipped when `ctx.cancelled.is_set()`.
- [x] 3.3 `llm/stream_hub.py` and the `GET /api/jobs/{id}/stream` route; hub on the app (`web.AppKey`), dropped at job end.
- [x] 3.4 `steps_analyse.py` `chat` wrapper: choose stream or plain, publish attempt and deltas, fallback memo per model for the job, log fields `stream`, `ttft_ms`, `fallback`.
- [x] 3.5 `web/analyse/stream.{js,css}` + `index.html` / `outputs.js` hook (live box in the AI model section). No other UI change.
- [x] 3.6 Re-run the section 2 tests: all green.

## 4. Gate
- [x] 4.1 `make test` full suite once (GPU excluded); paste the summary line.
- [x] 4.2 `openspec validate llm-streaming --strict`.

## 5. Manual proof
- [x] 5.1 Real run with the loaded E4B (cheap preset): Brave screenshot mid-stream of the live box, then of the final result; note time-to-first-token and tok/s from the job log (measured).
- [x] 5.2 Cancel a real run mid-stream; show `lms ps` (only the app-loaded model removed, the user's model untouched) and the job log.
- [x] 5.3 Run the `@pytest.mark.gpu` streaming test once and paste its output.
