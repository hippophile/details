"""One output kind from one transcript: render prompt, call the model, validate, retry ONCE with the error text,
never repair, verify quotes. Long transcripts: map over chunks (cached on disk), merge lists in code, and for debrief,
notes and brief one small reduce call for summary/title. A brief then gets ids and order from code and one small
`brief_link` call (which decision replaces which); code writes the links. Pure with respect to the DB: the step
persists the result."""
import copy, json, time
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator

from . import checks, quotes, schemas
from .chunking import cache_get, cache_key, cache_put, estimate_tokens, split_segments
from .client import LLMError
from .markdown import _clock
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
          "notes": ("title", "short title", 100),
          "brief": ("summary", "coherent summary of 3-6 sentences", 600)}
LINK_SCHEMA = {"type": "object", "properties": {
    "decisions": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "replaces": {"type": "array", "items": {"type": "string"}}},
        "required": ["id", "replaces"], "additionalProperties": False}},
    "disagreements": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "settled_by": {"type": ["string", "null"]}},
        "required": ["id", "settled_by"], "additionalProperties": False}}},
    "required": ["decisions", "disagreements"], "additionalProperties": False}
LINK_PROMPT = ("Below are the numbered decisions and disagreements of one recording, in time order, each with the time "
               "it was said. Decide ONLY two things. 1) Which decisions replace EARLIER decisions: a later decision that "
               "changes or cancels an earlier one replaces it; list a decision only if it replaces something. 2) Which "
               "decision settles which disagreement: only when a decision resolves that topic, else null. Use only the "
               "ids shown; never add, drop or reword an item. Reply with JSON only, like "
               '{{"decisions": [{{"id": "d2", "replaces": ["d1"]}}], "disagreements": [{{"id": "x1", "settled_by": "d2"}}]}}'
               "\n\nDecisions:\n{decisions}\n\nDisagreements:\n{disagreements}\n")
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
    params: dict = field(default_factory=dict)       # extra run params to store (brief: `link`)


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
    elif kind == "brief":
        for k in ("decisions", "action_items", "open_questions"):
            out[k] = _dedupe([i for c in contents for i in c[k]], "text")
        topics = {}
        for c in contents:
            for d in c["disagreements"]:
                t = topics.setdefault(norm(d["topic"]), {**d, "positions": []})
                for p in d["positions"]:
                    if not any((norm(q["who"] or ""), norm(q["stance"])) == (norm(p["who"] or ""), norm(p["stance"]))
                               for q in t["positions"]):
                        t["positions"].append(p)
        out["disagreements"] = list(topics.values())
        out["chapters"] = []
        for ch in (x for c in contents for x in c["chapters"]):
            if not out["chapters"] or norm(out["chapters"][-1]["title"]) != norm(ch["title"]):
                out["chapters"].append(ch)
        votes = [c["kind_of_recording"] for c in contents]
        out["kind_of_recording"] = max(dict.fromkeys(votes), key=votes.count)      # ties: the first seen
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


def _when(node):
    """Earliest at_ms found inside a located item (None = no quote was found in the audio)."""
    ats = []

    def walk(n):
        if isinstance(n, dict):
            if isinstance(n.get("at_ms"), int):
                ats.append(n["at_ms"])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(node)
    return min(ats) if ats else None


def _assemble_brief(content, timed):
    """-> (content with decisions, disagreements and chapters sorted by position and numbered d1.., x1.., times of the
    decisions, times of the disagreements). Unlocated items keep the model's order after the located ones. Without
    timed segments nothing can be located: the model's order stays."""
    c = copy.deepcopy(content)
    loc = quotes.locate(c, timed) if timed else c
    times = {}
    for key in ("decisions", "disagreements", "chapters"):
        ts = [_when(x) for x in loc[key]]
        order = sorted(range(len(ts)), key=lambda i: (ts[i] is None, ts[i] or 0, i))
        c[key], times[key] = [c[key][i] for i in order], [ts[i] for i in order]
    c["decisions"] = [{"id": f"d{n}", **d} for n, d in enumerate(c["decisions"], 1)]
    c["disagreements"] = [{"id": f"x{n}", **d} for n, d in enumerate(c["disagreements"], 1)]
    return c, times["decisions"], times["disagreements"]


def _link_input(items, times, line):
    return "\n".join(f"{it['id']} [{_clock(t) if t is not None else '?'}] {line(it)}" for it, t in zip(items, times)) or "(none)"


def _link_errors(data, decisions, disagreements):
    errs = [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message[:200]}"
            for e in sorted(Draft202012Validator(LINK_SCHEMA).iter_errors(data), key=lambda e: list(e.absolute_path))[:10]]
    if errs:
        return errs
    pos = {d["id"]: i for i, d in enumerate(decisions)}
    known = ", ".join(pos) or "none"
    replaced, listed = {}, set()
    for n, e in enumerate(data["decisions"]):
        me = e["id"]
        if me not in pos:
            errs.append(f"decisions/{n}/id: '{me}' is not a decision id (decisions: {known})")
        elif me in listed:
            errs.append(f"decisions/{n}/id: '{me}' is listed twice")
        listed.add(me)
        for r in e["replaces"]:
            if r not in pos:
                errs.append(f"decisions/{n}/replaces: '{r}' is not a decision id (decisions: {known})")
            elif me not in pos:
                continue
            elif r == me:
                errs.append(f"decisions/{n}/replaces: '{me}' cannot replace itself")
            elif pos[r] > pos[me]:
                errs.append(f"decisions/{n}/replaces: '{r}' comes after '{me}'; a decision can only replace an EARLIER one")
            elif r in replaced:
                errs.append(f"decisions/{n}/replaces: '{r}' is already replaced by '{replaced[r]}'; a decision is replaced at most once")
            else:
                replaced[r] = me
    xs, seen = {x["id"] for x in disagreements}, set()
    for n, e in enumerate(data["disagreements"]):
        if e["id"] not in xs:
            errs.append(f"disagreements/{n}/id: '{e['id']}' is not a disagreement id (disagreements: {', '.join(sorted(xs)) or 'none'})")
        elif e["id"] in seen:
            errs.append(f"disagreements/{n}/id: '{e['id']}' is listed twice")
        seen.add(e["id"])
        if e["settled_by"] is not None and e["settled_by"] not in pos:
            errs.append(f"disagreements/{n}/settled_by: '{e['settled_by']}' is not a decision id (decisions: {known})")
    return errs


def _apply_link(content, data):
    """Code writes status/replaces/replaced_by/settled_by; `data` (validated) only says which. None = no links."""
    for d in content["decisions"]:
        d.update(status="final", replaces=[], replaced_by=None)
    for x in content["disagreements"]:
        x["settled_by"] = None
    if not data:
        return
    by = {d["id"]: d for d in content["decisions"]}
    for e in data["decisions"]:
        by[e["id"]]["replaces"] = list(e["replaces"])
        for r in e["replaces"]:
            by[r].update(status="replaced", replaced_by=e["id"])
    xs = {x["id"]: x for x in content["disagreements"]}
    for e in data["disagreements"]:
        xs[e["id"]]["settled_by"] = e["settled_by"]


async def _brief_finish(chat, content, timed, llm, on_event, names):
    """-> (stored brief content, params for the run, link Attempts). Never raises LLMError: a failed link keeps the
    brief with links "failed". StreamCancelled (a cancel) propagates."""
    c, dec_t, dis_t = _assemble_brief(content, timed)
    if "known_scripts" in names:
        warn = list(dict.fromkeys(p for p, _ in checks.script_issues(c)))
        if warn:
            c["script_warnings"] = warn
    decs, diss = c["decisions"], c["disagreements"]
    if not (len(decs) >= 2 or (diss and decs)):
        _apply_link(c, None)
        c["links"] = "skipped"
        return c, {}, []
    one = lambda s: " ".join(str(s).split())
    prompt = LINK_PROMPT.format(
        decisions=_link_input(decs, dec_t, lambda d: one(d["text"])),
        disagreements=_link_input(diss, dis_t, lambda x: one(x["topic"]) + ": " + " | ".join(
            f"{one(p['who'] or 'someone')}: {one(p['stance'])}" for p in x["positions"])))
    if on_event:
        on_event("chunk", None, None)
    info = {"prompt": prompt}
    try:
        att = await call_validated(chat, "brief_link", [{"role": "user", "content": prompt}], LINK_SCHEMA,
                                   lambda d: _link_errors(d, decs, diss), llm.max_tokens("brief_link"), on_event)
    except LLMError as e:
        _apply_link(c, None)
        c["links"] = "failed"
        return c, {"link": {**info, "status": "failed", "error": str(e)}}, []
    last = att[-1]
    ok = last.valid
    _apply_link(c, last.content if ok else None)
    c["links"] = "ok" if ok else "failed"
    info.update(status=c["links"], attempts=len(att), reply=last.text, errors=last.errors,
                **_sum(att))
    return c, {"link": info}, att


def _soften(attempts, kind, names, text):
    """known_scripts alone failing after the retry does not lose the output: the reply is kept (its script_warnings are
    written by code later). Any schema error or other check failure stays a failure."""
    last = attempts[-1]
    if last.valid or "known_scripts" not in names:
        return attempts
    try:
        data = json.loads(last.text)
    except ValueError:
        return attempts
    if schemas.validate(kind, data) or checks.run(tuple(n for n in names if n != "known_scripts"), kind, data, text):
        return attempts
    last.errors, last.content = None, data
    return attempts


async def generate_kind(chat, *, settings, kind, template, model, context, segments, language, title, data_dir,
                        on_progress=None, on_event=None, preface="", timed_segments=None):
    """on_event (optional, live view): ("chunk", i, n) before each map call (1-based), ("chunk", None, None) before
    the reduce call, plus call_validated's ("attempt_end", ...).
    preface (speakers-diarization): a fixed sentence placed at the start of the transcript text of EVERY prompt (single
    pass and each chunk), so a legend is never lost in later chunks. Quote checks still run against the plain text;
    the chunk cache key sees the prefaced text, so a plain and a labelled run never share an entry.
    timed_segments (brief): [(start_ms, plain text)] of the transcript; quotes are located in these (never in labelled
    lines) to order and number decisions and to give the link call its times."""
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
        attempts = _soften(await call_validated(chat, kind, prompt(full), schema, validator(full), max_tokens, on_event),
                           kind, names, full)
        last = attempts[-1]
        content, v, u = (None, 0, 0) if not last.valid else quotes.verify(last.content, full)
        extra = {}
        if kind == "brief" and content is not None:
            content, extra, link = await _brief_finish(chat, content, timed_segments, llm, on_event, names)
            for f in ("tokens_in", "tokens_out", "wall_ms"):       # the link call is folded into the kept attempt
                setattr(last, f, getattr(last, f) + sum(getattr(a, f) for a in link))
        return KindResult(attempts, content, "single", 1, 0, v, u, _sum(attempts), extra)

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
            attempts = _soften(await call_validated(chat, kind, prompt(text), schema, validator(text), max_tokens,
                                                    on_event), kind, names, text)
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
    finish, extra = calls[-1].finish_reason if calls else None, {}
    if kind == "brief":
        content, extra, link = await _brief_finish(chat, content, timed_segments, llm, on_event, names)
        calls.extend(link)
    agg = Attempt(first_prompt, json.dumps(merged, ensure_ascii=False), 0, 0, 0, finish,
                  {"chunks": len(chunks), "cache_hits": hits, "calls": len(calls)})
    agg.tokens_in, agg.tokens_out, agg.wall_ms = (sum(getattr(a, f) for a in calls) for f in
                                                 ("tokens_in", "tokens_out", "wall_ms"))
    agg.content = content
    return KindResult([agg], content, "map-reduce", len(chunks), hits, v, u, _sum([agg]), extra)


def _reduce_errors(data):
    from jsonschema import Draft202012Validator
    return [e.message[:200] for e in Draft202012Validator(REDUCE_SCHEMA).iter_errors(data)]
