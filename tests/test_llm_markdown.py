"""llm-outputs: Markdown rendered from JSON only, deterministic, golden files."""
import pathlib

import pytest

from details.llm import markdown
from llm_helpers import good

GOLDEN = pathlib.Path(__file__).parent / "golden"


def stored(kind):
    c = good(kind)

    n = [60]

    def walk(o):
        if isinstance(o, dict):
            if "quote" in o:
                o["verified"] = True
                n[0] += 1
                o["at_ms"] = n[0] * 1000
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(c)
    if kind == "checklist":
        c["items"][0]["done"] = True
        c["items"][2]["verified"] = False
        del c["items"][2]["at_ms"]
    if kind == "debrief":
        c["risks"][0]["verified"] = False
        del c["risks"][0]["at_ms"]
    return c


@pytest.mark.parametrize("kind", ["checklist", "debrief", "notes", "tags"])
def test_golden(kind):
    out = markdown.render_output(kind, stored(kind))
    assert out == (GOLDEN / f"{kind}.md").read_text()
    assert markdown.render_output(kind, stored(kind)).encode() == out.encode()      # byte-identical


def test_checklist_state_is_read_at_render_time():
    c = stored("checklist")
    assert "- [x] Deploy" in markdown.render_output("checklist", c)
    c["items"][0]["done"] = False
    assert "- [ ] Deploy" in markdown.render_output("checklist", c)


def test_group_joins_outputs_in_kind_order():
    out = markdown.render_group([("tags", stored("tags")), ("checklist", stored("checklist"))])
    assert out == (GOLDEN / "group.md").read_text()


def test_model_text_cannot_inject_headings_into_structure():
    c = stored("checklist")
    c["items"][0]["text"] = "line one\n# fake heading"
    assert "\n# fake heading" not in markdown.render_output("checklist", c)


def test_quote_line_has_time_only_when_located_and_marker_replaces_unverified():
    c = {"title": "T", "items": [{"text": "a", "done": False, "quote": "x\ny", "verified": True, "at_ms": 3723000},
                                 {"text": "b", "done": False, "quote": "q", "verified": True},
                                 {"text": "c", "done": False, "quote": "z", "verified": False},
                                 {"text": "d", "done": False}]}
    lines = markdown.render_output("checklist", c).splitlines()
    assert lines[2:] == ["- [ ] a", "  > “x y” [1:02:03]", "- [ ] b", "  > “q”", "- [ ] c (quote not found)", "  > “z”",
                         "- [ ] d"]
