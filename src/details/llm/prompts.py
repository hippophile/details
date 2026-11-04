"""Prompt templates as git files: prompts/<kind>/v<N>.md (front matter + body). Placeholders: {{transcript}},
{{language}}, {{recording_title}} (plain string replace). `sync_prompts` inserts new (name, version) rows and refuses an
edited one ("edit = new version file"). Rows never mutate."""
import asyncio, hashlib, json, logging, os, re, secrets, unicodedata, weakref
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from ..settings import REPO_ROOT
from . import checks as checks_mod
from .schemas import KINDS, load_schema

log = logging.getLogger("details.prompts")

PROMPTS_DIR = REPO_ROOT / "prompts"
PLACEHOLDERS = ("transcript", "language", "recording_title")
_PH = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
GREEK_LETTERS = re.compile("[Ͱ-Ͽἀ-῿]")
LETTERS = re.compile(r"[^\W\d_]")
MAX_BODY_BYTES = 64 * 1024
MAX_NOTE_CHARS = 200


class PromptError(RuntimeError):
    pass


@dataclass(frozen=True)
class Template:
    name: str
    version: int
    kind: str
    body: str
    output_schema: str
    description: str
    suggested_params: dict
    sha256: str
    id: str | None = None
    checks: tuple = ()
    note: str = ""


def parse_template(path):
    return _parse(path, path.read_bytes())


def _parse(path, raw):
    """`path` names the file in messages and gives the expected file name; `raw` are the exact bytes."""
    text = raw.decode("utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)\Z", text, re.S)
    if not m:
        raise PromptError(f"{path}: missing front matter (--- ... ---)")
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    for k in ("name", "version", "kind", "output_schema"):
        if not fm.get(k):
            raise PromptError(f"{path}: front matter needs '{k}'")
    try:
        version = int(fm["version"])
        params = json.loads(fm.get("suggested_params") or "{}")
    except ValueError:
        raise PromptError(f"{path}: front matter 'version' must be an integer and 'suggested_params' JSON") from None
    if fm["kind"] not in KINDS:
        raise PromptError(f"{path}: unknown kind '{fm['kind']}'")
    if path.name != f"v{version}.md":
        raise PromptError(f"{path}: file name must be v{version}.md to match its version")
    body = m.group(2)
    names = tuple(c.strip() for c in fm.get("checks", "").split(",") if c.strip())
    for c in names:
        if c not in checks_mod.NAMES:
            raise PromptError(f"{path}: unknown check '{c}' (allowed: {', '.join(checks_mod.NAMES)})")
    for ph in _PH.findall(body):
        if ph not in PLACEHOLDERS:
            raise PromptError(f"{path}: unknown placeholder {{{{{ph}}}}} (allowed: "
                              + ", ".join("{{" + p + "}}" for p in PLACEHOLDERS) + ")")
    return Template(fm["name"], version, fm["kind"], body, fm["output_schema"], fm.get("description", ""), params,
                    hashlib.sha256(raw).hexdigest(), None, names, fm.get("note", ""))


_INSERT = ("INSERT INTO prompt_template (name, version, kind, body, output_schema, sha256, checks) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s)")


def _insert_args(t):
    return (t.name, t.version, t.kind, t.body, Jsonb(load_schema(t.kind)), t.sha256, list(t.checks))


async def sync_prompts(pool, prompts_dir=None):
    """Insert new templates; raise PromptError for an edited (name, version) or a bad file. All-or-nothing."""
    root = prompts_dir or PROMPTS_DIR
    found = [parse_template(f) for f in sorted(root.glob("*/v*.md"))]
    async with pool.connection() as c:
        async with c.transaction():
            for t in found:
                cur = await c.execute("SELECT sha256 FROM prompt_template WHERE name=%s AND version=%s",
                                      (t.name, t.version))
                row = await cur.fetchone()
                if row is None:
                    await c.execute(_INSERT, _insert_args(t))
                elif row["sha256"] != t.sha256:
                    raise PromptError(f"prompt '{t.name}' v{t.version} was edited after it was stored (stored sha256 "
                                      f"{row['sha256']}, file sha256 {t.sha256}): edit = new version file. Restore "
                                      f"the file or add prompts/{t.kind}/v{t.version + 1}.md")


async def latest_templates(pool):
    """-> {kind: Template} with the highest version per kind."""
    async with pool.connection() as c:
        cur = await c.execute("SELECT DISTINCT ON (kind) id, name, version, kind, body, output_schema, sha256, checks "
                              "FROM prompt_template ORDER BY kind, version DESC")
        return {r["kind"]: Template(r["name"], r["version"], r["kind"], r["body"], "", "", {}, r["sha256"], str(r["id"]),
                                    tuple(r["checks"]))
                for r in await cur.fetchall()}


async def versioned_templates(pool, picks):
    """picks: {kind: version} -> {kind: Template} for those exact versions (a missing one is simply absent)."""
    out = {}
    async with pool.connection() as c:
        for kind, version in picks.items():
            r = await (await c.execute("SELECT id, name, version, kind, body, sha256, checks FROM prompt_template "
                                       "WHERE kind=%s AND version=%s ORDER BY name LIMIT 1", (kind, version))).fetchone()
            if r:
                out[kind] = Template(r["name"], r["version"], r["kind"], r["body"], "", "", {}, r["sha256"],
                                     str(r["id"]), tuple(r["checks"]))
    return out


# ---- save a new version (prompt-ab) --------------------------------------------------------------------------

class PromptSaveError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


_LOCKS = weakref.WeakKeyDictionary()      # one lock per event loop (tests start several loops); the app has one


def _lock():
    loop = asyncio.get_running_loop()
    if loop not in _LOCKS:
        _LOCKS[loop] = asyncio.Lock()
    return _LOCKS[loop]


def _front(path):
    """Front matter of a prompt file as a dict ({} when the file is missing or unreadable)."""
    try:
        m = re.match(r"---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    except (OSError, ValueError):
        return {}
    fm = {}
    for line in (m.group(1).splitlines() if m else []):
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def read_note(root, kind, version):
    return _front(root / kind / f"v{version}.md").get("note", "")


def _build_file(name, version, kind, output_schema, description, suggested_params, checks, note, body):
    """The exact bytes of the new version file. Front matter is generated here; the user only supplies the body."""
    lines = ["---", f"name: {name}", f"version: {version}", f"kind: {kind}", f"output_schema: {output_schema}"]
    if description:
        lines.append(f"description: {description}")
    if suggested_params:
        lines.append(f"suggested_params: {suggested_params}")
    if checks:
        lines.append("checks: " + ", ".join(checks))
    if note:
        lines.append(f"note: {note}")
    lines.append("---")
    return ("\n".join(lines) + "\n" + body).encode("utf-8")


async def _insert_row(pool, t):
    async with pool.connection() as c:
        await c.execute(_INSERT, _insert_args(t))


def _check_input(kind, base_version, body, note):
    if kind not in KINDS:
        raise PromptSaveError(404, "unknown prompt kind")
    if type(base_version) is not int:
        raise PromptSaveError(422, "base_version must be a version number")
    if not isinstance(body, str) or not body.strip():
        raise PromptSaveError(422, "the prompt text is empty")
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise PromptSaveError(422, f"the prompt is longer than {MAX_BODY_BYTES // 1024} KB")
    if note is not None:
        if not isinstance(note, str):
            raise PromptSaveError(422, "note must be text")
        if len(note) > MAX_NOTE_CHARS:
            raise PromptSaveError(422, f"the note is longer than {MAX_NOTE_CHARS} characters")
        if any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in note):
            raise PromptSaveError(422, "the note must be one line without control characters")
    found = _PH.findall(body)
    if "transcript" not in found:
        raise PromptSaveError(422, "the prompt must contain the placeholder {{transcript}}")
    for ph in dict.fromkeys(found):
        if ph not in PLACEHOLDERS:
            raise PromptSaveError(422, f"unknown placeholder {{{{{ph}}}}} (allowed: "
                                  + ", ".join("{{" + p + "}}" for p in PLACEHOLDERS) + ")")


async def save_new_version(pool, root, kind, base_version, body, note=None):
    """Write prompts/<kind>/v<N+1>.md (N = highest in the database and on disk) and insert its row from the same
    bytes. Never replaces a file: temp file -> parse the exact bytes -> os.link. -> dict for the API."""
    _check_input(kind, base_version, body, note)
    note = (note or "").strip()
    async with pool.connection() as c:
        base = await (await c.execute("SELECT name, body, checks FROM prompt_template WHERE kind=%s AND version=%s "
                                      "ORDER BY name LIMIT 1", (kind, base_version))).fetchone()
    if base is None:
        raise PromptSaveError(422, f"base version {base_version} of {kind} does not exist")
    if body == base["body"]:
        raise PromptSaveError(422, "nothing changed: the text is identical to the base version")
    fm = _front(root / kind / f"v{base_version}.md")
    folder = root / kind
    async with _lock():
        folder.mkdir(parents=True, exist_ok=True)
        for _ in range(5):
            async with pool.connection() as c:
                row = await (await c.execute("SELECT max(version) AS v FROM prompt_template WHERE kind=%s", (kind,))).fetchone()
            on_disk = [int(m.group(1)) for p in folder.glob("v*.md") if (m := re.fullmatch(r"v(\d+)\.md", p.name))]
            n = max([row["v"] or 0, *on_disk]) + 1
            final = folder / f"v{n}.md"
            raw = _build_file(base["name"], n, kind, fm.get("output_schema") or f"{kind}.v1", fm.get("description", ""),
                              fm.get("suggested_params", ""), tuple(base["checks"]), note, body)
            tmp = folder / f".v{n}.md.{secrets.token_hex(4)}.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(raw)
                    f.flush()
                    os.fsync(f.fileno())
                try:
                    t = _parse(final, tmp.read_bytes())
                except PromptError as e:
                    raise PromptSaveError(422, f"the new file would not parse: {e}") from None
                if (t.kind, t.version, t.checks, t.sha256, t.body) != (kind, n, tuple(base["checks"]),
                                                                       hashlib.sha256(raw).hexdigest(), body):
                    raise PromptSaveError(422, "the new file does not read back as intended; nothing was saved")
                try:
                    os.link(tmp, final)                 # fails if the file exists: never replaces anything
                except FileExistsError:
                    continue
            finally:
                tmp.unlink(missing_ok=True)
            try:
                await _insert_row(pool, t)
            except BaseException as e:
                final.unlink(missing_ok=True)           # the file this call just created
                log.warning("saving %s v%s failed in the database: %s", kind, n, type(e).__name__)
                if isinstance(e, psycopg.errors.UniqueViolation):
                    raise PromptSaveError(409, f"{kind} v{n} already exists: try again") from None
                if isinstance(e, Exception):
                    raise PromptSaveError(500, "could not save the new version") from None
                raise
            return {"kind": kind, "version": n, "sha256": t.sha256, "checks": list(t.checks),
                    "path": f"{root.name}/{kind}/v{n}.md"}
        raise PromptSaveError(409, "another save is running: try again")


def render(body, transcript, language, recording_title):
    values = {"transcript": transcript, "language": language, "recording_title": recording_title}
    return _PH.sub(lambda m: values[m.group(1)], body)


def language_name(run_language, text, output_language="transcript"):
    """'Greek' | 'English'. English switch wins; else the run's language; auto/unknown -> >30% Greek letters."""
    if output_language == "en":
        return "English"
    if run_language == "el":
        return "Greek"
    if run_language == "en":
        return "English"
    letters = LETTERS.findall(text or "")
    greek = GREEK_LETTERS.findall(text or "")
    return "Greek" if letters and len(greek) / len(letters) > 0.3 else "English"


if __name__ == "__main__":      # `make prompts`
    import asyncio

    from ..db import open_pool
    from ..settings import load_settings

    async def main():
        s = load_settings()
        pool = await open_pool(s.db.url, s.db.connect_timeout)
        try:
            await sync_prompts(pool, s.paths.prompts_dir)
        finally:
            await pool.close()
        print("prompts synced")
    asyncio.run(main())
