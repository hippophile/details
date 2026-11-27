"""STT runs: persistence, listing, start/validate, Greek postfix, v1 transcript adoption. Rows are append-only."""
import asyncio, datetime, re, uuid

from psycopg.types.json import Jsonb

from . import comparison, library, recordings
from .models import stt_installed

V1_MODEL = "unknown (v1 import)"
LANGUAGES = ("el", "auto")
LANG_NAMES = {"el": "Ελληνικά", "auto": "Auto"}
GREEK_Q = re.compile("(?<=[Ͱ-Ͽἀ-῿])\\?")
LIST_COLS = ("id, recording_id, job_id, engine, model_id, language, status, started_at, finished_at, wall_ms, "
             "audio_ms, rtf, error, settings, created_at, raw->'languages' AS languages, "
             "raw->'cleaned' IS NOT NULL AS cleaned")
RUNNING = ("queued", "running")


class StartError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def greek_postfix(text):
    """A `?` directly after a Greek letter becomes the Greek question mark `;`."""
    return GREEK_Q.sub(";", text)


def fmt_wall(ms):
    s = ms / 1000
    return f"{s:.1f} s" if s < 60 else f"{int(s // 60)}:{int(round(s % 60)):02d}"


def language_setting(settings):
    """The requested language as the label shows it (`Ελληνικά + en + de`, `Auto`); None for rows without one (v1 import)."""
    lang = (settings or {}).get("language")
    if not lang:
        return None
    return " + ".join([LANG_NAMES.get(lang, lang), *(settings.get("also") or [])])


def run_label(n, model_label, status, wall_ms, rtf, setting=None):
    tail = f"{fmt_wall(wall_ms)} · {rtf:.1f}×" if status == "done" and wall_ms and rtf else status
    return f"#{n} · {model_label} · {setting} · {tail}" if setting else f"#{n} · {model_label} · {tail}"


def _out(row, registry, with_text=False):
    r = {k: (str(v) if isinstance(v, uuid.UUID) else v.isoformat() if isinstance(v, datetime.datetime) else v)
         for k, v in row.items()}
    m = registry.stt.get(r["model_id"])
    r["model_label"] = m.label if m else r["model_id"]
    live = " (live preview)" if (r.get("settings") or {}).get("live_preview") else ""     # live-assistant
    r["model_role"] = getattr(m, "role", None)
    r["label"] = run_label(r["n"], r["model_label"] + live, r["status"], r["wall_ms"], r["rtf"],
                           language_setting(r.get("settings")))
    return r


async def check_model(registry, settings, model_id, language, also=None):
    """Raises StartError: 422 unknown / not an STT model / bad language or also, 409 not installed."""
    if language not in LANGUAGES:
        raise StartError(422, "language must be 'el' or 'auto'")
    if also is not None:
        if not isinstance(also, list) or not all(isinstance(c, str) for c in also):
            raise StartError(422, "also must be a list of language codes")
        if also and language == "auto":
            raise StartError(422, "also needs a fixed language: it cannot be combined with auto")
        if len(set(also)) != len(also):
            raise StartError(422, "also has a duplicate language")
        for c in also:
            if c == language:
                raise StartError(422, f"also repeats the primary language '{c}'")
            if c not in settings.stt.extras():
                raise StartError(422, f"'{c}' is not an allowed extra language (allowed: {', '.join(settings.stt.extras())})")
    if not isinstance(model_id, str) or not model_id:
        raise StartError(422, "model_id is required")
    entry = registry.stt.get(model_id)
    if entry is None:
        raise StartError(422, f"'{model_id}' is not an STT model" if model_id in registry.llm
                         else f"unknown model_id '{model_id}'")
    if not entry.enabled:
        raise StartError(409, f"{entry.label} is disabled in models.toml")
    if not stt_installed(settings.paths.hf_hub, entry.repo):
        raise StartError(409, f"weights for {entry.repo} are not in the Hugging Face cache "
                              "(run `make setup` or download the repo with huggingface_hub)")
    return entry


async def start_run(pool, jobs, registry, settings, recording_id, model_id, *, language="el", vocab_hint=False,
                    initial_prompt=None, window_s=None, also=None):
    """Insert a queued run + enqueue its job -> (stt_run_id, job_id). StartError(404) for an unknown recording."""
    entry = registry.stt.get(model_id)
    if entry is None:
        raise StartError(422, f"unknown model_id '{model_id}'")
    if not library.as_uuid(recording_id):
        raise StartError(404, "recording not found")
    prompt = (initial_prompt or settings.stt.vocab_hint) if vocab_hint else None
    run_settings = {"language": language, "vocab_hint": bool(vocab_hint), "initial_prompt": prompt,
                    "window_s": window_s or settings.stt.window_s, "overlap_s": settings.stt.overlap_s,
                    "also": list(also or []), "switch_min_share": settings.stt.switch_min_share,
                    "condition_on_previous_text": False}
    async with pool.connection() as c:
        cur = await c.execute("SELECT 1 FROM recording WHERE id=%s AND deleted_at IS NULL", (recording_id,))
        if await cur.fetchone() is None:
            raise StartError(404, "recording not found")
        cur = await c.execute("INSERT INTO stt_run (recording_id, engine, model_id, settings, language) "
                              "VALUES (%s,%s,%s,%s,%s) RETURNING id",
                              (recording_id, entry.engine, model_id, Jsonb(run_settings),
                               language if language != "auto" else None))
        run_id = str((await cur.fetchone())["id"])
    try:
        job_id = await jobs.enqueue("stt", recording_id, [{"name": "stt_transcribe",
                                                           "params": {"stt_run_id": run_id}}])
    except BaseException:
        async with pool.connection() as c:
            await c.execute("DELETE FROM stt_run WHERE id=%s", (run_id,))
        raise
    async with pool.connection() as c:
        await c.execute("UPDATE stt_run SET job_id=%s WHERE id=%s", (job_id, run_id))
    return run_id, job_id


async def list_runs(pool, settings, registry, recording_id):
    rid = library.as_uuid(recording_id)
    if not rid:
        return None
    await adopt_v1_transcripts(pool, settings, rid)
    async with pool.connection() as c:
        cur = await c.execute(f"SELECT {LIST_COLS}, row_number() OVER (ORDER BY created_at, id) AS n "
                              "FROM stt_run WHERE recording_id=%s ORDER BY created_at DESC, id DESC", (rid,))
        return [_out(r, registry) for r in await cur.fetchall()]


async def get_run(pool, registry, run_id):
    rid = library.as_uuid(run_id)
    if not rid:
        return None
    async with pool.connection() as c:
        cur = await c.execute(
            f"SELECT * FROM (SELECT {LIST_COLS}, text, row_number() OVER (PARTITION BY recording_id "
            "ORDER BY created_at, id) AS n FROM stt_run) x WHERE id=%s", (rid,))
        row = await cur.fetchone()
    return _out(row, registry) if row else None


async def segments(pool, run_id):
    rid = library.as_uuid(run_id)
    if not rid:
        return None
    async with pool.connection() as c:
        if await (await c.execute("SELECT 1 FROM stt_run WHERE id=%s", (rid,))).fetchone() is None:
            return None
        cur = await c.execute("SELECT idx, start_ms, end_ms, text, avg_logprob, no_speech_prob FROM "
                              "transcript_segment WHERE stt_run_id=%s ORDER BY idx", (rid,))
        return await cur.fetchall()


async def delete_run(pool, run_id):
    """-> 'deleted' | 'busy' | None (unknown)."""
    rid = library.as_uuid(run_id)
    if not rid:
        return None
    async with pool.connection() as c:
        async with c.transaction():
            row = await (await c.execute("SELECT status, recording_id FROM stt_run WHERE id=%s FOR UPDATE", (rid,))).fetchone()
            if row is None:
                return None
            if row["status"] in RUNNING:
                return "busy"
            await c.execute("DELETE FROM stt_run WHERE id=%s", (rid,))
            await comparison.prune_orphans(c, row["recording_id"])
    return "deleted"


async def adopt_v1_transcripts(pool, settings, recording_id=None):
    """One done run (no segments) per recording that has an importer-written v1-transcript.txt and none yet."""
    where, args = "", ()
    if recording_id:
        where, args = "AND r.id=%s", (recording_id,)
    async with pool.connection() as c:
        cur = await c.execute("SELECT r.id, r.duration_ms FROM recording r WHERE NOT EXISTS "
                              f"(SELECT 1 FROM stt_run s WHERE s.recording_id=r.id AND s.model_id=%s) {where}",
                              (V1_MODEL, *args))
        todo = await cur.fetchall()
    root = recordings.recordings_dir(settings)
    for r in todo:
        f = root / str(r["id"]) / "v1-transcript.txt"
        try:
            text = await asyncio.to_thread(f.read_text, "utf-8")
        except OSError:
            continue
        async with pool.connection() as c:
            await c.execute(
                "INSERT INTO stt_run (recording_id, engine, model_id, status, text, audio_ms, started_at, "
                "finished_at, settings) SELECT %s,'mlx_whisper',%s,'done',%s,%s,now(),now(),%s WHERE NOT EXISTS "
                "(SELECT 1 FROM stt_run WHERE recording_id=%s AND model_id=%s)",
                (r["id"], V1_MODEL, text.strip(), r["duration_ms"], Jsonb({"source": "v1-transcript.txt"}),
                 r["id"], V1_MODEL))
