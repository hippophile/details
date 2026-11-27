"""Layered settings: config.toml < DETAILS_<SECTION>_<KEY> env (with .env filling what the env lacks).
The only module that reads the environment. DSNs and .env values never appear in errors or logs."""
import dataclasses, os, pathlib, tomllib
from dataclasses import dataclass, field

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.toml"
DOTENV_PATH = REPO_ROOT / ".env"


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Server:
    port: int


@dataclass(frozen=True)
class Paths:
    data_dir: pathlib.Path
    hf_hub: pathlib.Path
    prompts_dir: pathlib.Path = REPO_ROOT / "prompts"   # prompt-ab: where prompts/<kind>/vN.md are read and written


@dataclass(frozen=True)
class LmStudio:
    url: str
    cli: str
    exclusive: bool   # true = the memory manager may also unload models it did not load
    unload_on_terminal: bool   # true = a failed/cancelled analysis unloads the model this app loaded


@dataclass(frozen=True)
class Db:
    url: str
    url_test: str
    connect_timeout: int


@dataclass(frozen=True)
class Record:
    sample_rate: int
    sync_interval_s: float
    grace_s: float
    keep_wav: bool
    min_free_gb: float
    max_msg_mb: int
    ffmpeg: str
    ffprobe: str
    tz: str


@dataclass(frozen=True)
class Trash:
    days: int


@dataclass(frozen=True)
class Import:
    max_mb: int


@dataclass(frozen=True)
class Models:
    path: str   # models.toml; a relative path is resolved against the repo root


@dataclass(frozen=True)
class Runner:
    heartbeat_timeout_s: float
    cancel_grace_s: float
    log_max_lines: int            # per job; one "log truncated" line is written after that


@dataclass(frozen=True)
class Stt:
    window_s: int
    vocab_hint: str    # initial_prompt used when a run asks for the vocabulary hint
    plugins: str       # comma-separated modules the worker imports to register extra engines (tests, v2.1)
    overlap_s: float = 0   # extra audio decoded on each side of a window; a segment is kept by the window holding its midpoint
    extra_languages: str = "en,de"   # comma-separated codes a run may list as also spoken (`also`)
    switch_min_share: float = 0.7    # a window is decoded in an `also` language only when it holds this share (0.5 to 1)

    def extras(self):
        return [c.strip() for c in self.extra_languages.split(",") if c.strip()]


@dataclass(frozen=True)
class Llm:
    single_pass_max_tokens: int   # transcripts estimated above this are split into chunks (map-reduce)
    chunk_tokens: int             # target chunk size, estimated
    chunk_overlap_tokens: int     # segments repeated at the start of the next chunk, estimated
    chars_per_token: float        # estimate, not measured (Greek tokenises worse than English)
    timeout_s: float              # one chat call
    max_tokens_debrief: int
    max_tokens_notes: int
    max_tokens_checklist: int
    max_tokens_tags: int
    stream: bool                  # show the reply token by token (LM Studio streaming); validation still runs at the end
    stream_idle_timeout_s: float  # a streamed call fails after this long without bytes (the reply may take long)
    temperature: float = 0.1      # sampling temperature for every AI call (0 = most repeatable)

    def max_tokens(self, kind):
        return getattr(self, f"max_tokens_{kind}")


@dataclass(frozen=True)
class Live:
    """Live assist on /record. Every key has a default so a config.toml without [live] still loads."""
    stt_model: str = "whisper-turbo"      # models.toml [stt.<id>]; must be an installed `fast` model (checked per session)
    llm_model: str = "gemma-cheap"        # models.toml [llm.<id>]; must have role `cheap` (checked per session)
    language: str = "el"
    notes_every_min: float = 2.0          # rolling notes interval (0.5 to 30)
    partials: bool = True                 # grey partial line while talking
    max_backlog_s: float = 60.0           # live audio queued for the speech worker; older audio is dropped (never the WAV)
    max_transcript_tokens: int = 4000     # newest live transcript sent to the notes model (estimate, not measured)
    llm_timeout_s: float = 90.0           # one notes call (and one model load)
    stt_timeout_s: float = 30.0           # one speech reply, after the worker said ready
    stt_load_timeout_s: float = 120.0     # cold start of the speech worker (model load)
    vocab_hint: bool = False              # pass stt.vocab_hint to Whisper
    test_mode: bool = False               # tests only: allows test_interval_s
    test_interval_s: float = 0.0          # tests only (needs test_mode): notes interval in seconds, bypasses the 0.5 min floor


@dataclass(frozen=True)
class Diarization:
    """Who talked (speakers-diarization). Every key has a default so a config.toml without [diarization] still loads.
    hf_token comes from DETAILS_DIARIZATION_HF_TOKEN / HF_TOKEN (env or .env) only; it is never in config.toml or a repr."""
    model: str = "community-1"            # models.toml [diar.<id>]
    device: str = "cpu"                   # cpu | mps (opt-in; the PyTorch MPS backend lacks some operators)
    merge_gap_ms: int = 1500              # same-speaker turns closer than this count as one turn / one transcript line
    stall_s: float = 600.0                # the worker stops its keepalive after this long without progress
    keepalive_s: float = 15.0             # "alive" line interval from process start (covers a slow model load)
    progress_min_interval_s: float = 1.0  # at most one progress line per this long (0 = every callback)
    plugins: str = ""                     # extra engine modules for the worker (comma-separated; tests)
    hf_token: str = field(default="", repr=False)


@dataclass(frozen=True)
class Settings:
    server: Server
    paths: Paths
    lmstudio: LmStudio
    db: Db
    record: Record
    trash: Trash
    models: Models
    runner: Runner
    stt: Stt
    llm: Llm
    live: Live
    diarization: Diarization
    import_: Import   # config section [import]; trailing underscore because `import` is a keyword


def _v_server(s):
    if not 1 <= s.port <= 65535:
        raise ValueError("server.port: must be 1-65535")


def _v_paths(s):
    pass


def _v_lmstudio(s):
    if not s.url.startswith(("http://", "https://")):
        raise ValueError("lmstudio.url: must start with http:// or https://")


def _v_db(s):
    if s.connect_timeout < 1:
        raise ValueError("db.connect_timeout: must be >= 1")


def _v_record(s):
    if s.sample_rate <= 0:
        raise ValueError("record.sample_rate: must be > 0")
    if s.sync_interval_s <= 0:
        raise ValueError("record.sync_interval_s: must be > 0")
    if s.grace_s < 0:
        raise ValueError("record.grace_s: must be >= 0")
    if s.min_free_gb < 0:
        raise ValueError("record.min_free_gb: must be >= 0")
    if s.max_msg_mb < 1:
        raise ValueError("record.max_msg_mb: must be >= 1")
    try:
        import zoneinfo
        zoneinfo.ZoneInfo(s.tz)
    except Exception:
        raise ValueError("record.tz: unknown time zone") from None


def _v_trash(s):
    if s.days < 1:
        raise ValueError("trash.days: must be >= 1")


def _v_models(s):
    if not s.path:
        raise ValueError("models.path: must not be empty")


def _v_runner(s):
    if s.heartbeat_timeout_s <= 0:
        raise ValueError("runner.heartbeat_timeout_s: must be > 0")
    if s.cancel_grace_s < 0:
        raise ValueError("runner.cancel_grace_s: must be >= 0")
    if s.log_max_lines < 1:
        raise ValueError("runner.log_max_lines: must be > 0")


def _v_live(s):
    if not s.stt_model:
        raise ValueError("live.stt_model: must not be empty")
    if not s.llm_model:
        raise ValueError("live.llm_model: must not be empty")
    if not s.language:
        raise ValueError("live.language: must not be empty")
    if not 0.5 <= s.notes_every_min <= 30:
        raise ValueError("live.notes_every_min: must be between 0.5 and 30")
    if s.max_backlog_s <= 0:
        raise ValueError("live.max_backlog_s: must be > 0")
    if s.max_transcript_tokens < 100:
        raise ValueError("live.max_transcript_tokens: must be >= 100")
    for k in ("llm_timeout_s", "stt_timeout_s", "stt_load_timeout_s"):
        if getattr(s, k) <= 0:
            raise ValueError(f"live.{k}: must be > 0")
    if s.test_interval_s < 0 or (s.test_interval_s and not s.test_mode):
        raise ValueError("live.test_interval_s: only allowed with live.test_mode = true (tests)")


def _v_diarization(s):
    if not s.model:
        raise ValueError("diarization.model: must not be empty")
    if s.device not in DEVICES:
        raise ValueError(f"diarization.device: must be one of {', '.join(DEVICES)}")
    if s.merge_gap_ms < 0:
        raise ValueError("diarization.merge_gap_ms: must be >= 0")
    for k in ("stall_s", "keepalive_s"):
        if getattr(s, k) <= 0:
            raise ValueError(f"diarization.{k}: must be > 0")
    if s.progress_min_interval_s < 0:
        raise ValueError("diarization.progress_min_interval_s: must be >= 0")


def _v_import(s):
    if s.max_mb <= 0:
        raise ValueError("import.max_mb: must be > 0")


def _v_stt(s):
    if s.window_s < 1:
        raise ValueError("stt.window_s: must be >= 1")
    if s.overlap_s < 0:
        raise ValueError("stt.overlap_s: must be >= 0")
    codes = [c.strip() for c in s.extra_languages.split(",")] if s.extra_languages.strip() else []
    if "" in codes or len(set(codes)) != len(codes) or "auto" in codes:
        raise ValueError("stt.extra_languages: comma-separated language codes, no empty or repeated ones, not 'auto'")
    if not 0.5 <= s.switch_min_share <= 1:
        raise ValueError("stt.switch_min_share: must be between 0.5 and 1")


def _v_llm(s):
    for k in ("single_pass_max_tokens", "chunk_tokens", "max_tokens_debrief", "max_tokens_notes",
              "max_tokens_checklist", "max_tokens_tags"):
        if getattr(s, k) < 1:
            raise ValueError(f"llm.{k}: must be >= 1")
    if s.chunk_overlap_tokens < 0:
        raise ValueError("llm.chunk_overlap_tokens: must be >= 0")
    if s.chars_per_token <= 0:
        raise ValueError("llm.chars_per_token: must be > 0")
    if not 0 <= s.temperature <= 2:
        raise ValueError("llm.temperature: must be between 0 and 2")
    if s.timeout_s <= 0:
        raise ValueError("llm.timeout_s: must be > 0")
    if s.stream_idle_timeout_s <= 0:
        raise ValueError("llm.stream_idle_timeout_s: must be > 0")


# section name -> (dataclass, validation hook). Later changes add a section here + a [section] in config.toml.
SECTIONS = {"server": (Server, _v_server), "paths": (Paths, _v_paths),
            "lmstudio": (LmStudio, _v_lmstudio), "db": (Db, _v_db),
            "record": (Record, _v_record), "trash": (Trash, _v_trash), "import": (Import, _v_import),
            "models": (Models, _v_models), "runner": (Runner, _v_runner),
            "stt": (Stt, _v_stt), "llm": (Llm, _v_llm), "live": (Live, _v_live),
            "diarization": (Diarization, _v_diarization)}
DEVICES = ("cpu", "mps")
# keys that may only come from the environment / .env, never from config.toml: (section, key) -> variable to use
ENV_ONLY = {("diarization", "hf_token"): "HF_TOKEN"}
# the Hugging Face token variable names no child process of the server may inherit (see scrub_secret_env)
SECRET_ENV = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "DETAILS_DIARIZATION_HF_TOKEN")
# extra env names per key, tried after DETAILS_<SECTION>_<KEY>: (var, transform)
ALIASES = {
    ("server", "port"): [("DETAILS_PORT", None), ("PORT", None)],
    ("paths", "hf_hub"): [("HF_HOME", lambda v: str(pathlib.Path(v).expanduser() / "hub"))],
    ("db", "url"): [("DATABASE_URL", None)],
    ("db", "url_test"): [("DATABASE_URL_TEST", None)],
    ("record", "tz"): [("DETAILS_TZ", None)],
    ("diarization", "hf_token"): [("HF_TOKEN", None)],
}


def read_dotenv(path):
    """Parse KEY=VALUE lines (comments, `export`, simple quotes). Missing file -> {}."""
    out = {}
    try:
        text = pathlib.Path(path).read_text()
    except FileNotFoundError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.removeprefix("export ").split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _coerce(section, key, typ, value):
    try:
        if typ is int:
            return int(value)
        if typ is float:
            return float(value)
        if typ is bool:
            if isinstance(value, bool):
                return value
            v = str(value).strip().lower()
            if v in ("1", "true", "yes", "on"):
                return True
            if v in ("0", "false", "no", "off"):
                return False
            raise ValueError(v)
        if typ is pathlib.Path:
            return pathlib.Path(str(value)).expanduser().absolute()
        return str(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{section}.{key}: expected {typ.__name__}") from None


def load_settings(config_path=CONFIG_PATH, env=None, dotenv_path=DOTENV_PATH):
    """env defaults to os.environ; .env is merged into it WITHOUT overriding existing variables."""
    if env is None:
        env = os.environ
    if dotenv_path:
        for k, v in read_dotenv(dotenv_path).items():
            env.setdefault(k, v)
    config_path = pathlib.Path(config_path)
    try:
        raw = tomllib.loads(config_path.read_text())
    except FileNotFoundError:
        raise SettingsError(f"{config_path}: config file not found") from None
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{config_path}: invalid TOML: {e}") from None
    built = {}
    for (section, key), var in ENV_ONLY.items():      # a secret in the file is refused; the message never shows the value
        if key in (raw.get(section) or {}):
            alt = f"DETAILS_{section}_{key}".upper()
            raise SettingsError(f"{config_path}: {section}.{key} must not be set in the file: put {var} (or {alt}) "
                                "in the environment or .env instead")
    for name, (cls, validate) in SECTIONS.items():
        vals = {}
        for f in dataclasses.fields(cls):
            value = raw.get(name, {}).get(f.name)
            for var, tf in [(f"DETAILS_{name}_{f.name}".upper(), None)] + ALIASES.get((name, f.name), []):
                if env.get(var):
                    value = tf(env[var]) if tf else env[var]
                    break
            if value is None and f.default is not dataclasses.MISSING:
                vals[f.name] = f.default           # optional key: the dataclass default
                continue
            if value is None:
                raise SettingsError(f"{config_path}: missing [{name}] {f.name}")
            vals[f.name] = _coerce(name, f.name, f.type, value)
        section = cls(**vals)
        try:
            validate(section)
        except ValueError as e:
            raise SettingsError(str(e)) from None
        built["import_" if name == "import" else name] = section
    return Settings(**built)


def scrub_secret_env(env=None):
    """Remove the Hugging Face token variables from the server's own environment so that no child process it starts
    (STT worker, live worker, lms, diarization worker) inherits them. Call it right after load_settings(): the value
    stays in settings.diarization.hf_token (used by `diar_fetch` only)."""
    env = os.environ if env is None else env
    for name in SECRET_ENV:
        env.pop(name, None)
