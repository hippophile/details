import asyncio, json, subprocess

import pytest

from conftest import make_settings
from details.llm.lmstudio_ctl import CliTransport, Controller, LMStudioError
from fakes.lmstudio import FakeLMStudio, fixture


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


def cli(tmp_path, record, **env):
    async def fake_run(argv):
        record.append(argv)
        return "[]"
    return CliTransport(make_settings(tmp_path, **env), run=fake_run)


def test_binary_comes_from_settings(tmp_path):
    rec = []
    run(cli(tmp_path, rec, DETAILS_LMSTUDIO_CLI="/opt/x/lms").list_downloaded())
    assert rec[0] == ["/opt/x/lms", "ls", "--json"]


def test_default_binary_expands_home(tmp_path):
    rec = []
    run(cli(tmp_path, rec).list_loaded())
    assert rec[0][0].endswith("/.lmstudio/bin/lms") and not rec[0][0].startswith("~") and rec[0][1:] == ["ps", "--json"]


def test_load_and_unload_commands(tmp_path):
    rec = []
    t = cli(tmp_path, rec, DETAILS_LMSTUDIO_CLI="/opt/x/lms")
    run(t.load("google/gemma-4-e4b", "details-gemma-cheap", 8192))
    run(t.unload("details-gemma-cheap"))
    assert rec[0] == ["/opt/x/lms", "load", "google/gemma-4-e4b", "--identifier", "details-gemma-cheap",
                      "-c", "8192", "-y"]
    assert rec[1] == ["/opt/x/lms", "unload", "details-gemma-cheap"]


def test_failed_command_raises_lmstudio_error(tmp_path):
    async def boom(argv):
        raise LMStudioError("lms exited 1")
    t = CliTransport(make_settings(tmp_path), run=boom)
    with pytest.raises(LMStudioError):
        run(t.list_downloaded())


def test_missing_binary_raises_lmstudio_error(tmp_path):
    t = CliTransport(make_settings(tmp_path, DETAILS_LMSTUDIO_CLI="/nonexistent/lms"))
    with pytest.raises(LMStudioError, match="/nonexistent/lms"):
        run(t.list_loaded())


def test_reachable_false_on_closed_port(tmp_path):
    t = CliTransport(make_settings(tmp_path, DETAILS_LMSTUDIO_URL="http://127.0.0.1:1"))
    assert run(t.reachable()) is False


class Canned:
    def __init__(self, ls, ps):
        self.ls, self.ps = ls, ps

    async def list_downloaded(self):
        return self.ls

    async def list_loaded(self):
        return self.ps


def test_parse_recorded_ps_and_ls():
    ctl = Controller(Canned(fixture("lms_ls.json"), fixture("lms_ps_loaded.json")))
    loaded = run(ctl.list_loaded())
    assert {(m.identifier, m.model_key) for m in loaded} == {("google/gemma-4-e4b", "google/gemma-4-e4b"),
                                                            ("details-spike", "google/gemma-4-e4b")}
    assert [m.context for m in loaded if m.identifier == "details-spike"] == [4096]
    keys = {m.key for m in run(ctl.list_downloaded())}
    assert {"google/gemma-4-e4b", "gemma-4-26b-a4b-it-qat-mlx"} <= keys


def test_controller_load_passes_key_identifier_context_unload_by_identifier():
    fake = FakeLMStudio()
    ctl = Controller(fake)
    run(ctl.load("gemma-4-26b-a4b-it-qat-mlx", "details-gemma-nice", 32768))
    run(ctl.unload("details-gemma-nice"))
    assert fake.calls == [("load", "gemma-4-26b-a4b-it-qat-mlx", "details-gemma-nice", 32768),
                          ("unload", "details-gemma-nice")]


@pytest.mark.gpu
def test_real_lms_lists_registry_models():
    """By hand: needs the installed lms + LM Studio models (pytest -m gpu)."""
    from details.services.models import load_registry
    from details.settings import REPO_ROOT, load_settings
    s = load_settings(dotenv_path=None)
    ctl = Controller(CliTransport(s))
    keys = {m.key for m in run(ctl.list_downloaded())}
    reg = load_registry(REPO_ROOT / "models.toml")
    assert {e.model for e in reg.llm.values()} <= keys
    run(ctl.list_loaded())
