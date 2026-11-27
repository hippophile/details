import pytest

from details.settings import SettingsError, load_settings

CFG = """
[server]
port = 8765
[paths]
data_dir = "~/details-data"
hf_hub = "~/.cache/huggingface/hub"
[lmstudio]
url = "http://localhost:1234"
cli = "/opt/lms"
exclusive = false
unload_on_terminal = true
[db]
url = ""
url_test = ""
connect_timeout = 5
[record]
sample_rate = 16000
sync_interval_s = 5
grace_s = 60
keep_wav = false
min_free_gb = 2
max_msg_mb = 1
ffmpeg = ""
ffprobe = ""
tz = "Europe/Athens"
[trash]
days = 30
[import]
max_mb = 2048
[models]
path = "models.toml"
[runner]
heartbeat_timeout_s = 120
cancel_grace_s = 5
log_max_lines = 5000
[stt]
window_s = 30
overlap_s = 3
vocab_hint = "hint"
plugins = ""
[llm]
single_pass_max_tokens = 20000
chunk_tokens = 8000
chunk_overlap_tokens = 200
chars_per_token = 2.5
timeout_s = 900
max_tokens_debrief = 2000
max_tokens_notes = 2000
max_tokens_checklist = 1500
max_tokens_tags = 400
stream = true
stream_idle_timeout_s = 60
"""


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(CFG)
    return p


def load(cfg, env=None, dotenv=None):
    return load_settings(cfg, env=env or {}, dotenv_path=dotenv)


def test_defaults_from_file(cfg):
    s = load(cfg)
    assert s.server.port == 8765
    assert s.paths.data_dir.is_absolute() and s.paths.data_dir.name == "details-data"
    assert "~" not in str(s.paths.data_dir)


def test_env_overrides_file(cfg):
    assert load(cfg, {"DETAILS_PORT": "9000"}).server.port == 9000
    assert load(cfg, {"DETAILS_SERVER_PORT": "9001", "DETAILS_PORT": "9000"}).server.port == 9001


def test_port_alias(cfg):
    assert load(cfg, {"PORT": "9100"}).server.port == 9100


def test_dotenv_does_not_override_env(cfg, tmp_path):
    d = tmp_path / ".env"
    d.write_text("# c\nDATABASE_URL=postgresql://a/details\nDETAILS_PORT='9200'\nexport X=1\n")
    s = load(cfg, {"DATABASE_URL": "postgresql://b/details"}, d)
    assert s.db.url == "postgresql://b/details"
    assert s.server.port == 9200  # .env fills what the environment lacks


def test_dotenv_missing_file_is_fine(cfg, tmp_path):
    assert load(cfg, dotenv=tmp_path / "nope").server.port == 8765


def test_bad_toml_names_file(tmp_path):
    p = tmp_path / "broken.toml"
    p.write_text("[server\nport = ")
    with pytest.raises(SettingsError, match="broken.toml"):
        load_settings(p, env={}, dotenv_path=None)


def test_nested_lmstudio_section(cfg):
    s = load(cfg, {"DETAILS_LMSTUDIO_URL": "http://localhost:5555"})
    assert s.lmstudio.url == "http://localhost:5555"
    assert s.lmstudio.cli == "/opt/lms"


def test_validation_hook_rejects_bad_port(cfg):
    with pytest.raises(SettingsError, match=r"server\.port"):
        load(cfg, {"DETAILS_SERVER_PORT": "notanumber"})
    with pytest.raises(SettingsError, match=r"server\.port"):
        load(cfg, {"DETAILS_SERVER_PORT": "70000"})


def test_hf_home_sets_hub(cfg):
    assert str(load(cfg, {"HF_HOME": "/x/hf"}).paths.hf_hub) == "/x/hf/hub"


def test_settings_is_frozen(cfg):
    with pytest.raises(Exception):
        load(cfg).server.port = 1


def test_record_section_defaults_env_and_validation():
    from details.settings import SettingsError, load_settings
    s = load_settings(env={}, dotenv_path=None)
    assert s.record.sample_rate == 16000 and s.record.keep_wav is False and s.record.grace_s == 60
    assert load_settings(env={"DETAILS_RECORD_GRACE_S": "5"}, dotenv_path=None).record.grace_s == 5
    assert load_settings(env={"DETAILS_RECORD_KEEP_WAV": "true"}, dotenv_path=None).record.keep_wav is True
    import pytest
    with pytest.raises(SettingsError, match="record.sample_rate"):
        load_settings(env={"DETAILS_RECORD_SAMPLE_RATE": "0"}, dotenv_path=None)


def test_stt_section_defaults_and_validation(cfg):
    s = load(cfg)
    assert s.stt.window_s == 30 and s.stt.plugins == ""
    with pytest.raises(SettingsError, match=r"stt\.window_s"):
        load(cfg, env={"DETAILS_STT_WINDOW_S": "0"})


def test_stt_overlap_s_loads_env_override_and_negative_rejected(cfg):
    assert load(cfg).stt.overlap_s == 3
    assert load(cfg, env={"DETAILS_STT_OVERLAP_S": "0"}).stt.overlap_s == 0
    assert load(cfg, env={"DETAILS_STT_OVERLAP_S": "1.5"}).stt.overlap_s == 1.5
    with pytest.raises(SettingsError, match=r"stt\.overlap_s"):
        load(cfg, env={"DETAILS_STT_OVERLAP_S": "-1"})


def test_llm_section_defaults_env_override_and_validation(cfg):
    s = load(cfg)
    assert s.llm.single_pass_max_tokens == 20000 and s.llm.chars_per_token == 2.5
    assert s.llm.max_tokens("tags") == 400
    assert load(cfg, {"DETAILS_LLM_CHUNK_TOKENS": "1234"}).llm.chunk_tokens == 1234
    with pytest.raises(SettingsError, match=r"llm\.chars_per_token"):
        load(cfg, {"DETAILS_LLM_CHARS_PER_TOKEN": "0"})


def test_run_insights_settings_defaults_env_and_validation(cfg):
    s = load_settings(cfg, env={}, dotenv_path=None)
    assert s.runner.log_max_lines == 5000 and s.lmstudio.unload_on_terminal is True
    s = load_settings(cfg, env={"DETAILS_RUNNER_LOG_MAX_LINES": "50", "DETAILS_LMSTUDIO_UNLOAD_ON_TERMINAL": "false"},
                      dotenv_path=None)
    assert s.runner.log_max_lines == 50 and s.lmstudio.unload_on_terminal is False
    with pytest.raises(SettingsError, match="log_max_lines"):
        load_settings(cfg, env={"DETAILS_RUNNER_LOG_MAX_LINES": "0"}, dotenv_path=None)
    with pytest.raises(SettingsError, match="unload_on_terminal"):
        load_settings(cfg, env={"DETAILS_LMSTUDIO_UNLOAD_ON_TERMINAL": "maybe"}, dotenv_path=None)


def test_llm_stream_settings_defaults_env_and_validation(cfg):
    s = load_settings(cfg, env={}, dotenv_path=None)
    assert s.llm.stream is True and s.llm.stream_idle_timeout_s == 60
    s = load_settings(cfg, env={"DETAILS_LLM_STREAM": "false", "DETAILS_LLM_STREAM_IDLE_TIMEOUT_S": "5"}, dotenv_path=None)
    assert s.llm.stream is False and s.llm.stream_idle_timeout_s == 5
    with pytest.raises(SettingsError, match="stream_idle_timeout_s"):
        load_settings(cfg, env={"DETAILS_LLM_STREAM_IDLE_TIMEOUT_S": "0"}, dotenv_path=None)
    with pytest.raises(SettingsError, match=r"llm\.stream"):
        load_settings(cfg, env={"DETAILS_LLM_STREAM": "maybe"}, dotenv_path=None)
