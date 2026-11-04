"""Prompt templates as git files: prompts/<kind>/v<N>.md (front matter + body). Placeholders: {{transcript}},
{{language}}, {{recording_title}} (plain string replace). `sync_prompts` inserts new (name, version) rows and refuses an
edited one ("edit = new version file"). Rows never mutate."""
import hashlib, json, re
from dataclasses import dataclass

from psycopg.types.json import Jsonb

from ..settings import REPO_ROOT
from .schemas import KINDS, load_schema

PROMPTS_DIR = REPO_ROOT / "prompts"
PLACEHOLDERS = ("transcript", "language", "recording_title")
_PH = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
GREEK_LETTERS = re.compile("[Ͱ-Ͽἀ-῿]")
LETTERS = re.compile(r"[^\W\d_]")


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


def parse_template(path):
    raw = path.read_bytes()
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
    for ph in _PH.findall(body):
        if ph not in PLACEHOLDERS:
            raise PromptError(f"{path}: unknown placeholder {{{{{ph}}}}} (allowed: "
                              + ", ".join("{{" + p + "}}" for p in PLACEHOLDERS) + ")")
    return Template(fm["name"], version, fm["kind"], body, fm["output_schema"], fm.get("description", ""), params,
                    hashlib.sha256(raw).hexdigest())


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
                    await c.execute("INSERT INTO prompt_template (name, version, kind, body, output_schema, sha256) "
                                    "VALUES (%s,%s,%s,%s,%s,%s)",
                                    (t.name, t.version, t.kind, t.body, Jsonb(load_schema(t.kind)), t.sha256))
                elif row["sha256"] != t.sha256:
                    raise PromptError(f"prompt '{t.name}' v{t.version} was edited after it was stored: edit = new "
                                      f"version file. Restore it or add prompts/{t.kind}/v{t.version + 1}.md")


async def latest_templates(pool):
    """-> {kind: Template} with the highest version per kind."""
    async with pool.connection() as c:
        cur = await c.execute("SELECT DISTINCT ON (kind) id, name, version, kind, body, output_schema, sha256 "
                              "FROM prompt_template ORDER BY kind, version DESC")
        return {r["kind"]: Template(r["name"], r["version"], r["kind"], r["body"], "", "", {}, r["sha256"], str(r["id"]))
                for r in await cur.fetchall()}


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
            await sync_prompts(pool)
        finally:
            await pool.close()
        print("prompts synced")
    asyncio.run(main())
