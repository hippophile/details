"""Rolling notes: every `live.notes_every_min` of wall time, if committed text grew and no update is running, ask the
cheap model (Gemma E4B only) for notes, action items and tags from the previous notes + the newest live transcript.
Reuses client.chat + pipeline.call_validated (one retry, never repaired) and quotes.verify against the WHOLE live
transcript. Unverified items stay visible, marked. Any problem (LM Studio down, no memory, timeout, bad reply) becomes a
status message; recording and live text go on and the next tick tries again."""
import asyncio, hashlib, json, logging, pathlib, time

from ..llm import client, pipeline, quotes, schemas
from ..llm.chunking import estimate_tokens
from ..llm.client import LLMError
from ..llm.lmstudio_ctl import LMStudioError, identifier_for
from ..runner.joblog import redact, secrets_from
from ..runner.memory import MemoryManager, MemoryShort

log = logging.getLogger("details.live")
KIND, SCHEMA_KIND = "live_rolling", "live-rolling"
PROMPT_PATH = pathlib.Path(__file__).with_name("rolling_prompt.md")
MAX_TOKENS = 1200
STOP = "Recording and live text continue"


def prewarm():
    """Import the quote checker (eval/score.py and jiwer are slow) before the first update, off the event loop."""
    quotes.norm("x")


def prompt_template():
    text = PROMPT_PATH.read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Free:
    """A MemInfo with a value that was read off the event loop."""

    def __init__(self, gb):
        self.gb = gb

    def free_gb(self):
        return self.gb


def strip_verified(content):
    return {k: ([{kk: vv for kk, vv in it.items() if kk != "verified"} for it in v] if isinstance(v, list) else v)
            for k, v in content.items()}


def trim_transcript(lines, max_tokens, chars_per_token):
    """Newest lines that fit `max_tokens` (estimated); the newest line is always kept."""
    kept, used = [], 0
    for t in reversed(lines):
        n = estimate_tokens(t, chars_per_token)
        if kept and used + n > max_tokens:
            break
        kept.append(t)
        used += n
    return list(reversed(kept))


class Rolling:
    def __init__(self, session):
        self.s = session
        self._last_n = 0
        self.paused = False                                         # a recording pause: no update STARTS meanwhile
        self._seq = 0
        self._loop_task = self._update_task = None

    def start(self):
        self._loop_task = asyncio.create_task(self._loop())

    def stop(self):
        for t in (self._loop_task, self._update_task):
            if t is not None and not t.done():
                t.cancel()

    async def close(self):
        self.stop()
        tasks = [t for t in (self._loop_task, self._update_task) if t is not None and not t.done()]
        if tasks:
            await asyncio.wait(tasks, timeout=1.0)

    async def _loop(self):
        s = self.s
        while True:
            await s.mgr.clock.sleep(s.interval_s)
            if self.paused:
                continue                                            # an in-flight update may finish; none starts
            if self._update_task is not None and not self._update_task.done():
                continue                                            # an update is still running: skip this tick
            if len(s.commits) == self._last_n:
                continue                                            # nothing new was said
            self._update_task = asyncio.create_task(self._update())

    # ---- one update -----------------------------------------------------------------------------------
    def _where(self):
        st = self.s.mgr.settings
        return redact(st.lmstudio.url, secrets_from(st))

    async def _update(self):
        s = self.s
        n, lines = len(s.commits), [c["text"] for c in s.commits]
        try:
            s.set_status("notes", "ok", "Updating notes...")
            result = await self._run(lines)
            if result is None:
                self._last_n = n
                s.set_status("notes", "warn", "Notes update skipped: the model's reply was not valid twice. "
                                              "Previous notes kept")
                return
            self._last_n = n
            self._seq += 1
            msg = {"type": "live_notes", "seq": self._seq, "at_ms": s.audio_end_ms, **result}
            s.notes = msg
            s.emit(msg)
            s.set_status("notes", "ok", "Notes updated")
            text, sha = prompt_template()
            s._persist("rolling.json", json.dumps({**msg, "model": identifier_for(s.llm_entry.id),
                                                   "prompt_sha256": sha}, ensure_ascii=False, indent=1))
        except asyncio.CancelledError:
            raise
        except MemoryShort as e:
            s.set_status("notes", "error", f"Not enough free memory for the notes model ({e}). {STOP}; "
                                           "it will try again at the next update")
        except LMStudioError as e:
            s.set_status("notes", "error", f"Cannot reach LM Studio at {self._where()} ({e}). {STOP}; "
                                           "it will try again at the next update")
        except LLMError as e:
            s.set_status("notes", "error", f"{e}. {STOP}; it will try again at the next update")
        except asyncio.TimeoutError:
            s.set_status("notes", "error", f"LM Studio at {self._where()} did not answer within "
                                           f"{s.cfg.llm_timeout_s:g} s. {STOP}; it will try again at the next update")
        except Exception as e:
            log.exception("live notes update failed")
            s.set_status("notes", "error", f"Notes update failed ({type(e).__name__}). {STOP}")

    async def _run(self, lines):
        """-> {notes, action_items, tags, verified, unverified} or None when both replies were invalid."""
        s, mgr, cfg = self.s, self.s.mgr, self.s.cfg
        entry = s.llm_entry
        free = await s._off(mgr.memory.meminfo.free_gb)             # vm_stat is a blocking subprocess
        mm = MemoryManager(mgr.memory.ctl, mgr.registry, mgr.settings, _Free(free))
        s.llm_loaded = True                                         # release at the end even if the load is cut short
        await asyncio.wait_for(mm.ensure_llm(entry), cfg.llm_timeout_s)
        template, _ = prompt_template()
        kept = trim_transcript(lines, cfg.max_transcript_tokens, mgr.settings.llm.chars_per_token)
        prev = json.dumps(strip_verified({k: s.notes[k] for k in ("notes", "action_items", "tags")}), ensure_ascii=False) \
            if s.notes else "(none yet)"
        prompt = (template.replace("{{language}}", {"el": "Greek", "en": "English"}.get(cfg.language, cfg.language))
                  .replace("{{previous_notes}}", prev).replace("{{transcript}}", "\n".join(kept)))
        ident, schema = identifier_for(entry.id), schemas.model_schema(SCHEMA_KIND)

        async def chat(messages, *, kind, schema, max_tokens):
            return await asyncio.wait_for(client.chat(mgr.settings, model=ident, kind=kind, schema=schema,
                                                      messages=messages, max_tokens=max_tokens), cfg.llm_timeout_s)
        attempts = await pipeline.call_validated(chat, KIND, [{"role": "user", "content": prompt}], schema,
                                                 lambda d: schemas.validate(SCHEMA_KIND, d), MAX_TOKENS)
        last = attempts[-1]
        if not last.valid:
            return None
        content, v, u = await s._off(quotes.verify, last.content, "\n".join(c["text"] for c in s.commits))
        return {"notes": content["notes"], "action_items": content["action_items"], "tags": content["tags"],
                "verified": v, "unverified": u}
