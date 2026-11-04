"""The AI temperature comes from config ([llm] temperature / DETAILS_LLM_TEMPERATURE), not from code."""
import pytest
from fakes.lmstudio import FakeLMStudio
from llm_helpers import good
from test_llm_client import call

from details.settings import SettingsError


def test_default_temperature_is_from_config(mk_settings):
    assert mk_settings().llm.temperature == 0.1


def test_env_override_reaches_the_request(mk_settings):
    fake = FakeLMStudio().script(good("tags"))
    call(fake, mk_settings, DETAILS_LLM_TEMPERATURE="0.3")
    assert fake.requests[0]["temperature"] == 0.3


@pytest.mark.parametrize("bad", ["-0.1", "2.5"])
def test_out_of_range_temperature_is_refused(mk_settings, bad):
    with pytest.raises(SettingsError):
        mk_settings(DETAILS_LLM_TEMPERATURE=bad)
