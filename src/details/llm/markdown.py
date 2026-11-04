"""Markdown rendered from stored JSON only (deterministic; never parsed from model text). Checklist state is read at
render time: `- [x]` / `- [ ]`. Item text is collapsed to one line so model text cannot add structure."""
from .schemas import KINDS


def _one(s):
    return " ".join(str(s).split())


def _meta(item, *keys):
    parts = [f"{k}: {_one(item[k])}" for k in keys if item.get(k)]
    if item.get("verified") is False:
        parts.append("unverified")
    return f" ({', '.join(parts)})" if parts else ""


def _bullets(items, *keys):
    return [f"- {_one(i['text'])}{_meta(i, *keys)}" for i in items]


def _tasks(items):
    return [f"- [{'x' if i.get('done') else ' '}] {_one(i['text'])}{_meta(i, 'owner', 'due')}" for i in items]


def _section(heading, lines):
    return ["", f"## {heading}", ""] + lines if lines else []


def render_output(kind, c):
    if kind == "checklist":
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
        lines = ["# Tags", ""] + [f"- {_one(t['name'])}" for t in c["tags"]]
    else:
        raise ValueError(f"unknown kind '{kind}'")
    return "\n".join(lines) + "\n"


def render_group(outputs):
    """outputs: [(kind, content)] -> one document, outputs in kind order, a blank line between."""
    order = {k: i for i, k in enumerate(KINDS)}
    return "\n".join(render_output(k, c) for k, c in sorted(outputs, key=lambda o: order[o[0]]))
