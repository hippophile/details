PY := .venv/bin/python -I
PYS := .venv/bin/python
AUDIO ?= sessions/my_take.wav
MODELS ?=

.PHONY: setup run record score test migrate

setup:            ## one-time: ffmpeg + Python 3.12 venv + packages + Whisper models (~4.6 GB)
	@command -v ffmpeg >/dev/null || brew install ffmpeg
	uv venv -q --python 3.12 .venv
	VIRTUAL_ENV=.venv uv pip install -q -r requirements.txt
	$(PY) -c "from huggingface_hub import snapshot_download as d; [print(d(r)) for r in ('mlx-community/whisper-large-v3-turbo','mlx-community/whisper-large-v3-mlx')]"

run:              ## server on http://localhost:$${PORT:-8765}  (v1 live page at /live)
	PYTHONPATH=src $(PYS) -m details

migrate:          ## apply db/migrations/*.sql to DATABASE_URL
	PYTHONPATH=src $(PYS) -m details.migrate

record:           ## record the mic to sessions/my_take.wav (Ctrl+C to stop)
	./eval/record.sh

score:            ## score a take against eval/script_meeting_el_en.md
	$(PY) eval/score.py $(AUDIO) $(MODELS)

test:             ## pytest, excluding -m gpu
	$(PY) -m pytest -q tests
