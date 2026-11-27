"""prompt-ab: template checks (due_year_in_transcript) inside the validate/retry path, parse-time errors."""
import asyncio, types

import pytest

from details.llm import checks, pipeline, prompts
from llm_helpers import SEGS, ScriptedChat, good

V2 = types.SimpleNamespace(id=None, name="checklist", version=2, body="{{transcript}}", sha256="b" * 64,
                           checks=("due_year_in_transcript",))
V1 = types.SimpleNamespace(id=None, name="checklist", version=1, body="{{transcript}}", sha256="a" * 64)   # no .checks


def gen(chat, mk_settings, template=V2, segments=SEGS, data_dir=None, **env):
    s = mk_settings(**env)

    async def go():
        return await pipeline.generate_kind(chat, settings=s, kind="checklist", template=template, model="m",
                                            context=100_000, segments=segments, language="Greek", title="T",
                                            data_dir=data_dir)
    return asyncio.run(asyncio.wait_for(go(), 20))


def reply(due, quote="Θα κάνουμε deploy στις 12 Νοεμβρίου"):
    c = good("checklist")
    c["items"] = [{"text": "Deploy", "owner": None, "due": due, "done": False, "quote": quote}]
    return c


def test_invented_year_is_rejected_and_the_error_names_it_and_goes_into_the_retry(mk_settings):
    chat = ScriptedChat(reply("2023-05-15"), reply(None))
    r = gen(chat, mk_settings)
    assert r.attempts[0].errors and "2023" in r.attempts[0].errors[0]
    assert len(chat.calls) == 2
    assert "2023" in chat.calls[1]["messages"][-1]["content"]


def test_retry_fixes_it(mk_settings):
    r = gen(ScriptedChat(reply("2023-05-15"), reply(None)), mk_settings)
    assert len(r.attempts) == 2 and r.attempts[1].valid and r.content["items"][0]["due"] is None


def test_still_wrong_after_the_retry_keeps_the_raw_text_and_has_no_content(mk_settings):
    r = gen(ScriptedChat(reply("2023-05-15"), reply("2023-06-01")), mk_settings)
    assert len(r.attempts) == 2 and not r.attempts[-1].valid and r.content is None
    assert "2023-06-01" in r.attempts[-1].text


def test_year_present_in_the_transcript_is_valid_on_the_first_attempt(mk_settings):
    segs = ["Θα κάνουμε deploy στις 15 Μαΐου 2026 σίγουρα."]
    chat = ScriptedChat(reply("2026-05-15", quote="deploy στις 15 Μαΐου 2026"))
    r = gen(chat, mk_settings, segments=segs)
    assert len(chat.calls) == 1 and r.attempts[0].valid and r.content["items"][0]["due"] == "2026-05-15"


def test_null_due_passes(mk_settings):
    chat = ScriptedChat(reply(None))
    r = gen(chat, mk_settings)
    assert len(chat.calls) == 1 and r.attempts[0].valid


def test_a_version_without_the_check_still_accepts_an_invented_year(mk_settings):
    chat = ScriptedChat(reply("2023-05-15"))
    r = gen(chat, mk_settings, template=V1)
    assert len(chat.calls) == 1 and r.attempts[0].valid and r.content["items"][0]["due"] == "2023-05-15"


LONG = [f"Segment {i} θα κάνουμε deploy στις {i} Νοεμβρίου και ελέγχουμε." for i in range(5)] + \
       ["Segment 5 θα κάνουμε deploy στις 5 Νοεμβρίου 2026 και ελέγχουμε."]
ENV = dict(DETAILS_LLM_CHARS_PER_TOKEN="1", DETAILS_LLM_SINGLE_PASS_MAX_TOKENS="100", DETAILS_LLM_CHUNK_TOKENS="130",
           DETAILS_LLM_CHUNK_OVERLAP_TOKENS="0")


def chunk_reply(year_for_all):
    def f(call):
        import re
        ids = sorted({int(w) for w in re.findall(r"Segment (\d+)", call["messages"][0]["content"])})
        c = good("checklist")
        c["items"] = [{"text": f"Deploy {i}", "owner": None, "due": f"{year_for_all}-11-0{i + 1}", "done": False,
                       "quote": f"θα κάνουμε deploy στις {i} Νοεμβρίου"} for i in ids]
        return c
    return f


def test_map_reduce_checks_each_chunk_against_its_own_text(mk_settings, tmp_path):
    """2026 appears only in the last chunk: the first chunk's reply with 2026 is rejected, its retry (null) is kept."""
    def first_bad_then_null(call):
        c = chunk_reply(2026)(call)
        if len(call["messages"]) > 1:                         # the retry
            for it in c["items"]:
                it["due"] = None
        return c
    chat = ScriptedChat(first_bad_then_null, first_bad_then_null, first_bad_then_null, first_bad_then_null,
                        default=first_bad_then_null)
    r = gen(chat, mk_settings, segments=LONG, data_dir=tmp_path, **ENV)
    assert r.mode == "map-reduce" and r.chunks == 3
    sent = [c for c in chat.calls if len(c["messages"]) == 1]
    retried = [c for c in chat.calls if len(c["messages"]) == 3]
    assert len(sent) == 3 and len(retried) == 2               # chunks 1 and 2 had no 2026 in their text
    assert all("2026" in c["messages"][-1]["content"] for c in retried)


def test_cache_is_not_shared_between_prompt_versions(mk_settings, tmp_path):
    ok = lambda call: {**good("checklist"), "items": []}
    first = ScriptedChat(default=ok)
    r1 = gen(first, mk_settings, template=V1, segments=LONG, data_dir=tmp_path, **ENV)
    second = ScriptedChat(default=ok)
    r2 = gen(second, mk_settings, template=V2, segments=LONG, data_dir=tmp_path, **ENV)
    third = ScriptedChat(default=ok)
    r3 = gen(third, mk_settings, template=V1, segments=LONG, data_dir=tmp_path, **ENV)
    assert (r1.cache_hits, r2.cache_hits, r3.cache_hits) == (0, 0, 3)
    assert len(second.calls) == 3


# ---- the pure function ---------------------------------------------------------------------------------

def test_year_must_be_a_standalone_number():
    c = reply("2026-05-15")
    assert checks.run(["due_year_in_transcript"], "checklist", c, "ο κωδικός 20260 και 12026") != []
    assert checks.run(["due_year_in_transcript"], "checklist", c, "το έτος 2026.") == []
    assert checks.run(["due_year_in_transcript"], "checklist", c, "στις 15/5/2026") == []
    assert checks.run([], "checklist", c, "") == []
    assert checks.run(["due_year_in_transcript"], "tags", {"tags": []}, "") == []


# ---- front matter ---------------------------------------------------------------------------------------

FRONT = ("---\nname: checklist\nversion: {v}\nkind: checklist\noutput_schema: checklist.v1\n{extra}---\n"
         "Text\n{{{{transcript}}}}\n")


def test_checks_are_read_from_the_front_matter(tmp_path):
    d = tmp_path / "checklist"
    d.mkdir()
    (d / "v2.md").write_text(FRONT.format(v=2, extra="checks: due_year_in_transcript\n"))
    (d / "v1.md").write_text(FRONT.format(v=1, extra=""))
    assert prompts.parse_template(d / "v2.md").checks == ("due_year_in_transcript",)
    assert prompts.parse_template(d / "v1.md").checks == ()


def test_unknown_check_names_the_file_and_the_check(tmp_path):
    d = tmp_path / "checklist"
    d.mkdir()
    (d / "v1.md").write_text(FRONT.format(v=1, extra="checks: nonsense\n"))
    with pytest.raises(prompts.PromptError, match=r"v1\.md.*nonsense"):
        prompts.parse_template(d / "v1.md")


# ---- known_scripts (monday-brief) ------------------------------------------------------------------------------

BRIEF_V1 = types.SimpleNamespace(id=None, name="brief", version=1, body="{{transcript}}", sha256="d" * 64,
                                 checks=("known_scripts",))
BENGALI = "ορθোগ্রাফία"


def run_check(data):
    return checks.run(("known_scripts",), "brief", data, "")


def test_known_scripts_is_a_registered_check_name():
    assert "known_scripts" in checks.NAMES


def test_bengali_inside_a_greek_word_in_the_summary_is_an_error_naming_the_field():
    errs = run_check(good("brief", summary=f"Η {BENGALI} του κειμένου"))
    assert len(errs) == 1 and errs[0].startswith("summary") and "Bengali" in errs[0]


def test_the_path_is_named_for_nested_fields():
    c = good("brief")
    c["decisions"][0]["text"] = "Vietnamese ở đây Việt"          # Latin with marks only: allowed
    c["chapters"][0]["title"] = "Глава"
    errs = run_check(c)
    assert len(errs) == 1 and errs[0].startswith("chapters/0/title") and "Cyrillic" in errs[0]


def test_quote_who_and_owner_are_exempt():
    c = good("brief")
    c["decisions"][0]["quote"] = BENGALI
    c["action_items"][0]["owner"] = "Дмитрий"
    c["disagreements"] = [{"topic": "t", "positions": [{"who": "李明", "stance": "s", "quote": BENGALI},
                                                       {"who": None, "stance": "s", "quote": "q"}]}]
    assert run_check(c) == []


def test_greek_latin_digits_punctuation_and_symbols_pass():
    ok = "Ελληνικά, English, ß é ñ ΐ ά, 12 € → 5 % — “x” (a/b) ✓ µ"
    assert run_check(good("brief", summary=ok)) == []


def test_chinese_and_japanese_are_flagged():
    assert run_check(good("brief", summary="决定")) and run_check(good("brief", summary="ひらがな"))


def reply_with_bengali():
    return good("brief", summary=f"Η {BENGALI}")


def genb(chat, mk_settings, segments=SEGS):
    s = mk_settings()

    async def go():
        return await pipeline.generate_kind(chat, settings=s, kind="brief", template=BRIEF_V1, model="m",
                                            context=100_000, segments=segments, language="Greek", title="T",
                                            data_dir=None, timed_segments=[(i * 1000, t) for i, t in enumerate(segments)])
    return asyncio.run(asyncio.wait_for(go(), 20))


def test_the_error_goes_into_the_one_retry_and_a_clean_second_reply_has_no_warning(mk_settings):
    chat = ScriptedChat(reply_with_bengali(), good("brief"))
    r = genb(chat, mk_settings)
    assert len(chat.calls) == 2 and "Bengali" in chat.calls[1]["messages"][-1]["content"]
    assert r.attempts[0].errors and r.attempts[1].valid and "script_warnings" not in r.content


def test_still_foreign_after_the_retry_stores_the_brief_with_script_warnings(mk_settings):
    chat = ScriptedChat(reply_with_bengali(), reply_with_bengali())
    r = genb(chat, mk_settings)
    assert len(chat.calls) == 2 and r.content is not None
    assert r.content["summary"].endswith(BENGALI) and r.content["script_warnings"] == ["summary"]
    assert r.attempts[-1].valid                                         # not a schema failure
    from details.llm import schemas
    assert schemas.validate("brief", r.content, stored=True) == []


def test_a_schema_error_after_the_retry_is_still_a_failure(mk_settings):
    bad = reply_with_bengali()
    bad["decisions"][0].pop("quote")
    r = genb(ScriptedChat(bad, bad), mk_settings)
    assert r.content is None and not r.attempts[-1].valid
