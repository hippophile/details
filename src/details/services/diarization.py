"""Who talked: turn normalising, talk-time numbers, overlap alignment, labelled transcript lines, speaker names and the
run queries. Turns are audio facts stored per recording; the segment-to-speaker mapping is computed at read time and is
never written into transcript_segment."""
import bisect, datetime, unicodedata, uuid

from psycopg.types.json import Jsonb

from . import library
from .models import diar_status
from .stt_runs import fmt_wall

# Added before the transcript of every prompt of a labelled analysis (single pass and each chunk).
LEGEND = ("Each line starts with the speaker's name and a colon. Speaker labels are automatic and can be wrong. "
          "Never put a speaker name inside a quote.")
MAX_NAME = 60
HINT_MAX = 10
RUNNING = ("queued", "running")
LIST_COLS = ("id, recording_id, job_id, engine, model_id, settings, status, started_at, finished_at, wall_ms, "
             "audio_ms, rtf, speakers, error, created_at")


class NameInvalid(ValueError):
    pass


class NameTaken(ValueError):
    pass


class StartError(Exception):
    def __init__(self, status, message, hint=None):
        super().__init__(message)
        self.status, self.message, self.hint = status, message, hint


class LabelError(Exception):
    """A diarization run cannot be used as speaker labels (HTTP 422)."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


# ---- pure functions -------------------------------------------------------------------------------------------

def normalise_turns(raw, audio_ms):
    """Engine turns {start_ms, end_ms, speaker} -> sorted, clipped to [0, audio_ms], overlaps clamped (a later turn
    starts where the earlier one ended), empty turns dropped, raw labels renumbered 1..n by first appearance."""
    items = []
    for t in raw:
        a, b = (max(0, min(int(round(t[k])), audio_ms)) for k in ("start_ms", "end_ms"))
        if b > a:
            items.append((a, b, t["speaker"]))
    items.sort(key=lambda x: (x[0], x[1]))
    out, ids, last_end = [], {}, 0
    for a, b, label in items:
        a = max(a, last_end)
        if b <= a:
            continue
        out.append({"speaker": ids.setdefault(label, len(ids) + 1), "start_ms": a, "end_ms": b})
        last_end = b
    return out


def stats(turns, merge_gap_ms):
    """-> [{speaker, talk_ms, share_pct, turns}] by speaker number. share_pct is a share of the total talk time (not of
    the recording length) to one decimal; `turns` counts same-speaker runs after merging gaps below merge_gap_ms."""
    by = {}
    for t in sorted(turns, key=lambda t: (t["start_ms"], t["end_ms"])):
        by.setdefault(t["speaker"], []).append(t)
    total = sum(t["end_ms"] - t["start_ms"] for t in turns)
    out = []
    for spk in sorted(by):
        talk, runs, prev_end = 0, 0, None
        for t in by[spk]:
            talk += t["end_ms"] - t["start_ms"]
            if prev_end is None or t["start_ms"] - prev_end >= merge_gap_ms:
                runs += 1
            prev_end = t["end_ms"] if prev_end is None else max(prev_end, t["end_ms"])
        out.append({"speaker": spk, "talk_ms": talk, "share_pct": round(talk * 100 / total, 1) if total else 0.0,
                    "turns": runs})
    return out


def align(segments, turns):
    """One speaker per segment: the one with the largest overlap in ms, ties to the lower number, None (shown as
    "Unknown") when nothing overlaps. Never guessed."""
    ts = sorted(turns, key=lambda t: (t["start_ms"], t["end_ms"]))
    starts = [t["start_ms"] for t in ts]
    reach, m = [], 0
    for t in ts:
        m = max(m, t["end_ms"])
        reach.append(m)
    out = []
    for s in segments:
        a, b = s["start_ms"], s["end_ms"]
        acc, i = {}, bisect.bisect_left(starts, b) - 1       # turns starting at or after the segment end cannot overlap
        while i >= 0 and reach[i] > a:                       # no turn at or before i reaches into the segment
            t = ts[i]
            ov = min(b, t["end_ms"]) - max(a, t["start_ms"])
            if ov > 0:
                acc[t["speaker"]] = acc.get(t["speaker"], 0) + ov
            i -= 1
        out.append(min(acc, key=lambda k: (-acc[k], k)) if acc else None)
    return out


def display(speaker, names):
    if speaker is None:
        return "Unknown"
    return names.get(speaker) or f"Speaker {speaker}"


def render_lines(segments, aligned, names, merge_gap_ms):
    """`Name: text` lines. Consecutive segments of one speaker closer than merge_gap_ms become one line."""
    lines, cur = [], None
    for seg, spk in zip(segments, aligned):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if cur and cur[0] == spk and seg["start_ms"] - cur[1] < merge_gap_ms:
            cur[2].append(text)
            cur[1] = seg["end_ms"]
        else:
            cur = [spk, seg["end_ms"], [text]]
            lines.append(cur)
    return [f"{display(spk, names)}: {' '.join(texts)}" for spk, _, texts in lines]


def _key(s):
    return unicodedata.normalize("NFC", s).casefold()


def check_name(raw, taken):
    """Trim + NFC; 1-60 characters, no control characters, line breaks or colon (it becomes `Name:` in prompts);
    unique after NFC + casefold against `taken` (the other speakers' display names)."""
    if not isinstance(raw, str):
        raise NameInvalid("name must be text")
    name = unicodedata.normalize("NFC", raw)
    if any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in name):
        raise NameInvalid("name must not contain control characters or line breaks")
    name = name.strip()
    if not 1 <= len(name) <= MAX_NAME:
        raise NameInvalid(f"name must be 1 to {MAX_NAME} characters")
    if ":" in name:
        raise NameInvalid("name must not contain a colon")
    if any(_key(name) == _key(t) for t in taken):
        raise NameTaken("another speaker already has this name")
    return name


def validate_hints(min_speakers, max_speakers):
    for k, v in (("min_speakers", min_speakers), ("max_speakers", max_speakers)):
        if v is not None and (type(v) is not int or not 1 <= v <= HINT_MAX):
            raise StartError(422, f"{k} must be a whole number from 1 to {HINT_MAX}")
    if min_speakers is not None and max_speakers is not None and min_speakers > max_speakers:
        raise StartError(422, "min_speakers must not be larger than max_speakers")


def run_label(n, model_label, status, wall_ms, speakers):
    if status != "done":
        return f"#{n} · {model_label} · {status}"
    k = len(speakers or [])
    count = "no speech" if k == 0 else f"{k} speaker{'' if k == 1 else 's'}"
    return " · ".join(x for x in (f"#{n}", model_label, fmt_wall(wall_ms) if wall_ms else None, count) if x)


# ---- runs -----------------------------------------------------------------------------------------------------

def _out(row, registry, names):
    r = {k: (str(v) if isinstance(v, uuid.UUID) else v.isoformat() if isinstance(v, datetime.datetime) else v)
         for k, v in row.items()}
    m = registry.diar.get(r["model_id"])
    r["model_label"] = m.label if m else r["model_id"]
    mine = names.get(str(row["id"]), {})
    r["speakers"] = [{"speaker": s["speaker"], "name": mine.get(s["speaker"]), "display": display(s["speaker"], mine),
                      "talk_ms": s["talk_ms"], "share_pct": s["share_pct"], "turns": s["turns"]}
                     for s in (row["speakers"] or [])]
    r["label"] = run_label(r["n"], r["model_label"], r["status"], r["wall_ms"], row["speakers"])
    return r


async def names_for(c, run_ids):
    """{run_id (str): {speaker: name}} for the given runs."""
    out = {}
    if run_ids:
        cur = await c.execute("SELECT diarization_run_id, speaker, name FROM speaker_name "
                              "WHERE diarization_run_id = ANY(%s)", (list(run_ids),))
        for r in await cur.fetchall():
            out.setdefault(str(r["diarization_run_id"]), {})[r["speaker"]] = r["name"]
    return out


async def list_runs(pool, registry, recording_id):
    """-> list (newest first) | None when the recording is unknown."""
    rid = library.as_uuid(recording_id)
    if not rid:
        return None
    async with pool.connection() as c:
        if await (await c.execute("SELECT 1 FROM recording WHERE id=%s", (rid,))).fetchone() is None:
            return None
        rows = await (await c.execute(f"SELECT {LIST_COLS}, row_number() OVER (ORDER BY created_at, id) AS n "
                                      "FROM diarization_run WHERE recording_id=%s ORDER BY created_at DESC, id DESC",
                                      (rid,))).fetchall()
        names = await names_for(c, [r["id"] for r in rows])
    return [_out(r, registry, names) for r in rows]


async def get_run(pool, registry, run_id):
    rid = library.as_uuid(run_id)
    if not rid:
        return None
    async with pool.connection() as c:
        row = await (await c.execute(
            f"SELECT * FROM (SELECT {LIST_COLS}, row_number() OVER (PARTITION BY recording_id ORDER BY created_at, id) "
            "AS n FROM diarization_run) x WHERE id=%s", (rid,))).fetchone()
        names = await names_for(c, [row["id"]]) if row else {}
    return _out(row, registry, names) if row else None


async def get_turns(pool, run_id):
    rid = library.as_uuid(run_id)
    if not rid:
        return None
    async with pool.connection() as c:
        if await (await c.execute("SELECT 1 FROM diarization_run WHERE id=%s", (rid,))).fetchone() is None:
            return None
        return await (await c.execute("SELECT idx, start_ms, end_ms, speaker FROM diar_turn "
                                      "WHERE diarization_run_id=%s ORDER BY idx", (rid,))).fetchall()


async def start_run(pool, jobs, registry, settings, recording_id, model_id=None, min_speakers=None, max_speakers=None):
    """Validate, insert a queued run, enqueue its `diarize` job -> (diarization_run_id, job_id). StartError carries the
    HTTP status: 422 bad model/hints, 404 unknown or trashed recording, 409 model unavailable (with a hint)."""
    model_id = settings.diarization.model if model_id is None else model_id
    entry = registry.diar.get(model_id) if isinstance(model_id, str) else None
    if entry is None:
        raise StartError(422, f"'{model_id}' is not a speaker model" if isinstance(model_id, str)
                         and (model_id in registry.stt or model_id in registry.llm)
                         else f"unknown model_id '{model_id}'")
    validate_hints(min_speakers, max_speakers)
    rid = library.as_uuid(recording_id)
    if not rid:
        raise StartError(404, "recording not found")
    async with pool.connection() as c:
        if await (await c.execute("SELECT 1 FROM recording WHERE id=%s AND deleted_at IS NULL", (rid,))).fetchone() is None:
            raise StartError(404, "recording not found")
    ok, reason, hint = diar_status(entry, settings)
    if not ok:
        raise StartError(409, reason, hint)
    run_settings = {"device": settings.diarization.device, "merge_gap_ms": settings.diarization.merge_gap_ms}
    for k, v in (("min_speakers", min_speakers), ("max_speakers", max_speakers)):
        if v is not None:
            run_settings[k] = v
    async with pool.connection() as c:
        row = await (await c.execute("INSERT INTO diarization_run (recording_id, engine, model_id, settings) "
                                     "VALUES (%s,%s,%s,%s) RETURNING id",
                                     (rid, entry.engine, model_id, Jsonb(run_settings)))).fetchone()
        run_id = str(row["id"])
    try:
        job_id = await jobs.enqueue("diarize", rid, [{"name": "diarize", "params": {"diarization_run_id": run_id}}])
    except BaseException:
        async with pool.connection() as c:
            await c.execute("DELETE FROM diarization_run WHERE id=%s", (run_id,))
        raise
    async with pool.connection() as c:
        await c.execute("UPDATE diarization_run SET job_id=%s WHERE id=%s", (job_id, run_id))
    return run_id, job_id


async def delete_run(pool, run_id):
    """-> 'deleted' | 'busy' | None (unknown). Turns and names go with it (cascade); analyses keep their row."""
    rid = library.as_uuid(run_id)
    if not rid:
        return None
    async with pool.connection() as c:
        async with c.transaction():
            row = await (await c.execute("SELECT status FROM diarization_run WHERE id=%s FOR UPDATE", (rid,))).fetchone()
            if row is None:
                return None
            if row["status"] in RUNNING:
                return "busy"
            await c.execute("DELETE FROM diarization_run WHERE id=%s", (rid,))
    return "deleted"


async def store_result(pool, run_id, raw_turns, *, audio_ms, merge_gap_ms, wall_ms):
    """Ingest the engine's turns in one transaction (idempotent: DELETE then INSERT) and mark the run done."""
    turns = normalise_turns(raw_turns, audio_ms)
    speakers = stats(turns, merge_gap_ms)
    async with pool.connection() as c:
        async with c.transaction():
            await c.execute("DELETE FROM diar_turn WHERE diarization_run_id=%s", (run_id,))
            if turns:
                async with c.cursor() as cur:
                    await cur.executemany(
                        "INSERT INTO diar_turn (diarization_run_id, idx, start_ms, end_ms, speaker) "
                        "VALUES (%s,%s,%s,%s,%s)", [(run_id, i, t["start_ms"], t["end_ms"], t["speaker"])
                                                    for i, t in enumerate(turns)])
            await c.execute("UPDATE diarization_run SET status='done', finished_at=now(), wall_ms=%s, audio_ms=%s, "
                            "rtf=%s, speakers=%s, error=NULL WHERE id=%s",
                            (wall_ms, audio_ms, audio_ms / wall_ms if wall_ms else None, Jsonb(speakers), run_id))
    return speakers


async def rename(pool, run_id, speaker, name):
    """Set (str) or reset (None) one speaker's name. -> {speaker, name, display} | None when the run or speaker is
    unknown. Raises NameInvalid / NameTaken. Never touches transcripts or turns."""
    rid = library.as_uuid(run_id)
    if not rid or type(speaker) is not int:
        return None
    async with pool.connection() as c:
        async with c.transaction():
            run = await (await c.execute("SELECT speakers FROM diarization_run WHERE id=%s FOR UPDATE", (rid,))).fetchone()
            nums = [s["speaker"] for s in (run["speakers"] or [])] if run else []
            if speaker not in nums:
                return None
            names = (await names_for(c, [rid])).get(rid, {})
            if name is None:
                await c.execute("DELETE FROM speaker_name WHERE diarization_run_id=%s AND speaker=%s", (rid, speaker))
                names.pop(speaker, None)
            else:
                others = [n for n in nums if n != speaker]       # display names AND the default labels of the others
                clean = check_name(name, [display(n, names) for n in others] + [f"Speaker {n}" for n in others])
                await c.execute("INSERT INTO speaker_name (diarization_run_id, speaker, name) VALUES (%s,%s,%s) "
                                "ON CONFLICT (diarization_run_id, speaker) DO UPDATE SET name=EXCLUDED.name",
                                (rid, speaker, clean))
                names[speaker] = clean
    return {"speaker": speaker, "name": names.get(speaker), "display": display(speaker, names)}


# ---- speaker labels for transcripts / analyses ------------------------------------------------------------------

async def label_source(pool, stt_run_id, diarization_run_id, *, need_segments=False):
    """Check that a diarization run can label an stt_run -> (turns, names). LabelError (422) when the id is malformed or
    unknown, the run is not done, belongs to another recording, or (need_segments) the transcript has no timestamps."""
    did = library.as_uuid(diarization_run_id) if isinstance(diarization_run_id, str) else None
    if not did:
        raise LabelError("diarization_run_id must be the id of a speaker run")
    async with pool.connection() as c:
        run = await (await c.execute("SELECT status, recording_id FROM diarization_run WHERE id=%s", (did,))).fetchone()
        if run is None:
            raise LabelError("the speaker run was not found")
        if run["status"] != "done":
            raise LabelError(f"the speaker run is {run['status']}: wait until it is done")
        stt = await (await c.execute("SELECT recording_id FROM stt_run WHERE id=%s", (stt_run_id,))).fetchone()
        if stt is None or stt["recording_id"] != run["recording_id"]:
            raise LabelError("the speaker run belongs to another recording")
        if need_segments and await (await c.execute("SELECT 1 FROM transcript_segment WHERE stt_run_id=%s LIMIT 1",
                                                    (stt_run_id,))).fetchone() is None:
            raise LabelError("this transcript has no timestamps (imported from v1), so it cannot get speaker labels")
        turns = await (await c.execute("SELECT start_ms, end_ms, speaker FROM diar_turn WHERE diarization_run_id=%s "
                                       "ORDER BY idx", (did,))).fetchall()
        names = (await names_for(c, [did])).get(did, {})
    return turns, names


async def labelled_segments(pool, stt_run_id, diarization_run_id):
    """Segment rows of an stt_run with `speaker` and `speaker_name` added (computed by overlap, not stored)."""
    turns, names = await label_source(pool, stt_run_id, diarization_run_id)
    async with pool.connection() as c:
        rows = await (await c.execute("SELECT idx, start_ms, end_ms, text, avg_logprob, no_speech_prob FROM "
                                      "transcript_segment WHERE stt_run_id=%s ORDER BY idx", (stt_run_id,))).fetchall()
    for r, spk in zip(rows, align(rows, turns)):
        r["speaker"], r["speaker_name"] = spk, display(spk, names)
    return rows


async def labelled_lines(pool, stt_run_id, diarization_run_id, merge_gap_ms):
    """`Name: text` lines for the analyse step, with the names current now. LabelError when the run is gone."""
    try:
        turns, names = await label_source(pool, stt_run_id, diarization_run_id, need_segments=True)
    except LabelError as e:
        raise LabelError(f"the speaker labels are gone: {e.message}") from None
    async with pool.connection() as c:
        rows = await (await c.execute("SELECT start_ms, end_ms, text FROM transcript_segment WHERE stt_run_id=%s "
                                      "ORDER BY idx", (stt_run_id,))).fetchall()
    return render_lines(rows, align(rows, turns), names, merge_gap_ms)
