# Whispered — agent and contributor rules

Offline-first PyQt6 transcription app. These rules apply to every change.

## Before you change X, read Y

- `build.py`, `main.py` startup, `packaging/`, `appimage/`, `release.yml`,
  `linux-package.yml`, version bumps, signing → `docs/agents/build.md`.
- An LLM step, `prompts/`, `core/lm_client.py`, `core/insights*.py`,
  `article_generator.py`, `text_processor.py`, `book_pipeline.py`, or anything
  that sends requests to LM Studio (benchmarks included) →
  `docs/LLM_RESEARCH.ru.md` (mandatory) and `docs/agents/llm.md`.
- A user-visible string, `tr()`, `locales/`, plural counts, list dates →
  `docs/agents/i18n.md`.
- `covers/`, `assets/covers/`, `assets/fonts/`, `tools/pptx_to_template.py`,
  the cover step → `covers/AGENTS.md`.
- Code in `ui/`, `core/`, `application/`, `domain/` → that directory's `AGENTS.md`.

## Layers

- `main.py` — entry: QApplication, theme, locale, frozen-runtime setup.
- `domain/` — Qt-free DTOs and pure logic (`Segment`, `Word`,
  `TranscriptionResult` in `domain/transcription.py`).
- `application/` — Qt-free coordination of UI and engines: job engine, step
  registry, export/readiness decisions, multi-call history writes.
- `infrastructure/` — IO adapters; `persistence/artifact_store.py` writes every
  generator's provenance manifest.
- `ui/` — PyQt6 widgets, flow controllers; `core/` — workers, LLM clients,
  history (SQLite+FTS5), i18n, model downloads.
- Root engines — `transcriber.py` (whisper.cpp in a spawn child), `diarizer.py`,
  `article_generator.py`, `text_processor.py`, `batch_processor.py`,
  `book_pipeline.py`. Keep `word_timestamps=False` in the default flow: `True`
  glues Cyrillic words (`transcriber._group_words_into_segments`).
- `covers/` — cover templates, QPainter renderer; `prompts/*.md` — LLM prompts.

Import rules, enforced by `tests/test_layer_imports.py` and
`tests/test_domain_transcription.py`:

- `domain/` never imports `PyQt6`, `ui`, `core.live`, `core`, `application`
  or `infrastructure`.
- `application/` and `infrastructure/` never import `PyQt6`, `ui` or `core.live`.
- `core/` never imports `application/` or `ui/`.
- Nothing in `domain/`, `application/`, `infrastructure/`, `core/` imports
  `transcriber.py` or `batch_processor.py`; shared Qt-free parts go to `core/`.
- `ui/__init__.py` has no re-exports: import from concrete modules.

## Mandatory rules

1. **Offline first.** No telemetry, no cloud APIs. Network only for:
   - local LM Studio at `Config.lm_studio_url`;
   - model downloads the user explicitly requested;
   - the optional user-keyed cloud provider on the YouTube tab
     (`core/ai_provider.py`; `Config.yt_provider` defaults to `lmstudio`);
   - upload to the user's own YouTube channel (`core/youtube_oauth.py`,
     `core/youtube_upload.py`, `application/youtube_autoupload.py`;
     `Config.yt_publish_mode` defaults to `off`) only when the user presses the
     publish dialog's button, presses "Upload" in File → YouTube upload queue
     (`ui/youtube_queue_dialog.py`), or runs `tools/youtube_autoupload.py` —
     only for packages queued on the wizard's last step; `--update` may push
     their edited title, description and tags to already-uploaded videos;
   - SSH copy of finished records whose video is on YouTube (cleaned text,
     speaker transcript with the user's voice marked, YouTube package) to the
     user's own server, only when `Config.hermes_push_target` is set (empty by
     default) and `tools/hermes_push.py` runs (`application/hermes_push.py`).
2. **Never block the UI.** Work over ~100 ms goes to a `QThread` (pattern:
   `core/base_worker.py`) or a separate process (pattern: `transcriber.py`).
   UI communication only via Qt signals.
3. **Everything long-running is cancellable** (`cancel()`) and shuts down
   cleanly in `MainWindow.closeEvent`.
   - Own `QThread`s through `core.worker_registry.WorkerRegistry`, not bare
     `cancel()` + `wait()`: a worker that outlives its bounded wait must be
     retained until it finishes — dropping the last reference to, or
     destroying, a running `QThread` makes Qt abort the process.
   - A business signal named `finished` (shadowing `QThread.finished`) needs a
     `_disconnect_business_signals()` override; pattern: `core/insights_worker.py`.
   - A thread that blocks waiting for another worker's signal (no event loop)
     connects with `Qt.ConnectionType.DirectConnection` (`BatchWorker`,
     `Transcriber.transcribe(direct_callbacks=True)`).
   - Such paths need a real-Qt test in `tests_qt/`; the `tests/conftest.py`
     stubs never start a thread.
4. **Settings go through `Config`** (`config.py` dataclass). New fields get
   defaults; the loader drops unknown keys.
5. **Code style.** CPython 3.11, type hints, docstrings matching neighbours,
   logging via `core.logger.get_logger(__name__)`; `print()` only in standalone
   CLI scripts (`build.py`, `tools/`).
6. **Platforms.** macOS is primary. Linux source installs are supported;
   `packaging/linux/` is a preview Fedora PyInstaller tarball (mic-only, no
   system-audio helper, no RPM/Flatpak, no `requirements-linux.lock`);
   `appimage/` is an unvalidated experiment. Windows 11 x64 has source,
   packaging and CI preview support; keep its hardware/signing gates explicit.
7. **Dependencies.** Pin minimum versions in `requirements.txt`; import heavy
   or optional deps lazily with a clear error message.
8. **One step = one commit** (`feat:`/`fix:`/`docs:`/`chore:`); never mix
   refactoring with features.

## User data

- Never commit user data (`input/`, `output/`, `models/`, recordings,
  transcripts) or put real transcript text into tests or docs.
- Never read or commit `private_plans/` (git-ignored personal material).
- A script that mutates `get_config()` in memory gets persisted by any later
  `save_config()`: restore the user's config on disk afterwards.

## Pre-commit gate

`tests/` and lint run on **system python** (Qt stubbed in `tests/conftest.py`;
needs pytest, numpy, requests, python-docx, lxml). mypy and `tests_qt/` need
real PyQt6: run them with `.venv/bin/python` (one-time: `.venv/bin/python -m
pip install mypy==1.20.2 types-requests`). CI (`.github/workflows/ci.yml`)
mirrors this. mypy blocks on every path below; a new top-level module joins
it here and in `ci.yml`.

```bash
ruff check .
python -m pytest tests/ -q
python -m compileall -q . -x '.venv|.claude|build|dist|docs/archive'
.venv/bin/python -m mypy --ignore-missing-imports core/ transcriber.py diarizer.py \
    exporters.py utils.py config.py version.py domain/ application/ infrastructure/ \
    batch_processor.py book_pipeline.py ui/
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests_qt/ -q
```

## Documents

- Current: `README.md`, `ROADMAP.md`, `TESTING.md`, `docs/HERMES_PUSH.md`,
  `docs/YOUTUBE_UPLOAD.ru.md`, `docs/SYSTEM_CAPTURE_IPC.ru.md`,
  `docs/LIVE_COMPATIBILITY_MATRIX.ru.md`, `docs/release-notes/`.
- In force: `docs/LLM_RESEARCH.ru.md` (see triggers); `docs/AUDIT_2026-10-27.ru.md`
  (open defects); open plans — read when the task touches their area:
  `docs/LIVE_TRANSCRIPTION_PLAN.ru.md`, `docs/WINDOWS_SUPPORT_PLAN.ru.md`,
  `docs/MULTITRACK_ZOOM_PLAN.ru.md`, `docs/WHISPER_ECOSYSTEM_PLAN_2026-10.ru.md`,
  `docs/UI_UX_AGENT_IMPLEMENTATION_2026-10-18.ru.md` (+ its audit, status log).
- Historical: every other file in `docs/` (strategy, dated snapshots, finished
  plans and audits) and `docs/archive/`. Read only when the task names them;
  move newly finished plans to `docs/archive/`.
