# Roadmap

*Working rules for contributors and AI agents live in [CLAUDE.md](CLAUDE.md).
The original long-form development plan (2026-07) is preserved in
[docs/archive/ROADMAP_full_2026-07.md](docs/archive/ROADMAP_full_2026-07.md).*

## Shipped

- **Core pipeline** — whisper.cpp transcription in a cancellable child
  process, pyannote speaker diarization, ffmpeg format conversion.
- **Workspace shell redesign** — three permanent columns (Library / document
  / Inspector rail with Materials, Insights, Cut, Chat, Settings sections),
  `Ctrl+K` command palette over the existing FTS index, a single status bar
  for operation/progress/cancel/queue/LLM status, and a new-record source
  picker (file / folder / recorder / Live) replacing the old sidebar screens.
- **Library** — transcription history with SQLite FTS5 full-text search,
  built-in audio player synced to the transcript, editable transcript with
  speaker renaming.
- **Checklist presets** — an Inspector checklist runs a whole chain in one
  go: transcribe → any combination of YouTube package, article, insights, and
  book generation, with artifacts auto-saved.
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
  manual portraits, title suggestion, and PNG/JPEG/sidecar export. Frame,
  Zoom-tile, ONNX, and ComfyUI modules exist but are not yet connected to UI.
- **Quality** — a unit suite with fully stubbed Qt plus a separate
  offscreen real-Qt smoke suite (`tests_qt/`), ruff-clean codebase, blocking
  mypy on `core/`/engines, headless UI smoke checks in the pre-commit gate.

## Current focus

*Last audit: 2026-08-13, execution tracked in
[docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md](docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md).
P0 (worker lifecycle, model integrity, Recorder backpressure, system-audio
IPC auth, output-path collisions) and P2 (Config validation, Cover template
path containment, structured export/batch errors, FTS rebuild policy) are
closed as of 2026-08-18. P1 structural work is partial: `DocumentSession`
and an `export_controller` are extracted from `main_window.py`; a Job/
Pipeline engine exists but isn't wired into the preset chain yet (the one
concrete bug it was meant to fix — "chapters" computed twice when both
YouTube and Insights are enabled — is closed separately via a shared
`InsightsCache`, not via the engine itself); the `Artifact` provenance
model is now wired into every generator that actually writes a file —
Cover, article, YouTube, and book — with one deliberate exception:
Insights has no file-writing action of its own to attach provenance to.
`provider`/`model`/`prompt_version` stay unfilled everywhere (no reliable
way to tell whether a saved file's text still matches the exact LLM call
that produced it, versus being hand-edited after). Release metadata
(`pyproject.toml`/`version.py` as the single version source) is in
place; the resource-manifest/CI parts of that work need a real build
environment to validate and are not attempted from here.*

- **Local-LLM robustness** — local completions are serialized process-wide;
  network cancellation is now interruptible (LM Studio streaming reads poll
  instead of blocking on a single long socket timeout). Remaining:
  Anthropic's client is still a single non-streaming request with no
  mid-flight abort, simplify YouTube worker orchestration, and prevent
  failed generations from being saved as results.
- **Structural cleanup** — Qt-free domain types (`domain/transcription.py`,
  `domain/artifact.py`, `domain/job.py`) are split out of `transcriber.py`;
  `main_window.py` (still 1800+ lines) has `DocumentSession`/
  `export_controller` extracted but transcription/pipeline orchestration
  is intentionally still inline — it turned out to be mostly direct widget
  calls with no further logic worth pulling into a separate file.

## Next

- **Live transcription stabilization** — the opt-in L1–L14 foundation is in
  the development branch, including the UI-independent session pipeline and
  a buildable ScreenCaptureKit helper. Gate A/Gate B are not complete: pass
  the real mic/model soak and Zoom/Meet/Teams matrix while the implemented
  L15–L21 UI/product path remains behind the disabled feature flag. See
  [docs/LIVE_TRANSCRIPTION_PLAN.ru.md](docs/LIVE_TRANSCRIPTION_PLAN.ru.md#62-ревизия-l1l14-и-обязательная-стабилизация-перед-l15).
- **Distribution** — Flathub package for Linux; signed, notarized DMG for
  macOS. Goal: install without cloning the repo.
- **Cover portrait pipeline** — connect the existing frame picker/extraction,
  Zoom-tile, ONNX, and ComfyUI modules through cancellable UI workers and add
  model download/integrity handling.
- **Windows support** — setup/run scripts, packaging (PyInstaller + Inno
  Setup), and a CI job that builds the frozen package and runs a smoke test
  on every push are in place. What's left before calling it a supported
  release platform: real-hardware validation, a clean-VM install check, and
  code signing (`docs/WINDOWS_SUPPORT_PLAN.ru.md`).

## Ideas parked for later

Global dictation hotkey, user prompt library, watch-folder/CLI mode, and
word-level karaoke highlighting. These are documented in the archived plan;
none are scheduled.
