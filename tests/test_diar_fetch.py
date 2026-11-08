"""speakers-diarization: `python -m details.diar_fetch` (make diar-model) with a fake downloader. No network."""
import pytest

from conftest import make_settings
from diar_helpers import SENTINEL

REPO = "pyannote/speaker-diarization-community-1"


class GatedRepoError(Exception):
    """Same name as huggingface_hub's, which is what the real downloader raises for an unaccepted model."""


def test_no_token_exits_nonzero_says_where_to_put_it_and_never_downloads(tmp_path, capsys):
    from details import diar_fetch
    calls = []
    rc = diar_fetch.main(settings=make_settings(tmp_path), downloader=lambda *a, **k: calls.append((a, k)))
    out = capsys.readouterr()
    assert rc != 0 and calls == []
    assert "HF_TOKEN" in out.err + out.out and ".env" in out.err + out.out


def test_downloader_gets_the_configured_cache_repo_and_token(tmp_path, capsys):
    from details import diar_fetch
    hub = tmp_path / "myhub"
    s = make_settings(tmp_path, DETAILS_PATHS_HF_HUB=str(hub), HF_TOKEN=SENTINEL)
    calls = []

    def fake(repo, **kw):
        calls.append((repo, kw))
        return str(hub / "snap")
    rc = diar_fetch.main(settings=s, downloader=fake)
    out = capsys.readouterr()
    assert rc == 0 and len(calls) == 1
    assert calls[0][0] == REPO and calls[0][1]["cache_dir"] == hub and calls[0][1]["token"] == SENTINEL
    assert SENTINEL not in out.out + out.err


def test_gated_repo_error_tells_to_accept_the_terms_and_masks_the_token(tmp_path, capsys):
    from details import diar_fetch
    s = make_settings(tmp_path, HF_TOKEN=SENTINEL)

    def fake(repo, **kw):
        raise GatedRepoError(f"401 Client Error. Access to {repo} is restricted. token={SENTINEL} Bearer {SENTINEL}")
    rc = diar_fetch.main(settings=s, downloader=fake)
    out = capsys.readouterr()
    text = out.out + out.err
    assert rc != 0 and "terms" in text and f"https://huggingface.co/{REPO}" in text
    assert SENTINEL not in text


def test_other_errors_are_plain_redacted_and_nonzero(tmp_path, capsys):
    from details import diar_fetch
    s = make_settings(tmp_path, HF_TOKEN="plainTokenValue123")

    def fake(repo, **kw):
        raise OSError("disk full while using plainTokenValue123")
    rc = diar_fetch.main(settings=s, downloader=fake)
    out = capsys.readouterr()
    assert rc != 0 and "disk full" in out.err + out.out and "plainTokenValue123" not in out.err + out.out


def test_unknown_configured_model_is_a_plain_error(tmp_path, capsys):
    from details import diar_fetch
    s = make_settings(tmp_path, HF_TOKEN=SENTINEL, DETAILS_DIARIZATION_MODEL="nope")
    rc = diar_fetch.main(settings=s, downloader=lambda *a, **k: pytest.fail("must not download"))
    assert rc != 0 and "diarization.model" in capsys.readouterr().err


def test_makefile_targets_exist_and_default_setup_is_unchanged():
    from details.settings import REPO_ROOT
    mk = (REPO_ROOT / "Makefile").read_text()
    assert "setup-diar:" in mk and "requirements-diar.txt" in mk and "diar-model:" in mk and "details.diar_fetch" in mk
    setup = mk.split("setup:")[1].split("\n\n")[0]
    assert "requirements-diar" not in setup and "pyannote" not in setup
    assert "pyannote" not in (REPO_ROOT / "requirements.txt").read_text()
    assert "pyannote" in (REPO_ROOT / "requirements-diar.txt").read_text()
