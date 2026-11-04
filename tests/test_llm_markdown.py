"""llm-outputs: Markdown rendered from JSON only, deterministic, golden files."""
import pathlib

import pytest

from details.llm import markdown
from llm_helpers import good

GOLDEN = pathlib.Path(__file__).parent / "golden"


def stored(kind):
    c = good(kind)

    def walk(o):
        if isinstance(o, dict):
            if "quote" in o:
                o["verified"] = True
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(c)
    if kind == "checklist":
        c["items"][0]["done"] = True
        c["items"][2]["verified"] = False
    if kind == "debrief":
        c["risks"][0]["verified"] = False
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
