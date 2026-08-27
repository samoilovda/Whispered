# Whispered — project rules

Rules for anyone (human or AI agent) changing this codebase. Distilled from
the original development plan (`docs/archive/ROADMAP_full_2026-07.md`).

## Architecture map

| Layer | Modules | Notes |
|---|---|---|
| Entry | `main.py` | QApplication, theme, locale; calls `multiprocessing.freeze_support()` for the frozen build — do not move it above `_setup_frozen_runtime()` |
| Domain | `domain/` | Qt-free DTOs shared by engines, UI, and Live: `transcription.py` (`Segment`, `Word`, `TranscriptionResult`), `artifact.py` (`Artifact` — provenance record: which transcript revision/provider/model/prompt version produced a generated file, and its `cache_key()`), `job.py` (`JobSpec`/`StepSpec`/`StepOutcome` — a step DAG's shape; see `application/job_engine.py`). Must never import `PyQt6`, `ui`, or `core.live` — enforced by `tests/test_domain_transcription.py`. New shared data types go here, not in `transcriber.py` |
| Application | `application/` | Coordination between UI widgets and engines, above `domain/` but below `ui/`. `document_session.py`: the single `DocumentSession.apply_result()` fan-out that `MainWindow` uses to hand a new/loaded/edited `TranscriptionResult` to every panel that needs it. `export_controller.py`: the Qt-free "what to export and whether it worked" decision behind the Export menu. `job_engine.py`: runs a `JobSpec`'s steps respecting dependency order, per-resource concurrency limits (e.g. `local_llm=1`), and `Artifact`-based cache-skip — **not yet wired into the preset chain or any generator**, see R8 in the audit plan. New panel dependencies/export formats register with the existing controllers instead of adding another hand-copied call site in `main_window.py` |
| Infrastructure | `infrastructure/` | IO adapters below `application/`. Currently `persistence/artifact_store.py`: atomic read/write of an `Artifact`'s provenance manifest (`<path>.manifest.json`) next to the artifact file, and `is_cache_valid()` for reuse decisions. Generators (Cover, article, YouTube, insights, book) are not yet migrated onto it — see R5-full in the audit plan |
| UI | `ui/` | PyQt6 widgets; `main_window.py` owns the preset chain; `ui/__init__.py` intentionally has no re-exports — import from concrete modules |
| Workers | `core/` | `lm_client.py` (LM Studio, OpenAI-compatible), `ai_provider.py` (optional cloud), `insights_worker.py`, `history.py` (SQLite+FTS5), `i18n.py`, `logger.py`, `worker_registry.py` (lifecycle for background `QThread`s — see rule 3) |
| Engines | `transcriber.py` (whisper.cpp in a spawn child process; re-exports the domain DTOs for backward compatibility), `diarizer.py` (pyannote, lazy import), `article_generator.py`, `text_processor.py`, `batch_processor.py`, `book_pipeline.py` |
| Covers | `covers/` | Declarative templates and QPainter renderer; frame/Zoom/ONNX/ComfyUI modules are experimental and not yet wired into the workspace |
| Prompts | `prompts/*.md` | every LLM task is an editable Markdown prompt loaded via `core.prompts.load_prompt` |

Key data types: `Segment`, `Word`, `TranscriptionResult` in
`domain/transcription.py` (also importable from `transcriber` for existing
call sites).

## Mandatory rules

1. **Offline first.** No cloud APIs, no telemetry. Network is allowed only
   to local LM Studio (`config.lm_studio_url`), for explicit user-requested
   model downloads, and for the optional user-keyed cloud provider on the
   YouTube tab (`core/ai_provider.py`, default stays `lmstudio`).
2. **Never block the UI.** Anything longer than ~100 ms goes to a `QThread`
   (pattern: `core/base_worker.py`) or a separate process (pattern:
   `transcriber.py`). UI communication only via Qt signals.
3. **Everything long-running is cancellable** (`cancel()` method) and shuts
   down cleanly in `MainWindow.closeEvent`. Own your `QThread`s through
   `core.worker_registry.WorkerRegistry` rather than a bare `cancel()` +
   `wait()`: a worker that outlives its bounded wait must be retained (not
   abandoned) until it actually finishes — dropping the last reference to,
   or destroying, a still-running `QThread` is what Qt aborts the process
   for. If a business signal happens to be named `finished` (shadowing
   `QThread`'s own), the worker class needs a `_disconnect_business_signals()`
   override — see `core/insights_worker.py` for the pattern.
4. **Settings go through `Config`** (`config.py` dataclass). New fields get
   defaults; the loader drops unknown keys, so backward compatibility is
   automatic.
5. **Code style.** CPython 3.11, type hints, docstrings matching neighbors,
   logging via `core.logger.get_logger(__name__)`. `print()` only in
   standalone CLI scripts (`build.py`, `setup_diarization.py`).
6. **Platforms.** macOS is primary. Linux source installs are supported, but
   AppImage is not a validated release channel. Windows 11 x64 has source,
   packaging, and CI preview support; keep its open hardware/signing gates
   explicit.
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
# ui/ is not typed yet as a whole and is only checked informationally in
# CI; the two files below are the exception (see "Mypy blocking modules").
python -m mypy --ignore-missing-imports core/ transcriber.py diarizer.py \
    exporters.py utils.py config.py version.py domain/ application/ infrastructure/ \
    lm_studio_manager.py batch_processor.py book_pipeline.py \
    ui/transcript_view.py ui/live_transcript_view.py
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

## Cover generator gotchas

- In the source PPTX, the 16:9 slide scale makes the numeric point size equal
  to the pixel size on the 1280×720 project canvas. Do not apply another
  point-to-pixel conversion in the renderer.
- `onnxruntime` is imported only inside restoration/face-detection calls.
  Cover rendering and manual photo selection must keep working without model
  weights or a usable execution provider.
- The PPTX converter drops shapes fully outside the slide. Decorative
  `custGeom` paths accept only `moveTo`, `cubicBezTo`, and `close`; unknown
  geometry commands are errors rather than silently degraded output.
- Bellota Bold is the default OFL-licensed replacement for Templegarten;
  Poiret One remains an optional lighter alternative. Keep each font's OFL
  file with its TTF and preserve the generic fallback path.

## i18n / live language switching

- The UI language switches at runtime with no restart. `core.i18n.set_locale()`
  reloads the strings and fires every callback registered via
  `core.i18n.on_language_changed` (weak refs — pass a bound method or keep
  your own reference). The Settings dialog calls `set_locale()` on Apply/OK.
- Every widget that builds captions with `tr()` must re-apply them on a
  language change. Use `ui.i18n_helpers.Retranslator`: wrap static captions
  with `self._i18n.text(widget, key[, setter][, tooltip=…])`, register a
  method for state-derived text with `self._i18n.call(self._retranslate_…)`,
  then `self._i18n.bind()` once at the end of `__init__`. `MainWindow`,
  `RunView`, `SettingsDialog` keep their own bespoke `_retranslate`.
- Modal dialogs opened with `.exec()` (recipe editor, provider dialog, model
  downloader, speaker rename, …) are rebuilt each time they open and cannot
  coexist with the Settings dialog, so they are deliberately not retranslated.
- Transient strings (status-bar operation text, toasts, already-open message
  boxes) are not retranslated — they re-emit in the active language on next use.

## Layout conventions

- `input/`, `output/` are git-ignored user data.
- Executed plans and historical docs live in `docs/archive/`; strategy docs
  in `docs/`. The public roadmap is `ROADMAP.md`.

## Mypy blocking modules

These modules are now fully typed and have **zero mypy errors**.
Run `python -m mypy --ignore-missing-imports <module>` and ensure it stays
clean before merging changes to these files.

| Module | Fixed in |
|---|---|
| `core/` (all) | P0/P1 audit |
| `transcriber.py`, `diarizer.py`, `exporters.py`, `utils.py`, `config.py` | P0/P1 audit |
| `version.py`, `domain/`, `application/`, `infrastructure/` | written typed from the start (R6–R9) |
| `lm_studio_manager.py` | R12 (P2 audit) |
| `batch_processor.py` | R12 (P2 audit) |
| `book_pipeline.py` | R12 (P2 audit) |
| `ui/transcript_view.py` | R14 (P2 audit) |
| `ui/live_transcript_view.py` | R14 (P2 audit) |

`ui/` as a whole stays informational-only in CI until the rest of it is
typed; these two files are ahead of that and are enforced now.
