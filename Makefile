PY := .venv/bin/python -I
PYS := .venv/bin/python
AUDIO ?= sessions/my_take.wav
MODELS ?=

.PHONY: setup setup-diar diar-model run record score test test-js migrate prompts

setup:            ## one-time: ffmpeg + Python 3.12 venv + packages + Whisper models (~4.6 GB)
	@command -v ffmpeg >/dev/null || brew install ffmpeg
	uv venv -q --python 3.12 .venv
	VIRTUAL_ENV=.venv uv pip install -q -r requirements.txt
	$(PY) -c "from huggingface_hub import snapshot_download as d; [print(d(r)) for r in ('mlx-community/whisper-large-v3-turbo','mlx-community/whisper-large-v3-mlx')]"

setup-diar:       ## optional: pyannote.audio for "Who talked" (heavy install; not part of `make setup`)
	VIRTUAL_ENV=.venv uv pip install -q -r requirements-diar.txt

diar-model:       ## download the speaker model (needs HF_TOKEN in .env and the model terms accepted on its page)
	PYTHONPATH=src $(PYS) -m details.diar_fetch

run:              ## server on http://localhost:$${PORT:-8765}  (v1 live page at /live)
	PYTHONPATH=src $(PYS) -m details

migrate:          ## apply db/migrations/*.sql to DATABASE_URL
	PYTHONPATH=src $(PYS) -m details.migrate

prompts:          ## sync prompts/*/v*.md into prompt_template (also runs at server start)
	PYTHONPATH=src $(PYS) -m details.llm.prompts

record:           ## record the mic to sessions/my_take.wav (Ctrl+C to stop)
	./eval/record.sh

score:            ## score a take against eval/script_meeting_el_en.md
	$(PY) eval/score.py $(AUDIO) $(MODELS)

test:             ## pytest, excluding -m gpu
	$(PY) -m pytest -q tests

test-js:          ## node --test tests/js (also run by `make test` through pytest; prints SKIP if node is missing)
	@if command -v node >/dev/null; then node --test tests/js; else echo "SKIP: node not found, JS diff tests not run"; fi
