"""llm-outputs: JSON Schemas per kind."""
import hashlib, json

import pytest

from details.llm import schemas
from llm_helpers import GOOD, good


def test_five_kinds_brief_first():
    assert schemas.KINDS == ("brief", "debrief", "notes", "checklist", "tags")
    for k in schemas.KINDS:
        s = schemas.load_schema(k)
        assert s["additionalProperties"] is False and s["properties"]["schema_version"]["const"] == 1


@pytest.mark.parametrize("kind", ["brief", "debrief", "notes", "checklist", "tags"])
def test_good_fixture_passes(kind):
    assert schemas.validate(kind, good(kind)) == []


def test_checklist_due_null_and_iso_date_ok():
    c = good("checklist")
    assert c["items"][0]["due"] == "2026-11-12" and c["items"][0]["owner"] is None
    assert schemas.validate("checklist", c) == []


def mut(kind, fn):
    c = good(kind)
    fn(c)
    return schemas.validate(kind, c)


@pytest.mark.parametrize("fn", [
    lambda c: c["items"][0].update(due="next week"),
    lambda c: c["items"][0].update(due="2026-13-45x"),
    lambda c: c["items"][0].pop("quote"),
    lambda c: c["items"][0].update(extra=1),
    lambda c: c.update(extra=1),
    lambda c: c["items"][0].update(done="yes"),
    lambda c: c.update(schema_version=2),
])
def test_bad_checklists_fail(fn):
    assert mut("checklist", fn)


@pytest.mark.parametrize("kind,fn", [
    ("debrief", lambda c: c["decisions"][0].pop("quote")),
    ("debrief", lambda c: c["risks"][0].update(severity="huge")),
    ("notes", lambda c: c["sections"][0]["bullets"][0].pop("quote")),
    ("tags", lambda c: c["tags"][0].update(name="Not Kebab")),
    ("tags", lambda c: c.update(tags=[{"name": f"t{i}", "quote": "q"} for i in range(9)])),
])
def test_other_bad_fixtures_fail(kind, fn):
    assert mut(kind, fn)


def test_greek_kebab_tag_accepted():
    c = good("tags")
    c["tags"][0]["name"] = "ασφάλεια-δεδομένων"
    assert schemas.validate("tags", c) == []


def test_verified_is_code_written_only():
    c = good("checklist")
    c["items"][0]["verified"] = True
    assert schemas.validate("checklist", c, stored=True) == []
    assert schemas.validate("checklist", c)        # a model must not write it


def test_model_schema_has_no_verified_property_anywhere():
    for k in schemas.KINDS:
        assert "verified" not in str(schemas.model_schema(k))


# ---- brief (monday-brief) ------------------------------------------------------------------------------------

CODE_FIELDS = {"verified", "id", "status", "replaces", "replaced_by", "settled_by", "links", "script_warnings"}
PINNED = {"debrief": "992ea8b8eb95e9ea444e0fd602808e00042ad7ed7445a69edc5864d67b8d84f4",
          "notes": "79eee7b71d23678c562996c150e573521297d60b978cb19b369730ff442436d3",
          "checklist": "f6108714511d1c573e9a76ed505d498fed64210e37f1862b4d64f69ecbc1cf5b",
          "tags": "f55c9ec83d2a399deda1e0ec8699c66f5352baced7c848e8d1cc35f0fecf9bfa"}


def pos(who="Φίλιππος", stance="όχι", quote="q"):
    return {"who": who, "stance": stance, "quote": quote}


def brief_with(**over):
    c = good("brief")
    c["disagreements"] = [{"topic": "Budget", "positions": [pos(), pos("Γιάννης", "ναι")]}]
    c.update(over)
    return c


def test_brief_good_with_disagreement_and_lesson_with_empty_lists_pass():
    assert schemas.validate("brief", brief_with()) == []
    lesson = good("brief", kind_of_recording="lesson", decisions=[], disagreements=[], action_items=[])
    assert schemas.validate("brief", lesson) == []


@pytest.mark.parametrize("fn", [
    lambda c: c["disagreements"][0].update(positions=[pos()]),                       # one side only
    lambda c: c["disagreements"][0]["positions"][0].pop("quote"),
    lambda c: c.update(kind_of_recording="webinar"),
    lambda c: c.pop("kind_of_recording"),
    lambda c: c["decisions"][0].pop("quote"),
    lambda c: c["chapters"][0].pop("quote"),
    lambda c: c["action_items"][0].update(done="no"),
    lambda c: c["decisions"][0].update(status="final"),                              # code-written: not from the model
    lambda c: c.update(links="ok"),
    lambda c: c.update(extra=1),
])
def test_bad_briefs_fail(fn):
    c = brief_with()
    fn(c)
    assert schemas.validate("brief", c)


def test_brief_who_may_be_null():
    c = brief_with()
    c["disagreements"][0]["positions"][0]["who"] = None
    assert schemas.validate("brief", c) == []


def test_brief_stored_form_allows_the_code_written_fields():
    c = brief_with(links="ok", script_warnings=["summary"])
    c["decisions"][0].update(id="d1", status="final", replaces=[], replaced_by=None, verified=True)
    c["disagreements"][0].update(id="x1", settled_by=None)
    assert schemas.validate("brief", c, stored=True) == []
    c["decisions"][0]["status"] = "maybe"
    assert schemas.validate("brief", c, stored=True)


def test_brief_model_schema_has_none_of_the_code_written_fields():
    names = set()

    def walk(n):
        if isinstance(n, dict):
            names.update(n.get("properties", {}))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(schemas.model_schema("brief"))
    assert not names & CODE_FIELDS and {"kind_of_recording", "positions", "who"} <= names


@pytest.mark.parametrize("kind", sorted(PINNED))
def test_the_four_existing_model_schemas_are_byte_identical_to_before(kind):
    got = hashlib.sha256(json.dumps(schemas.model_schema(kind), sort_keys=True).encode()).hexdigest()
    assert got == PINNED[kind]
