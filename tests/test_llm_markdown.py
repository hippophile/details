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


# ---- brief (monday-brief) ------------------------------------------------------------------------------------

def stored_brief():
    return {"schema_version": 1, "kind_of_recording": "meeting", "summary": "Συζήτηση για το deploy.", "links": "ok",
            "chapters": [{"title": "Έναρξη", "quote": "Καλημέρα σε όλους", "verified": True, "at_ms": 0},
                         {"title": "Deploy", "quote": "Θα κάνουμε deploy", "verified": True, "at_ms": 2161000}],
            "decisions": [{"id": "d1", "text": "Deploy σε όλα τα υποκαταστήματα", "quote": "θα βγούμε παντού",
                           "verified": True, "at_ms": 2102000, "status": "replaced", "replaces": [], "replaced_by": "d2"},
                          {"id": "d2", "text": "Deploy πρώτα σε 2 υποκαταστήματα", "quote": "πρώτα σε δύο",
                           "verified": True, "at_ms": 9670000, "status": "final", "replaces": ["d1"], "replaced_by": None}],
            "disagreements": [{"id": "x1", "topic": "Budget", "settled_by": None, "positions": [
                {"who": "Φίλιππος", "stance": "Πολύ ακριβό", "quote": "είναι ακριβό", "verified": True, "at_ms": 4323000},
                {"who": None, "stance": "Αξίζει", "quote": "αξίζει", "verified": False}]}],
            "action_items": [{"text": "Ετοιμασία documentation", "owner": "Νίκος", "due": "2026-12-01", "done": False,
                              "quote": "Ο Νίκος θα ετοιμάσει το documentation", "verified": True, "at_ms": 10212000}],
            "open_questions": [{"text": "Ποιο μοντέλο;", "quote": "ποιο μοντέλο θα χρησιμοποιήσουμε",
                                "verified": True, "at_ms": 6055000}]}


def test_brief_golden():
    out = markdown.render_output("brief", stored_brief())
    assert out == (GOLDEN / "brief.md").read_text()
    assert markdown.render_output("brief", stored_brief()).encode() == out.encode()


def test_a_lesson_says_so_in_every_empty_section():
    c = stored_brief()
    c.update(kind_of_recording="lesson", decisions=[], disagreements=[], action_items=[], open_questions=[], chapters=[],
             links="skipped")
    out = markdown.render_output("brief", c)
    for line in ("Nothing was decided.", "No disagreements.", "Nothing to do.", "No open questions.", "No chapters."):
        assert line in out, line
    assert "changed from" not in out


def test_a_settled_disagreement_names_the_decision_and_replaced_ones_are_not_listed_on_their_own():
    c = stored_brief()
    c["disagreements"][0]["settled_by"] = "d2"
    out = markdown.render_output("brief", c)
    assert "- Budget (settled by: Deploy πρώτα σε 2 υποκαταστήματα)" in out and "still open" not in out
    decided = out.split("## Decided")[1].split("## Disagreed")[0]
    assert decided.count("\n- ") == 1                       # only the final decision is a top-level bullet


def test_a_chain_of_changes_nests_one_level_deeper_each_time():
    c = stored_brief()
    c["decisions"].append({"id": "d3", "text": "Μόνο 1", "quote": "μόνο ένα", "verified": True, "at_ms": 9999000,
                           "status": "final", "replaces": ["d2"], "replaced_by": None})
    c["decisions"][1].update(status="replaced", replaced_by="d3")
    decided = markdown.render_output("brief", c).split("## Decided")[1].split("## Disagreed")[0]
    assert "  - changed from: Deploy πρώτα σε 2 υποκαταστήματα [2:41:10]\n    - changed from: Deploy σε όλα" in decided


def test_failed_links_and_script_warnings_are_said_in_the_export():
    c = stored_brief()
    c.update(links="failed", script_warnings=["summary"])
    out = markdown.render_output("brief", c)
    assert "Could not work out which decisions replaced earlier ones." in out
    assert "Some text is in an unexpected alphabet." in out


def test_model_text_cannot_inject_headings_into_a_brief():
    c = stored_brief()
    c["decisions"][1]["text"] = "x\n# fake"
    c["summary"] = "a\n## fake"
    out = markdown.render_output("brief", c)
    assert "\n# fake" not in out and "\n## fake" not in out


def test_group_puts_the_brief_first():
    out = markdown.render_group([("tags", stored("tags")), ("brief", stored_brief())])
    assert out.startswith("# Brief") and out.index("# Brief") < out.index("# Tags")
