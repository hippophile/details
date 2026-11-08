"""Per-job structured log + secret redaction. Lines are JSON in <data_dir>/jobs/<job_id>/<step>/log.jsonl
(job-level lines in the `_job` dir); `n` is a per-job counter that continues after a restart.
One shared JobLog per job id (JobLogs registry) so the runner, steps and API never race on `n`.
Prompt, reply and transcript text never go in here. Every line passes through redact() on write and on read."""
import datetime, json, logging, pathlib, re, threading
from urllib.parse import urlsplit

log = logging.getLogger("details.joblog")
JOB_DIR = "_job"
MAX_LINE = 2000
MIN_SECRET = 6          # shorter values are not masked (they would mangle ordinary words)
SRC = ("runner", "step", "worker", "llm", "memory")
LEVELS = ("debug", "info", "warn", "error")

_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/\s@]+@")
_KEYED = re.compile(r"""(?i)((?:["']?)(?:password|passwd|pwd|token|api[_-]?key)(?:["']?)\s*[=:]\s*["']?)[^\s&;,"'}]+""")
_BEARER = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_HF_TOKEN = re.compile(r"hf_[A-Za-z0-9]{20,}")        # Hugging Face tokens, masked even when settings do not know them


def secrets_from(settings):
    """Secret values known to the settings: the database URLs (and their passwords), LM Studio URL userinfo, the
    Hugging Face token."""
    out = []
    token = getattr(getattr(settings, "diarization", None), "hf_token", "")
    for url in (settings.db.url, settings.db.url_test, settings.lmstudio.url):
        if not url:
            continue
        try:
            parts = urlsplit(url)
            if url in (settings.db.url, settings.db.url_test) or parts.password or parts.username:
                out.append(url)
            if parts.password:
                out.append(parts.password)
        except ValueError:
            out.append(url)
    if token:
        out.append(token)
    return tuple(dict.fromkeys(v for v in out if len(v) >= MIN_SECRET))


def redact_exact(text, secrets=()):
    """Mask only exact configured secret values and URL credentials (used on prompt/reply text, which is user content)."""
    if not isinstance(text, str):
        return text
    for v in sorted(secrets, key=len, reverse=True):
        if len(v) >= MIN_SECRET:
            text = text.replace(v, "***")
    return _USERINFO.sub(r"\1***@", text)


def redact(text, secrets=()):
    """Mask URL credentials, password=/token=/api_key= values, bearer tokens and the exact configured secrets."""
    if not isinstance(text, str):
        return text
    text = redact_exact(text, secrets)
    text = _KEYED.sub(r"\1***", text)
    text = _HF_TOKEN.sub("***", text)
    return _BEARER.sub(r"\1***", text)


def _redact_data(value, secrets):
    if isinstance(value, str):
        return redact(value, secrets)[:500]
    if isinstance(value, dict):
        return {str(k): _redact_data(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_data(v, secrets) for v in value]
    return value if value is None or isinstance(value, (bool, int, float)) else redact(str(value), secrets)[:500]


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


class JobLog:
    def __init__(self, data_dir, job_id, max_lines=5000, secrets=()):
        self.root = pathlib.Path(data_dir) / "jobs" / str(job_id)
        self.max_lines, self.secrets = max_lines, tuple(secrets)
        self._lock = threading.Lock()
        self._n = self._truncated = None

    def _dir(self, step):
        return self.root / (step if step else JOB_DIR)

    def _files(self):
        return sorted(self.root.glob("*/log.jsonl")) if self.root.is_dir() else []

    def _recover(self):
        n, truncated = 0, False
        for f in self._files():
            for raw in self._raw(f):
                n = max(n, raw.get("n", 0))
                truncated = truncated or raw.get("msg") == "log truncated"
        self._n, self._truncated = n, truncated

    @staticmethod
    def _raw(path):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for line in text.splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue                  # a half-written last line
            if isinstance(d, dict) and isinstance(d.get("n"), int):
                yield d

    def append(self, step, level, src, msg, data=None):
        """-> the written line, or None once the cap is reached. Never raises (a log must not fail a job)."""
        try:
            with self._lock:
                if self._n is None:
                    self._recover()
                if self._truncated:
                    return None
                if self._n >= self.max_lines:
                    self._n += 1
                    self._truncated = True
                    line = {"n": self._n, "ts": _now(), "level": "warn", "step": None, "src": "runner",
                            "msg": "log truncated", "data": {"max_lines": self.max_lines}}
                    self._write(None, line)
                    return None
                self._n += 1
                line = {"n": self._n, "ts": _now(), "level": level, "step": step, "src": src,
                        "msg": redact(str(msg), self.secrets)[:MAX_LINE], "data": _redact_data(data or {}, self.secrets)}
                self._write(step, line)
                return line
        except Exception:
            log.exception("could not write a job log line")
            return None

    def _write(self, step, line):
        d = self._dir(step)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def read(self, after=0, limit=1000):
        """Lines with n > after, ordered by n, at most `limit`. Redacted again on the way out."""
        out = [d for f in self._files() for d in self._raw(f) if d["n"] > after]
        out.sort(key=lambda d: d["n"])
        out = out[:max(0, limit)]
        for d in out:
            d["msg"] = redact(str(d.get("msg", "")), self.secrets)
            d["data"] = _redact_data(d.get("data") or {}, self.secrets)
        return out

    @staticmethod
    def text(lines):
        """Readable plain-text form for the download."""
        out = []
        for d in lines:
            extra = " ".join(f"{k}={v}" for k, v in (d.get("data") or {}).items())
            out.append(f"{d['ts']} {d['level'].upper():5} [{d['step'] or 'job'}] {d['src']}: {d['msg']}"
                       + (f" {extra}" if extra else ""))
        return "\n".join(out) + ("\n" if out else "")


class JobLogs:
    """Registry: one JobLog per job id. `for_settings` returns the process-wide registry of a data_dir."""
    _by_dir = {}
    _guard = threading.Lock()

    def __init__(self, settings):
        self.settings = settings
        self._logs = {}

    @classmethod
    def for_settings(cls, settings):
        key = str(settings.paths.data_dir)
        with cls._guard:
            reg = cls._by_dir.get(key)
            if reg is None:
                reg = cls._by_dir[key] = cls(settings)
            reg.settings = settings
            return reg

    def get(self, job_id):
        job_id = str(job_id)
        with self._guard:
            jl = self._logs.get(job_id)
            if jl is None:
                s = self.settings
                jl = self._logs[job_id] = JobLog(s.paths.data_dir, job_id, s.runner.log_max_lines, secrets_from(s))
            return jl

    def forget(self, job_id):
        self._logs.pop(str(job_id), None)
