# Roadmap

*Working rules for contributors and AI agents live in [CLAUDE.md](CLAUDE.md).
The original long-form development plan (2026-07) is preserved in
[docs/archive/ROADMAP_full_2026-07.md](docs/archive/ROADMAP_full_2026-07.md).*

## Shipped

- **Core pipeline** — whisper.cpp transcription in a cancellable child
  process, pyannote speaker diarization, ffmpeg format conversion.
- **Library-first UI** — sidebar navigation (Library / Queue / Recorder),
  transcription history with SQLite FTS5 full-text search, built-in audio
  player synced to the transcript, editable transcript with speaker renaming.
- **Launch-bar presets** — one click runs a whole chain: transcribe →
  YouTube package and/or article generation, with artifacts auto-saved.
- **YouTube package** — titles, hook+summary description, viewer-oriented
  chapter timecodes (≤7.5-minute gaps), key-question timecodes, tags;
  local LLM by default, optional user-keyed cloud provider.
- **Content tools** — transcript cleanup, article drafts in 5 formats,
  AI chat with the transcript, insights (chapters / action items / key
  moments), book pipeline, Cut tab with EDL export.
- **9 export formats** — TXT, TXT+timestamps, SRT, VTT, JSON, MD, HTML,
  DOCX, PDF.
- **Polish** — RU/EN localization, dark/light themes, mic recorder, batch
  queue, custom vocabulary, standalone macOS `.app` build (PyInstaller,
  external whisper stack), AppImage build script.
- **Quality** — 360 unit tests with fully stubbed Qt, ruff-clean codebase,
  headless UI smoke checks in the pre-commit gate.

## Current focus

- **Local-LLM robustness** — sequential insight generation (parallel
  requests can overwhelm LM Studio), reasoning-model token budgets,
  failed-generation artifacts must never be saved as results.

## Next

- **Live transcription stabilization** — the opt-in L1–L14 foundation is in
  the development branch, including the UI-independent session pipeline and
  a buildable ScreenCaptureKit helper. Gate A/Gate B are not complete: pass
  the real mic/model soak and Zoom/Meet/Teams matrix while the implemented
  L15–L21 UI/product path remains behind the disabled feature flag. See
  [docs/LIVE_TRANSCRIPTION_PLAN.ru.md](docs/LIVE_TRANSCRIPTION_PLAN.ru.md#62-ревизия-l1l14-и-обязательная-стабилизация-перед-l15).
- **Distribution** — Flathub package for Linux; signed, notarized DMG for
  macOS. Goal: install without cloning the repo.
- **Windows support** — setup/run scripts, CI coverage, packaging. The code
  uses `pathlib` throughout, but real Windows support needs testing and
  scripts.

## Ideas parked for later

Global dictation hotkey, user prompt library, watch-folder/CLI mode, and
word-level karaoke highlighting. These are documented in the archived plan;
none are scheduled.
