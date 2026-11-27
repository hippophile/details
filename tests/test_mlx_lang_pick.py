"""stt-languages: pick_language (pure) and the mlx-whisper adapter's per-window language choice with a stub model."""
import numpy as np
import pytest

from details.engines.mlx_whisper import MlxWhisperEngine, pick_language


def test_window_stays_primary_when_the_other_language_is_below_the_threshold():
    lang, shares = pick_language({"el": .35, "en": .60, "de": .05, "is": .9}, "el", ["en", "de"], .7)
    assert lang == "el" and shares == {"el": .35, "en": .6, "de": .05}


def test_window_switches_when_the_share_reaches_the_threshold():
    lang, shares = pick_language({"el": .10, "en": .05, "de": .85}, "el", ["en", "de"], .7)
    assert lang == "de" and shares == {"el": .1, "en": .05, "de": .85}


@pytest.mark.parametrize("en,want", [(.69, "el"), (.71, "en")])
def test_threshold_edges(en, want):
    assert pick_language({"el": 1 - en, "en": en}, "el", ["en"], .7)[0] == want


def test_languages_outside_the_allowed_set_are_dropped_and_shares_renormalised():
    lang, shares = pick_language({"el": .1, "en": .1, "is": .8}, "el", ["en"], .7)
    assert shares == {"el": .5, "en": .5} and lang == "el"


def test_all_zero_probabilities_fall_back_to_the_primary():
    assert pick_language({"fr": 1.0}, "el", ["en"], .7)[0] == "el"


class Stub:
    def __init__(self, probs):
        self.probs, self.asked = probs, []

    def transcribe(self, x, **kw):
        self.asked.append(kw["language"])
        return {"language": kw["language"], "segments": []}


def engine(probs):
    e = MlxWhisperEngine({"repo": "x/y"})
    e._mlxw, e.audio = Stub(probs), np.zeros(16000 * 40, dtype=np.float32)
    e._detect = lambda x: probs
    return e


def test_adapter_decodes_in_the_picked_language_and_records_shares():
    e = engine({"el": .2, "en": .1, "de": .7, "fr": .5})
    e.transcribe_window("a.flac", 0, 30, {"language": "el", "also": ["en", "de"], "switch_min_share": .7})
    assert e._mlxw.asked == ["de"] and e.language == "de"
    assert e.lang_probs == {"el": .2, "en": .1, "de": .7}


def test_adapter_without_also_does_not_detect_and_keeps_the_requested_language():
    e = engine({})
    e._detect = lambda x: pytest.fail("no detection without also")
    e.transcribe_window("a.flac", 0, 30, {"language": "el"})
    assert e._mlxw.asked == ["el"] and e.lang_probs is None
