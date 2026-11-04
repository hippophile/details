# Design notes: llm-streaming spike (measured 2026-11-04, LM Studio 0.4.x, already-loaded `google/gemma-4-e4b`)

Read-only: only chat requests to the loaded model, no load/unload. `lms ps` before and after: identical (one entry, `google/gemma-4-e4b`).

| Question | Result (measured) |
|---|---|
| 1.1 streamed `json_schema` reply | Streams. `tests/fixtures/lmstudio_stream_ok.sse`: 198 lines, SSE `data:` chunks, finish chunk, usage chunk (`choices: []`, `completion_tokens` 96), `data: [DONE]`. `Content-Type: text/event-stream`. |
| 1.2 cancel | Closing the socket after about 1.3 s (30 chunks read) made the server log `Client disconnected. Stopping generation... (If the model is busy processing the prompt, it will finish first.)` then `Finished streaming response`. Generation stops. Caveat from that same log line: while the prompt is still being processed the model finishes that first. |
| 1.3 `finish_reason:"length"` | Arrives on the cut-off stream (`max_tokens` 20, `completion_tokens` 19): `tests/fixtures/lmstudio_stream_length.sse`. |
| 1.3 rejected `stream_options` | Not rejected: `{"bogus":1}` was accepted and streamed normally, so the 400 fallback path is covered by the fake only. |
| other | A request for a model name that is not loaded still streamed (reasoning deltas, `reasoning_content`) instead of an error, so a wrong identifier is not detected by the stream itself. |

Not measured: the 26B model, and the shape of a mid-stream server error (the fake uses `data: {"error": {"message": ...}}`; `chat_stream` also accepts a plain string `error`).

Implementation choices that differ from or refine the proposal:
- First byte timeout: the time to first token includes prompt processing (minutes for a 20k-token transcript), so until the first byte the call uses `llm.timeout_s`; `llm.stream_idle_timeout_s` applies between chunks afterwards.
- The hub keeps a bounded ring of recent events (4096) across attempts, not only the current attempt, so `Last-Event-ID` resume survives a retry; the text snapshot is still the current attempt only (cap 256 KB, tail kept).
- Hub events: `attempt_start`, `delta` (`text`, `n` = pieces merged by the 100 ms coalescing), `attempt_end` (`valid`), `fallback`, `idle` (the step ended), plus route-level `snapshot` and `end` (`status`).
