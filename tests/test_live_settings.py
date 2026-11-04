"""[live] settings: defaults, env override, validation, registry checks at startup."""
import pytest

from conftest import make_settings
from details.app import create_app
from details.settings import SettingsError


def test_shipped_defaults(tmp_path):
    s = make_settings(tmp_path).live
    assert (s.notes_every_min, s.stt_model, s.llm_model, s.language) == (2, "whisper-turbo", "gemma-cheap", "el")
    assert (s.partials, s.vocab_hint) == (True, False)
    assert (s.max_backlog_s, s.max_transcript_tokens, s.llm_timeout_s) == (60, 4000, 90)
    assert (s.stt_timeout_s, s.stt_load_timeout_s) == (30, 120)
    assert s.test_mode is False and s.test_interval_s == 0


def test_env_override(tmp_path):
    assert make_settings(tmp_path, DETAILS_LIVE_NOTES_EVERY_MIN="0.5").live.notes_every_min == 0.5
    assert make_settings(tmp_path, DETAILS_LIVE_PARTIALS="false").live.partials is False


@pytest.mark.parametrize("key,val", [("NOTES_EVERY_MIN", "0"), ("NOTES_EVERY_MIN", "99"), ("NOTES_EVERY_MIN", "0.4"),
                                     ("MAX_BACKLOG_S", "0"), ("MAX_TRANSCRIPT_TOKENS", "10"), ("LLM_TIMEOUT_S", "0"),
                                     ("STT_TIMEOUT_S", "0"), ("STT_LOAD_TIMEOUT_S", "0")])
def test_invalid_values_name_the_key(tmp_path, key, val):
    with pytest.raises(SettingsError, match=f"live.{key.lower()}"):
        make_settings(tmp_path, **{f"DETAILS_LIVE_{key}": val})


def test_interval_override_needs_test_mode(tmp_path):
    with pytest.raises(SettingsError, match="live.test_interval_s"):
        make_settings(tmp_path, DETAILS_LIVE_TEST_INTERVAL_S="2")
    s = make_settings(tmp_path, DETAILS_LIVE_TEST_MODE="1", DETAILS_LIVE_TEST_INTERVAL_S="2").live
    assert s.test_interval_s == 2


def test_unknown_llm_model_fails_startup_naming_the_key(tmp_path):
    with pytest.raises(Exception, match="live.llm_model"):
        create_app(make_settings(tmp_path, DETAILS_LIVE_LLM_MODEL="nope"))
    with pytest.raises(Exception, match="live.llm_model"):
        create_app(make_settings(tmp_path, DETAILS_LIVE_LLM_MODEL="whisper-turbo"))     # an STT id


def test_unknown_stt_model_fails_startup_naming_the_key(tmp_path):
    with pytest.raises(Exception, match="live.stt_model"):
        create_app(make_settings(tmp_path, DETAILS_LIVE_STT_MODEL="nope"))


def test_a_non_cheap_or_non_fast_model_still_starts_the_app(tmp_path):
    """Refused per session with a message (zero loads), not a startup error."""
    create_app(make_settings(tmp_path, DETAILS_LIVE_LLM_MODEL="gemma-nice", DETAILS_LIVE_STT_MODEL="whisper-large"))
