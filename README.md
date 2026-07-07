# Whispered

Privacy-first desktop app for transcribing audio/video and turning it into
publishable content — fully offline, no subscriptions, no cloud uploads.

Transcription runs on `whisper.cpp` (Metal / CUDA / ROCm accelerated),
speaker diarization on `pyannote.audio`, and content generation (articles,
summaries, chapters, YouTube metadata, book chapters, social posts) on a
local LLM via LM Studio's OpenAI-compatible API.

See [APP_DETAILS.md](APP_DETAILS.md) for the full feature rundown and
[ROADMAP.md](ROADMAP.md) for architecture notes and planned work.

## Install

**Fedora / Linux**
```bash
./setup.sh   # installs deps, compiles whisper.cpp, downloads the default model
./run.sh
```

**macOS**
```bash
./setup-mac.sh
./run-mac.sh
```

AMD GPU (ROCm) users: see the ROCm setup steps in
[APP_DETAILS.md](APP_DETAILS.md#-key-features) before running `setup.sh`.

A prebuilt AppImage can be built with `appimage/build-appimage.sh`
(see `appimage/`).

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
# or, for a reproducible pinned install:
pip install -r requirements-dev.lock

ruff check .            # lint — must be clean
python -m pytest tests/ -v
python main.py          # smoke test
```

Regenerate lock files after touching `requirements*.txt`:
```bash
uv pip compile requirements.txt -o requirements.lock
uv pip compile requirements-dev.txt -o requirements-dev.lock
```

Contributing agents/humans: read [ROADMAP.md](ROADMAP.md) §1 (architecture
and mandatory rules) before making changes, and
[AUDIT_HANDOFF.md](AUDIT_HANDOFF.md) for the current quality backlog.
See [TESTING.md](TESTING.md) for test conventions.
