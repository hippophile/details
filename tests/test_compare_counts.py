"""compare-runs: output_counts table (pure)."""
import pytest

from compare_helpers import good_checklist
from details.services.comparison import output_counts
from llm_helpers import good


def test_debrief_counts_and_unverified():
    c = good("debrief")
    c["risks"].append({"text": "x", "severity": "low", "quote": "q", "verified": False})
    c["decisions"][0]["verified"] = True
    assert output_counts("debrief", c) == {"decisions": 1, "action_items": 1, "risks": 2, "open_questions": 1,
                                           "unverified": 1}


def test_notes_counts_sections_and_bullets():
    c = good("notes")
    c["sections"].append({"heading": "h", "bullets": [{"text": "a", "quote": "q", "verified": False},
                                                       {"text": "b", "quote": "q"}]})
    assert output_counts("notes", c) == {"sections": 2, "bullets": 3, "unverified": 1}


def test_checklist_items_done_unverified():
    c = good_checklist(5, 2)
    c["items"][4]["verified"] = False
    assert output_counts("checklist", c) == {"items": 5, "done": 2, "with_due": 0, "unverified": 1}


def test_tags_counts():
    c = good("tags")
    c["tags"][0]["verified"] = False
    assert output_counts("tags", c) == {"tags": 2, "unverified": 1}


@pytest.mark.parametrize("kind", ["debrief", "notes", "checklist", "tags"])
def test_empty_or_odd_content_never_raises(kind):
    counts = output_counts(kind, {})
    assert counts["unverified"] == 0 and all(v == 0 for v in counts.values())
    assert output_counts(kind, None)["unverified"] == 0


def test_checklist_with_due_counts_items_that_have_a_due_date():
    c = good_checklist(4, 0)
    c["items"][0]["due"] = "2026-11-12"
    c["items"][2]["due"] = "2026-12-01"
    assert output_counts("checklist", c)["with_due"] == 2
    assert output_counts("checklist", good_checklist(3, 1))["with_due"] == 0
