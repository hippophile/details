"""Markdown rendered from stored JSON only (deterministic; never parsed from model text). Checklist state is read at
render time: `- [x]` / `- [ ]`. Item text is collapsed to one line so model text cannot add structure.
An item with a quote is followed by `  > “quote” [mm:ss]` (time only when the content was located: quotes.locate)."""
from .schemas import KINDS


def _one(s):
    return " ".join(str(s).split())


def _meta(item, *keys):
    parts = [f"{k}: {_one(item[k])}" for k in keys if item.get(k)]
    if item.get("verified") is False:
        parts.append("quote not found")
    return f" ({', '.join(parts)})" if parts else ""


def _clock(ms):
    s = int(ms) // 1000
    return f"{s // 3600}:{s % 3600 // 60:02}:{s % 60:02}" if s >= 3600 else f"{s // 60:02}:{s % 60:02}"


def _with_quote(line, item):
    if not item.get("quote"):
        return [line]
    at = f" [{_clock(item['at_ms'])}]" if isinstance(item.get("at_ms"), (int, float)) else ""
    return [line, f"  > “{_one(item['quote'])}”{at}"]


def _bullets(items, *keys):
    return [x for i in items for x in _with_quote(f"- {_one(i['text'])}{_meta(i, *keys)}", i)]


def _tasks(items):
    return [x for i in items for x in _with_quote(
        f"- [{'x' if i.get('done') else ' '}] {_one(i['text'])}{_meta(i, 'owner', 'due')}", i)]


def _section(heading, lines):
    return ["", f"## {heading}", ""] + lines if lines else []


def _brief_lines(c):
    by = {d["id"]: d for d in c["decisions"] if d.get("id")}
    at = lambda i: f" [{_clock(i['at_ms'])}]" if isinstance(i.get("at_ms"), (int, float)) else ""

    def decision(d, depth):
        pad = "  " * depth
        out = [f"{pad}- changed from: {_one(d['text'])}{at(d)}"] if depth else [
            f"- {_one(d['text'])}{_meta(d)}", *([f"  > “{_one(d['quote'])}”{at(d)}"] if d.get("quote") else [])]
        for r in d.get("replaces") or []:
            if r in by:
                out += decision(by[r], depth + 1)
        return out

    def position(p):
        who = _one(p["who"]) if p.get("who") else "Unknown speaker"
        return [f"  - {who}: {_one(p['stance'])}{_meta(p)}", *([f"    > “{_one(p['quote'])}”{at(p)}"] if p.get("quote") else [])]

    def disagreement(x):
        state = f"settled by: {_one(by[x['settled_by']]['text'])}" if x.get("settled_by") in by else "still open"
        return [f"- {_one(x['topic'])} ({state})"] + [l for p in x["positions"] for l in position(p)]

    final = [d for d in c["decisions"] if d.get("status") != "replaced"]
    decided = [l for d in final for l in decision(d, 0)] or ["Nothing was decided."]
    if c.get("links") == "failed":
        decided.insert(0, "_Could not work out which decisions replaced earlier ones._")
    lines = ["# Brief", "", _one(c["summary"])]
    if c.get("script_warnings"):
        lines += ["", "_Some text is in an unexpected alphabet._"]
    for heading, body in (("Decided", decided),
                          ("Disagreed", [l for x in c["disagreements"] for l in disagreement(x)] or ["No disagreements."]),
                          ("To do", _tasks(c["action_items"]) or ["Nothing to do."]),
                          ("Open questions", _bullets(c["open_questions"]) or ["No open questions."]),
                          ("Chapters", [f"- {'[' + _clock(i['at_ms']) + '] ' if isinstance(i.get('at_ms'), (int, float)) else ''}"
                                        f"{_one(i['title'])}" for i in c["chapters"]] or ["No chapters."])):
        lines += ["", f"## {heading}", ""] + body
    return lines


def render_output(kind, c):
    if kind == "brief":
        lines = _brief_lines(c)
    elif kind == "checklist":
        lines = [f"# {_one(c['title'])}", ""] + _tasks(c["items"])
    elif kind == "debrief":
        lines = ["# Debrief", "", _one(c["summary"])]
        lines += _section("Decisions", _bullets(c["decisions"]))
        lines += _section("Action items", _tasks(c["action_items"]))
        lines += _section("Risks", _bullets(c["risks"], "severity"))
        lines += _section("Open questions", _bullets(c["open_questions"]))
    elif kind == "notes":
        lines = [f"# {_one(c['title'])}"]
        for s in c["sections"]:
            lines += _section(_one(s["heading"]), _bullets(s["bullets"]))
    elif kind == "tags":
        lines = ["# Tags", ""] + [x for t in c["tags"] for x in _with_quote(f"- {_one(t['name'])}{_meta(t)}", t)]
    else:
        raise ValueError(f"unknown kind '{kind}'")
    return "\n".join(lines) + "\n"


def render_group(outputs):
    """outputs: [(kind, content)] -> one document, outputs in kind order, a blank line between."""
    order = {k: i for i, k in enumerate(KINDS)}
    return "\n".join(render_output(k, c) for k, c in sorted(outputs, key=lambda o: order[o[0]]))
