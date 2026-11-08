"""python -m details.diar_fetch (make diar-model): download the diarization model snapshot into settings.paths.hf_hub.
The Hugging Face token (settings.diarization.hf_token, from HF_TOKEN in .env) is used for this download only; the server
runs the model offline from the cache. Errors pass through redact() so a token is never printed."""
import sys

from .runner.joblog import redact, secrets_from
from .services.models import load_registry, resolve_path
from .settings import load_settings

NO_TOKEN = ("No Hugging Face token found. Create a free account and a read token at https://huggingface.co/settings/tokens, "
            "accept the terms on the model page ({url}), then put HF_TOKEN=... in .env (never paste or screenshot it) "
            "and run `make diar-model` again.")
GATED = ("The model is gated. Open {url}, sign in, accept the terms on that page, then run `make diar-model` again.")


def _gated(e):
    text = f"{type(e).__name__} {e}".lower()
    return any(w in text for w in ("gatedrepo", "gated", "restricted", "401", "403"))


def main(argv=None, *, settings=None, downloader=None):
    s = settings or load_settings()
    entry = load_registry(resolve_path(s)).diar.get(s.diarization.model)
    if entry is None:
        print(f"diarization.model: '{s.diarization.model}' is not a [diar.<id>] entry in models.toml", file=sys.stderr)
        return 2
    url = f"https://huggingface.co/{entry.repo}"
    token = s.diarization.hf_token
    if not token:
        print(NO_TOKEN.format(url=url), file=sys.stderr)
        return 2
    if downloader is None:
        from huggingface_hub import snapshot_download as downloader
    try:
        path = downloader(entry.repo, cache_dir=s.paths.hf_hub, token=token)
    except Exception as e:
        detail = redact(str(e), secrets_from(s)).splitlines()[0][:300] if str(e) else type(e).__name__
        print((GATED.format(url=url) + "\n" if _gated(e) else "") + f"Download failed: {detail}", file=sys.stderr)
        return 1
    print(f"Speaker model downloaded to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
