"""Helpers for the llm-outputs tests: a Greek transcript, good model replies per kind, a scripted chat function,
stt_run seeding and an app/job environment wired to FakeLMStudio in HTTP mode. Fakes only, no GPU."""
import contextlib, json, types

from details.runner import steps_analyse
from details.runner.steps import StepRegistry
from details.services import stt_runs
from jobs_helpers import jobs_env
from stt_helpers import insert_run, rec

SEGS = ["Καλημέρα σε όλους, ξεκινάμε τη συνάντηση.",
        "Θα κάνουμε deploy στις 12 Νοεμβρίου.",
        "Ο Νίκος θα ετοιμάσει το documentation μέχρι την Παρασκευή.",
        "Υπάρχει ρίσκο με τα δεδομένα του GDPR.",
        "Δεν ξέρουμε ακόμα ποιο μοντέλο θα χρησιμοποιήσουμε."]
FULL = " ".join(SEGS)
TEMPLATE = types.SimpleNamespace(id=None, name="x", version=1, body="Lang: {{language}} Title: {{recording_title}}\n"
                                                                   "{{transcript}}", sha256="a" * 64, checks=())

GOOD = {
    "debrief": {"schema_version": 1, "summary": "Συζήτηση για το deploy.",
                "decisions": [{"text": "Deploy στις 12 Νοεμβρίου", "quote": "Θα κάνουμε deploy στις 12 Νοεμβρίου"}],
                "action_items": [{"text": "Ετοιμασία documentation", "owner": "Νίκος", "due": None, "done": False,
                                  "quote": "Ο Νίκος θα ετοιμάσει το documentation"}],
                "risks": [{"text": "Ρίσκο GDPR", "severity": "medium", "quote": "ρίσκο με τα δεδομένα του GDPR"}],
                "open_questions": [{"text": "Ποιο μοντέλο;", "quote": "ποιο μοντέλο θα χρησιμοποιήσουμε"}]},
    "notes": {"schema_version": 1, "title": "Σημειώσεις συνάντησης",
              "sections": [{"heading": "Deploy", "bullets": [
                  {"text": "Deploy στις 12/11", "quote": "Θα κάνουμε deploy στις 12 Νοεμβρίου"}]}]},
    "checklist": {"schema_version": 1, "title": "Ενέργειες",
                  "items": [{"text": "Deploy στις 12/11", "owner": None, "due": "2026-11-12", "done": False,
                             "quote": "Θα κάνουμε deploy στις 12 Νοεμβρίου"},
                            {"text": "Documentation", "owner": "Νίκος", "due": None, "done": False,
                             "quote": "Ο Νίκος θα ετοιμάσει το documentation"},
                            {"text": "Έλεγχος GDPR", "owner": None, "due": None, "done": False,
                             "quote": "ρίσκο με τα δεδομένα του GDPR"}]},
    "tags": {"schema_version": 1, "tags": [{"name": "deploy", "quote": "Θα κάνουμε deploy"},
                                           {"name": "gdpr", "quote": "δεδομένα του GDPR"}]},
    "brief": {"schema_version": 1, "kind_of_recording": "meeting", "summary": "Συζήτηση για το deploy.",
              "chapters": [{"title": "Έναρξη", "quote": "Καλημέρα σε όλους"},
                           {"title": "Deploy", "quote": "Θα κάνουμε deploy"}],
              "decisions": [{"text": "Deploy στις 12 Νοεμβρίου", "quote": "Θα κάνουμε deploy στις 12 Νοεμβρίου"}],
              "disagreements": [],
              "action_items": [{"text": "Ετοιμασία documentation", "owner": "Νίκος", "due": None, "done": False,
                                "quote": "Ο Νίκος θα ετοιμάσει το documentation"}],
              "open_questions": [{"text": "Ποιο μοντέλο;", "quote": "ποιο μοντέλο θα χρησιμοποιήσουμε"}]},
}
LINK_NONE = {"decisions": [], "disagreements": []}


def good(kind, **over):
    c = json.loads(json.dumps(GOOD[kind]))
    c.update(over)
    return c


def kind_of(body):
    return body["response_format"]["json_schema"]["name"]


def good_responder(body):
    """FakeLMStudio.responder: a valid reply for whatever kind is asked (reduce calls get {"text": ...})."""
    k = kind_of(body)
    return {"text": "Σύνοψη"} if k == "reduce" else LINK_NONE if k == "brief_link" else good(k)


class ScriptedChat:
    """The `chat` callable the pipeline takes. Each reply: dict (JSON content), str (raw content) or
    callable(call) -> either. `calls` records messages/kind/schema/max_tokens."""

    def __init__(self, *replies, default=None):
        self.replies, self.default, self.calls = list(replies), default, []

    async def __call__(self, messages, *, kind, schema, max_tokens):
        self.calls.append({"messages": messages, "kind": kind, "schema": schema, "max_tokens": max_tokens})
        r = self.replies.pop(0) if self.replies else self.default
        if callable(r):
            r = r(self.calls[-1])
        if isinstance(r, Exception):
            raise r
        text = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        return {"text": text, "tokens_in": 100, "tokens_out": 50, "finish_reason": "stop", "wall_ms": 500,
                "raw": {"choices": [{"message": {"content": text}}]}}


def seed_run(db, settings, segs=SEGS, language="el", status="done", title="rec"):
    """-> (recording_id, stt_run_id): a done stt_run with transcript segments."""
    rid = rec(db, settings, seconds=5, title=title)
    run_id = insert_run(db, rid, status=status, language=language, text=" ".join(segs))
    for i, t in enumerate(segs):
        db.execute("INSERT INTO transcript_segment (stt_run_id, idx, start_ms, end_ms, text) VALUES (%s,%s,%s,%s,%s)",
                   (run_id, i, i * 1000, i * 1000 + 900, t))
    return rid, run_id


def sync_prompts(db_url, root=None):
    """Store the prompt files of `root` (default: a tmp copy of the shipped v1 files, see conftest.v1_prompts_dir)."""
    import asyncio

    import conftest
    from details.db import open_pool
    from details.llm import prompts
    root = root or conftest.v1_prompts_dir()

    async def go():
        pool = await open_pool(db_url)
        try:
            await prompts.sync_prompts(pool, root)
        finally:
            await pool.close()
    asyncio.run(go())


@contextlib.asynccontextmanager
async def analysis_env(db_url, mk_settings, fake, *, start=True, url=None, **env):
    """jobs_env with only the analyse step registered (plus built-in lm_load/lm_unload), bound to the pool, and
    settings pointing at the fake HTTP server."""
    url = url or fake.url or await fake.start_http()
    reg = StepRegistry()
    steps_analyse.install(reg)
    async with jobs_env(db_url, mk_settings, steps=reg, lm=fake, start=start, DETAILS_LMSTUDIO_URL=url, **env) as e:
        steps_analyse.bind(e.pool)
        yield e
    await fake.stop_http()
