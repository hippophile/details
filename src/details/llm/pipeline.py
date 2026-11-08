"""One output kind from one transcript: render prompt, call the model, validate, retry ONCE with the error text,
never repair, verify quotes. Long transcripts: map over chunks (cached on disk), merge lists in code, and for debrief
and notes one small reduce call for summary/title. Pure with respect to the DB: the step persists the result."""
import json, time
from dataclasses import dataclass, field

from . import checks, quotes, schemas
from .chunking import cache_get, cache_key, cache_put, estimate_tokens, split_segments
from .prompts import render
from .quotes import norm

PROMPT_OVERHEAD_TOKENS = 1500     # template + schema + safety margin inside the model context (estimate)
MIN_ROOM_TOKENS = 256
REDUCE_SCHEMA = {"type": "object", "properties": {"text": {"type": "string", "minLength": 1}}, "required": ["text"],
                 "additionalProperties": False}
REDUCE_PROMPT = ("Below are partial {what}s written for consecutive parts of one recording. Combine them into ONE "
                 "{target}. Use only the given text; do not add facts. Write in {language}. Keep English terms in "
                 "English. Reply with JSON only.\n\n{parts}\n")
REDUCE = {"debrief": ("summary", "coherent summary of 3-6 sentences", 600),
          "notes": ("title", "short title", 100)}
MAX_TAGS = 8


@dataclass
class Attempt:
    rendered_prompt: str
    text: str
    tokens_in: int
    tokens_out: int
    wall_ms: int
    finish_reason: str | None
    raw: dict
    errors: list | None = None
    content: dict | None = None

    @property
    def valid(self):
        return self.errors is None


@dataclass
class KindResult:
    attempts: list
    content: dict | None
    mode: str = "single"
    chunks: int = 1
    cache_hits: int = 0
    verified: int = 0
    unverified: int = 0
    stats: dict = field(default_factory=dict)


def _parse(text, finish_reason):
    try:
        data = json.loads(text)
    except ValueError as e:
        why = "reply was cut off (max_tokens reached): " if finish_reason == "length" else ""
        return None, [f"{why}reply is not valid JSON: {e}"]
    return data, None


async def call_validated(chat, kind, messages, schema, validate, max_tokens, on_event=None):
    """-> list of 1-2 Attempts. Exactly one retry, which appends the model's reply and the validation errors.
    on_event("attempt_end", valid=, attempt=, kind=) is called after each attempt is checked (live view; optional)."""
    attempts, msgs = [], list(messages)
    for n in range(2):
        r = await chat(msgs, kind=kind, schema=schema, max_tokens=max_tokens)
        data, errs = _parse(r["text"], r.get("finish_reason"))
        if errs is None:
            errs = validate(data) or None
        a = Attempt("\n\n".join(m["content"] for m in msgs), r["text"], r["tokens_in"], r["tokens_out"], r["wall_ms"],
                    r.get("finish_reason"), r["raw"], errs, None if errs else data)
        attempts.append(a)
        if on_event:
            on_event("attempt_end", valid=not errs, attempt=n + 1, kind=kind)
        if not errs:
            break
        msgs = msgs + [{"role": "assistant", "content": r["text"]},
                       {"role": "user", "content": "Your reply was rejected: " + "; ".join(errs)
                        + ". Reply again with corrected JSON only, matching the schema."}]
    return attempts


def _dedupe(items, key):
    seen, out = set(), []
    for it in items:
        k = norm(it[key])
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return out


def merge(kind, contents):
    """Lists merged in code, de-duplicated on norm(text); the first chunk's prose fields are kept."""
    first = contents[0]
    out = {k: v for k, v in first.items()}
    if kind == "debrief":
        for k in ("decisions", "action_items", "risks", "open_questions"):
            out[k] = _dedupe([i for c in contents for i in c[k]], "text")
    elif kind == "checklist":
        out["items"] = _dedupe([i for c in contents for i in c["items"]], "text")
    elif kind == "notes":
        sections = {}
        for c in contents:
            for s in c["sections"]:
                sec = sections.setdefault(norm(s["heading"]), {"heading": s["heading"], "bullets": []})
                sec["bullets"] = _dedupe(sec["bullets"] + s["bullets"], "text")
        out["sections"] = list(sections.values())
    elif kind == "tags":
        count, firsts = {}, {}
        for c in contents:
            for t in c["tags"]:
                k = norm(t["name"])
                count[k] = count.get(k, 0) + 1
                firsts.setdefault(k, t)
        ranked = sorted(firsts, key=lambda k: -count[k])      # stable: ties keep first-appearance order
        out["tags"] = [firsts[k] for k in ranked[:MAX_TAGS]]
    return out


def _sum(attempts, **extra):
    return {"tokens_in": sum(a.tokens_in for a in attempts), "tokens_out": sum(a.tokens_out for a in attempts),
            "wall_ms": sum(a.wall_ms for a in attempts), **extra}


async def generate_kind(chat, *, settings, kind, template, model, context, segments, language, title, data_dir,
                        on_progress=None, on_event=None, preface=""):
    """on_event (optional, live view): ("chunk", i, n) before each map call (1-based), ("chunk", None, None) before
    the reduce call, plus call_validated's ("attempt_end", ...).
    preface (speakers-diarization): a fixed sentence placed at the start of the transcript text of EVERY prompt (single
    pass and each chunk), so a legend is never lost in later chunks. Quote checks still run against the plain text;
    the chunk cache key sees the prefaced text, so a plain and a labelled run never share an entry."""
    llm = settings.llm
    full = "\n".join(segments)
    max_tokens = llm.max_tokens(kind)
    room = max(MIN_ROOM_TOKENS, context - max_tokens - PROMPT_OVERHEAD_TOKENS)
    schema = schemas.model_schema(kind)
    names = tuple(getattr(template, "checks", ()) or ())      # prompt-ab: code checks of this prompt version

    def validator(text):
        """Schema first; the template's checks run against the text the model saw (whole transcript or one chunk)."""
        return lambda d: schemas.validate(kind, d) or checks.run(names, kind, d, text)
    progress = on_progress or (lambda f, m="": None)

    def framed(text):
        return f"{preface}\n\n{text}" if preface else text

    def prompt(text):
        return [{"role": "user", "content": render(template.body, framed(text), language, title)}]

    if estimate_tokens(full, llm.chars_per_token) <= min(llm.single_pass_max_tokens, room):
        attempts = await call_validated(chat, kind, prompt(full), schema, validator(full), max_tokens, on_event)
        last = attempts[-1]
        content, v, u = (None, 0, 0) if not last.valid else quotes.verify(last.content, full)
        return KindResult(attempts, content, "single", 1, 0, v, u, _sum(attempts))

    chunks = split_segments(segments, min(llm.chunk_tokens, room), llm.chunk_overlap_tokens, llm.chars_per_token)
    params = {"temperature": llm.temperature, "max_tokens": max_tokens, "language": language}
    total = len(chunks) + (1 if kind in REDUCE and len(chunks) > 1 else 0)
    contents, hits, calls = [], 0, []
    first_prompt = prompt("\n".join(chunks[0]))[0]["content"]

    def failed(attempts):
        last = attempts[-1]
        agg = Attempt(first_prompt, last.text, 0, 0, 0, last.finish_reason, last.raw, last.errors, None)
        agg.tokens_in, agg.tokens_out, agg.wall_ms = (sum(getattr(a, f) for a in calls + attempts) for f in
                                                     ("tokens_in", "tokens_out", "wall_ms"))
        return KindResult([agg], None, "map-reduce", len(chunks), hits, 0, 0, _sum([agg]))

    for i, chunk in enumerate(chunks):
        text = "\n".join(chunk)
        key = cache_key(kind, template.sha256, model, params, framed(text))
        hit = cache_get(data_dir, key) if data_dir else None
        if hit:
            contents.append(hit["content"])
            hits += 1
        else:
            if on_event:
                on_event("chunk", i + 1, len(chunks))
            attempts = await call_validated(chat, kind, prompt(text), schema, validator(text), max_tokens, on_event)
            if not attempts[-1].valid:
                return failed(attempts)
            calls.extend(attempts)
            contents.append(attempts[-1].content)
            if data_dir:
                cache_put(data_dir, key, {"content": attempts[-1].content})
        progress((i + 1) / total, f"{kind}: chunk {i + 1}/{len(chunks)}")
    merged = merge(kind, contents)
    if kind in REDUCE and len(contents) > 1:
        field_, target, red_max = REDUCE[kind]
        parts = "\n".join(f"- {c[field_]}" for c in contents)
        msgs = [{"role": "user", "content": REDUCE_PROMPT.format(what=field_, target=target, language=language,
                                                                 parts=parts)}]
        if on_event:
            on_event("chunk", None, None)
        red = await call_validated(chat, "reduce", msgs, REDUCE_SCHEMA, _reduce_errors, red_max, on_event)
        if not red[-1].valid:
            return failed(red)
        calls.extend(red)
        merged[field_] = red[-1].content["text"]
        progress(1.0, f"{kind}: reduce")
    content, v, u = quotes.verify(merged, full)
    agg = Attempt(first_prompt, json.dumps(merged, ensure_ascii=False), 0, 0, 0, calls[-1].finish_reason if calls else None,
                  {"chunks": len(chunks), "cache_hits": hits, "calls": len(calls)})
    agg.tokens_in, agg.tokens_out, agg.wall_ms = (sum(getattr(a, f) for a in calls) for f in
                                                 ("tokens_in", "tokens_out", "wall_ms"))
    agg.content = content
    return KindResult([agg], content, "map-reduce", len(chunks), hits, v, u, _sum([agg]))


def _reduce_errors(data):
    from jsonschema import Draft202012Validator
    return [e.message[:200] for e in Draft202012Validator(REDUCE_SCHEMA).iter_errors(data)]
