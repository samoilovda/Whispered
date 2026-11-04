# Roadmap

*Working rules for contributors and AI agents live in [AGENTS.md](AGENTS.md).
The original long-form development plan (2026-07) is preserved in
[docs/archive/ROADMAP_full_2026-07.md](docs/archive/ROADMAP_full_2026-07.md).*

## Shipped

- **Core pipeline** — whisper.cpp transcription in a cancellable child
  process, offline speaker diarization (sherpa-onnx, no torch), ffmpeg
  format conversion.
- **Workspace shell redesign** — a two-column shell (Library / center):
  one start screen (source chips, a five-recipe picker plus a step-level
  recipe editor, a single Launch button) replaces the old inspector rail
  and its Materials/Insights/Cut/Chat/Settings pages, which are now tabs
  on the record view itself. Launching runs the selected recipe as one
  `JobRunner`-driven job through a dedicated run screen (per-step status,
  point-in-place retry, cancel) instead of the old one-click-per-step
  preset chain. `Ctrl+K` covers records, recipes ("Run: <recipe>"), the
  open run's failed/cancelled steps ("Restart: <step>"), and the existing
  six actions; the Library's record cards show a run's failed steps
  without opening them, filterable by recipe. A single status bar covers
  operation/progress/cancel/queue/LLM status. Live capture is a start-
  screen source rather than a parallel screen, feeding the same
  `JobRunner` with its transcribe step marked skipped (already streamed).
  (docs/UI_REDESIGN_PLAN_2026-09.ru.md, Track B.)
- **Library** — transcription history with SQLite FTS5 full-text search,
  built-in audio player synced to the transcript, editable transcript with
  speaker renaming.
- **UI concept 2026-10** — a Library that lists records (filters in one
  menu, date groups, rename); a transcript that reads as paragraphs, follows
  playback, marks uncertain passages and has a context menu (copy with
  timecode, bookmark, chapter, cut); bookmarks; record tabs that appear with
  their material and a header with the last run; a one-row player with
  playback keys; a start screen that says whether the recipe can run; the
  user's own notes feeding Insights; a grouped forgiving `Ctrl+K`; a
  menu-bar icon; a shortcuts sheet
  (docs/archive/UI_CONCEPT_IMPLEMENTATION_PLAN_2026-10.ru.md).
- **Recipes** — five built-in step sets (transcript-only, YouTube video,
  podcast article, meeting notes, book) plus one user-editable custom
  slot, replacing the old Inspector checklist/preset chain: transcribe →
  any combination of clean, article, insights, YouTube package, book, and
  cover generation, each a `JobEngine` step with cache-skip and
  provenance, artifacts auto-saved.
- **YouTube package** — titles, hook+summary description, viewer-oriented
  chapter timecodes (≤7.5-minute gaps), key-question timecodes, tags;
  local LLM by default, optional user-keyed cloud provider.
- **Content tools** — transcript cleanup, article drafts in 5 formats,
  AI chat with the transcript, insights (chapters / action items / key
  moments), book pipeline, Cut tab with EDL export.
- **9 export formats** — TXT, TXT+timestamps, SRT, VTT, JSON, MD, HTML,
  DOCX, and PDF are available in the record export menu.
- **Polish** — RU/EN localization, dark/light themes, mic recorder, batch
  queue, custom vocabulary, standalone macOS `.app` build (PyInstaller,
  external whisper stack), Linux source setup, and an unvalidated AppImage
  build script.
- **Prosvet cover MVP** — declarative 16:9/9:16 templates, QPainter preview,
  manual portraits, title suggestion, and PNG/JPEG/sidecar export. Portraits
  can also come from the record's video: a frame picker, a "whose photo is
  this?" choice of Zoom gallery tiles, and per-speaker photo variants framed
  by the face. ONNX face restoration and the ComfyUI adapter exist but are
  not yet connected to UI.
- **Record materials and automation** — subtitles (edit, SRT/VTT export
  and import), clips (edit, preview, MP4 render), tasks with CSV export; a
  watch folder with its own recipe, an opt-in "new Zoom recording" offer,
  the YouTube upload queue (in the app and `tools/youtube_autoupload.py`)
  and the SSH push to the user's server (`tools/hermes_push.py`); deleting
  a record withdraws its upload and push.
- **Quality** — a unit suite with fully stubbed Qt plus a separate
  offscreen real-Qt smoke suite (`tests_qt/`), ruff-clean codebase, blocking
  mypy on `core/`/engines, headless UI smoke checks in the pre-commit gate.

## Current focus

*Last audit: 2026-11-04 — [docs/AUDIT_2026-11-04.ru.md](docs/AUDIT_2026-11-04.ru.md)
is the register of open defects; its §11 says which item each commit closed.
The 2026-10-27 audit (A01–A34) is closed and archived
([docs/archive/AUDIT_2026-10-27.ru.md](docs/archive/AUDIT_2026-10-27.ru.md)),
as are the 2026-08 audits before it. Real hardware gates (transcription
with weights, LM Studio, YouTube upload, Hermes push to a real server,
signed macOS/Windows builds, Live soak) are still checked by hand.*

- **Local-LLM robustness** — local completions are serialized process-wide;
  network cancellation is now interruptible (LM Studio streaming reads poll
  instead of blocking on a single long socket timeout), and the book,
  cleaning and article steps read their answers as a stream too. Remaining:
  Anthropic's client is still a single non-streaming request with no
  mid-flight abort, simplify YouTube worker orchestration, and prevent
  failed generations from being saved as results.
- **Structural cleanup** — Qt-free domain types (`domain/transcription.py`,
  `domain/artifact.py`, `domain/job.py`) are split out of `transcriber.py`;
  record lifecycle, export flows and operations/Cancel have their own
  controllers (`ui/document_lifecycle_controller.py`,
  `ui/export_flow_controller.py`, `ui/operation_controller.py`), panels are
  driven through public methods only, and one whisper model runs at a time
  (`OperationController.asr_holder()`). Still open (audit 2026-11-04 AR1):
  `main_window.py` is ~3 960 lines and the controllers reach into it
  through `host._*` — narrow per-controller host protocols are next.

## Next

*A post-redesign review (2026-08-26) produced a worked plan for the next
round: nine design cleanups plus eleven features, each with the files to
touch, acceptance criteria and tests —
[docs/IMPROVEMENT_PLAN_2026-08.ru.md](docs/IMPROVEMENT_PLAN_2026-08.ru.md).
Its plan item B1 (cache-skip) is done: recipe runs and single-step jobs
pass `build_cache_checks()` to `JobRunner`, and a skipped step reloads its
artifact through its `load_artifact`.*

- **Live transcription stabilization** — the opt-in L1–L14 foundation is in
  the development branch, including the UI-independent session pipeline and
  a buildable ScreenCaptureKit helper. Gate A/Gate B are not complete: pass
  the real mic/model soak and Zoom/Meet/Teams matrix while the implemented
  L15–L21 UI/product path remains behind the disabled feature flag. See
  [docs/LIVE_TRANSCRIPTION_PLAN.ru.md](docs/LIVE_TRANSCRIPTION_PLAN.ru.md#62-ревизия-l1l14-и-обязательная-стабилизация-перед-l15).
- **Course Capture panel** — a queue of lessons (`domain/course_capture.py`,
  `ui/course_capture_panel.py`) recorded one at a time via the same
  system-audio `LiveRuntime` path as Live, for content the user is
  legitimately watching but can't download (e.g. a copy-protected course
  video) — captures the app's audio output during normal playback, not a
  downloader. Reuses `LiveSetupPanel` for target/model/language, saves each
  finished lesson straight to history tagged with the course name, and can
  stitch every finished lesson into one plain-text document. Sits behind
  the same `live_transcription_enabled` flag as Live, since it shares its
  unreleased capture pipeline; manually verified end-to-end (Chrome target
  discovery, real non-silent capture) but has no automated Qt-level
  coverage of the capture flow itself yet, only the Qt-free queue model.
- **Distribution** — Flathub package for Linux; signed, notarized DMG for
  macOS. Goal: install without cloning the repo.
- **Cover portrait pipeline** — the frame picker, Zoom-tile choice and
  speaker photo variants are connected; what is left is connecting the ONNX
  restoration and ComfyUI modules through cancellable UI workers, with model
  download/integrity handling.
- **Windows support** — setup/run scripts, packaging (PyInstaller + Inno
  Setup), and a CI job that builds the frozen package and runs a smoke test
  on every push are in place. What's left before calling it a supported
  release platform: real-hardware validation, a clean-VM install check, and
  code signing (`docs/WINDOWS_SUPPORT_PLAN.ru.md`).

## Ideas parked for later

Global dictation hotkey, user prompt library and word-level karaoke
highlighting (the watch folder and the CLI tools — `tools/youtube_autoupload.py`,
`tools/hermes_push.py` — have shipped since). These are documented in the archived plan;
none are scheduled.
