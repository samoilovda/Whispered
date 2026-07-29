# Whispered — project rules

Rules for anyone (human or AI agent) changing this codebase. Distilled from
the original development plan (`docs/archive/ROADMAP_full_2026-07.md`).

## Architecture map

| Layer | Modules | Notes |
|---|---|---|
| Entry | `main.py` | QApplication, theme, locale; calls `multiprocessing.freeze_support()` for the frozen build — do not move it above `_setup_frozen_runtime()` |
| UI | `ui/` | PyQt6 widgets; `main_window.py` owns the preset chain; `ui/__init__.py` intentionally has no re-exports — import from concrete modules |
| Workers | `core/` | `lm_client.py` (LM Studio, OpenAI-compatible), `ai_provider.py` (optional cloud), `insights_worker.py`, `history.py` (SQLite+FTS5), `i18n.py`, `logger.py` |
| Engines | `transcriber.py` (whisper.cpp in a spawn child process), `diarizer.py` (pyannote, lazy import), `article_generator.py`, `text_processor.py`, `batch_processor.py`, `book_pipeline.py` |
| Prompts | `prompts/*.md` | every LLM task is an editable Markdown prompt loaded via `core.prompts.load_prompt` |

Key data types: `TranscriptionResult` and `Segment` (`start`, `end`, `text`,
`speaker`) in `transcriber.py`.

## Mandatory rules

1. **Offline first.** No cloud APIs, no telemetry. Network is allowed only
   to local LM Studio (`config.lm_studio_url`), for explicit user-requested
   model downloads, and for the optional user-keyed cloud provider on the
   YouTube tab (`core/ai_provider.py`, default stays `lmstudio`).
2. **Never block the UI.** Anything longer than ~100 ms goes to a `QThread`
   (pattern: `core/base_worker.py`) or a separate process (pattern:
   `transcriber.py`). UI communication only via Qt signals.
3. **Everything long-running is cancellable** (`cancel()` method) and shuts
   down cleanly in `MainWindow.closeEvent`.
4. **Settings go through `Config`** (`config.py` dataclass). New fields get
   defaults; the loader drops unknown keys, so backward compatibility is
   automatic.
5. **Code style.** Python 3.10+, type hints, docstrings matching neighbors,
   logging via `core.logger.get_logger(__name__)`. `print()` only in
   standalone CLI scripts (`build.py`, `setup_diarization.py`).
6. **Platforms.** Linux (primary) and macOS are both supported and tested;
   keep `appimage/`, `setup.sh`/`run.sh` working. Windows is a future task.
7. **Dependencies.** Pin minimum versions in `requirements.txt`; import
   heavy/optional deps (pyannote) lazily with a clear error message.
8. **One step = one commit** (`feat:`/`fix:`/`docs:`/`chore:`). Don't mix
   refactoring with features.

## Pre-commit gate

```bash
ruff check .                     # must be clean
python -m pytest tests/ -q      # system python — Qt is stubbed in tests/conftest.py
python -m compileall -q . -x '.venv|.claude|build|dist|docs/archive'
# mypy is a blocking gate for this set — it is clean and must stay clean.
# ui/ is not typed yet and is only checked informationally in CI.
python -m mypy --ignore-missing-imports core/ transcriber.py diarizer.py \
    exporters.py utils.py config.py
# real-Qt headless smoke (PyQt6 lives only in the project venv):
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests_qt/ -q
```

Unit tests run with **system python** against PyQt6 stubs; anything that
needs real Qt runs with `.venv/bin/python` and `QT_QPA_PLATFORM=offscreen`.
CI mirrors all of the above (`.github/workflows/ci.yml`).

## Standalone build gotchas

- `.venv/bin/python build.py` → `dist/Whispered.app`. The whisper stack
  (pywhispercpp + libwhisper + its pure-python deps) is deliberately NOT
  bundled — build.py deploys it to
  `~/Library/Application Support/Whispered/lib`, and
  `main.py::_setup_frozen_runtime()` puts that dir on `sys.path`.
- Transcription children are spawned by re-executing the frozen binary:
  `multiprocessing.freeze_support()` in `main.py` must run after
  `_setup_frozen_runtime()` (children import pywhispercpp from the external
  lib dir) and before any Qt import.
- Standalone driver scripts that use `multiprocessing` spawn MUST have an
  `if __name__ == "__main__"` guard.

## Local LLM gotchas

- LM Studio CLI: `/Users/den/.lmstudio/bin/lms`; load with the full key and
  flags (`lms load google/gemma-4-12b -y --gpu max`) — bare `lms load`
  opens an interactive picker that hangs non-interactive shells.
- gemma-4 is a reasoning model: hidden reasoning shares the `max_tokens`
  budget (`core/insights_worker.py::_RESPONSE_MAX_TOKENS`) and streams no
  visible content. Symptom of a starved budget: empty response with
  `finish_reason: length`.
- Don't fire parallel LLM requests at LM Studio on long transcripts — the
  server can stop responding while `lms server status` still says running.
  Fix: `lms server stop && lms server start`, reload the model.
- Prompts embed the transcript as ~25-second coalesced blocks
  (`_build_prompt_text`) — don't reintroduce per-segment timestamp lines.
- `word_timestamps=True` breaks Cyrillic transcripts (word-gluing in
  `_group_words_into_segments`) — the default flow must keep it `False`.

## Layout conventions

- `input/`, `output/` are git-ignored user data.
- Executed plans and historical docs live in `docs/archive/`; strategy docs
  in `docs/`. The public roadmap is `ROADMAP.md`.
