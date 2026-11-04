"""Real mlx-whisper smoke test. Excluded by default (-m gpu). Needs the turbo weights in the HF cache, ffmpeg and the
macOS `say` Greek voice (Melina) to build a short fixture."""
import asyncio, shutil, subprocess

import pytest

from details.runner import steps_stt
from details.runner.steps import StepRegistry
from details.services import stt_runs
from details.services.models import stt_installed
from jobs_helpers import jobs_env, wait_status
from library_helpers import FFMPEG
from stt_helpers import SRC, rec, run

TEXT = ("Καλημέρα. Σήμερα θα μιλήσουμε για την υιοθέτηση της τεχνητής νοημοσύνης στην εταιρεία. "
        "Θα δούμε τα οφέλη, τους κινδύνους και τα επόμενα βήματα για την ομάδα μας.")


@pytest.mark.gpu
def test_real_turbo_transcribes_greek_fixture(db, db_url, mk_settings, tmp_path, monkeypatch):
    if not (shutil.which("say") and FFMPEG):
        pytest.skip("needs macOS `say` and ffmpeg to build the fixture")
    s = mk_settings(db_url)
    if not stt_installed(s.paths.hf_hub, "mlx-community/whisper-large-v3-turbo"):
        pytest.skip("turbo weights not in the HF cache")
    monkeypatch.setenv("PYTHONPATH", str(SRC))
    aiff = tmp_path / "f.aiff"
    subprocess.run(["say", "-v", "Melina", "-o", str(aiff), TEXT], check=True)
    rid = rec(db, s, seconds=1)
    flac = next((s.paths.data_dir / "recordings" / rid).glob("master.flac"))
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(aiff), "-ar", "16000", "-ac", "1", str(flac)], check=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(flac)], capture_output=True, text=True).stdout)
    db.execute("UPDATE recording SET duration_ms=%s WHERE id=%s", (round(dur * 1000), rid))

    async def go():
        reg = StepRegistry()
        steps_stt.install(reg)
        async with jobs_env(db_url, mk_settings, steps=reg, heartbeat="300") as e:
            steps_stt.bind(e.pool)
            run_id, jid = await stt_runs.start_run(e.pool, e.svc, e.models, e.settings, rid, "whisper-turbo",
                                                   language="el", vocab_hint=False)
            await wait_status(e.svc, jid, "done", timeout=300)
            return run_id
    run_id = run(go, timeout=330)
    text, wall, rtf = db.execute("SELECT text, wall_ms, rtf FROM stt_run WHERE id=%s", (run_id,)).fetchone()
    print(f"GPU smoke: audio {dur:.1f}s wall {wall} ms rtf {rtf:.1f} text {text[:100]!r}")
    assert "Καλημέρα" in text or "νοημοσύνη" in text
    assert wall > 0 and rtf > 0
