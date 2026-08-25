# Whispered

[![CI](https://github.com/samoilovda/Whispered/actions/workflows/ci.yml/badge.svg)](https://github.com/samoilovda/Whispered/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-500%20passing-brightgreen)](TESTING.md)
[![Lint](https://img.shields.io/badge/lint-ruff-261230)](https://docs.astral.sh/ruff/)
[![Python](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platforms-macOS%20%C2%B7%20Linux%20%C2%B7%20Windows%20preview-lightgrey)]()
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Local audio and video transcription with tools for turning recordings into
usable content.**

Whispered is a PyQt6 desktop application. It transcribes recordings with
`whisper.cpp`, can label speakers with `pyannote.audio`, keeps a searchable
history, and helps turn a transcript into subtitles, articles, a YouTube
package, insights, a book draft, or an editing timeline.

Transcription runs locally. AI features use a local LM Studio server by
default. Text is sent to an external service only when a cloud provider is
explicitly selected in the YouTube tab.

🇷🇺 [Русская версия](README.ru.md)

---

## Interface

The window is a two-column workspace: a Library pane on the left (collapsible
or auto-collapsing on narrow windows) and the current document on the right —
a start screen, the record view, the cover workspace, or a run screen,
depending on what you're doing. The layout is remembered between runs.

- The start screen picks a source (file, folder, microphone, or Live) and a
  recipe — one of five built-in step sets (transcript only, YouTube video,
  podcast article, meeting notes, book) or a custom one edited via
  "Configure…" — then launches with a single button; parameters stay out of
  the way behind a one-line summary and an "Change" link.
- Launching runs the recipe as one job through a run screen: one row per
  step (transcribe, clean, article, insights, YouTube package, book, cover),
  live progress, and per-step retry/cancel without re-running what already
  succeeded. Once transcription finishes, its generator panels — Articles,
  YouTube, Book, Insights, Cut, Chat — live as tabs on the record view.
- `Ctrl+K` opens a command palette that searches transcript history, lists
  every recipe ("Run: <recipe>") and the open run's failed/cancelled steps
  ("Restart: <step>"), and exposes quick actions (new record, YouTube
  package, export, Live, queue, settings).
- The Library's record cards show a run's failed steps directly, without
  opening the record, and can be filtered by recipe alongside the existing
  source-kind filter.
- A single status bar at the bottom shows the current operation, progress,
  cancel control, a popover queue for batch jobs, and LM Studio/GPU status.

---

## What the application can do

### Cover generator

Open **Covers** from the Library to create Prosvet YouTube artwork from the
bundled declarative template. The workspace supports duo, solo, and text-only
layouts, mint/warm variants, live preview, manual portraits, and reproducible
PNG/JPEG exports with a `.cover.json` sidecar, plus title suggestions from the
open transcript. The original Templegarten face is replaced by bundled
OFL-licensed Bellota Bold, with a generic system-font fallback. Shorts export
stays opt-in until the authored 9:16 adaptation receives brand approval.

Frame extraction, Zoom-tile detection, ONNX restoration, and the localhost
ComfyUI adapter currently exist as experimental core modules. They are not yet
wired into the Cover workspace; portraits are selected from PNG/JPEG files.

### Transcription

- audio: MP3, WAV, FLAC, M4A, OGG, OPUS, WMA, AAC;
- video: MP4, MKV, AVI, MOV, WebM, WMV, FLV, M4V;
- Whisper models from Tiny and Base through Large v3 and Turbo variants;
- language auto-detection, 19 selectable languages, and translation to English;
- performance profiles and CPU / Metal / CUDA / ROCm acceleration;
- custom vocabulary passed to Whisper as an initial prompt;
- hard cancellation: transcription runs in a child process that can be
  terminated without closing the application.

`ffmpeg` and `ffprobe` are required for media conversion, duration probing, and
video editing.

### Speakers

Optional `pyannote.audio` diarization adds speaker labels. Speakers can be
renamed or merged in the UI; names are saved in history and used by exporters.

### Library, queue, and recorder

- SQLite transcription history with FTS5 full-text search and a `LIKE` fallback;
- reopening saved transcript segments, metadata, and speaker names;
- sequential batch processing for multiple files;
- microphone recording with device selection, level meter, pause, and resume;
- file drag-and-drop and Russian / English UI localization.

History stores transcripts, but it does not restore the content of previously
generated AI artifacts. A recipe's output is saved as separate files in the
application data directory.

### Transcript workspace

- built-in audio/video player with click-to-seek transcript segments;
- segment editing while preserving timestamps;
- find, replace, copy, and speaker renaming;
- 9 export formats: TXT, timestamped TXT, SRT, VTT,
  JSON, Markdown, HTML, DOCX, and PDF.

### AI tools

The following features require LM Studio with a loaded chat model:

- filler removal and spoken-to-written coherence cleanup;
- five content formats: blog post, FAQ, listicle, executive summary, and social
  posts;
- transcript chat with streamed responses;
- insights: chapters, action items, and key moments;
- book pipeline: spoken-text unwrapping, an optional custom prompt, and batch
  processing of Markdown files.

The YouTube package contains:

- timestamped chapters;
- title options;
- a description;
- tags;
- timestamped key questions.

Only the YouTube package can use either LM Studio, an OpenAI-compatible API, or
Anthropic with the user's key. All other AI tools currently use LM Studio.

### API-key storage

When the optional `keyring` package and a working OS backend are available,
cloud-provider API keys and the Hugging Face token are stored in macOS
Keychain, Windows Credential Manager, or a Linux Secret Service. Otherwise
Whispered falls back to its owner-only local configuration file. Use a
dedicated, least-privilege key and protect the user account accordingly.

### Recipes

Transcription and the history save always run. The selected recipe then
runs its remaining steps — any combination of clean, article, insights,
YouTube package, book, and cover — as one job (per-resource concurrency,
cache-skip on an unchanged model/prompt/transcript, point-in-place retry).
Five built-in recipes cover the common cases; "Configure…" opens a step-level
editor for a one-off combination, saved as a single custom recipe. Editing
operations remain manual.

### Editing

The Cut tab lets you choose kept segments, automatically deselect pauses, seek
through the source, export a CMX3600 EDL, and assemble a draft MP4 with
`ffmpeg`. Draft assembly uses per-segment re-encoding by default for more
accurate cut boundaries.

### Live transcription

The experimental Live section can be enabled manually in Settings and is then
started from the source picker (file / folder / recorder / Live) when
starting a new record. It includes:

- microphone and system-audio sources;
- asynchronous preflight checks;
- an incremental transcript, pause/resume, and diagnostics;
- saving finalized text segments to the Library during a session;
- application discovery and system-audio capture through a separate
  ScreenCaptureKit helper.

Live does not write WAV, M4A, or temporary PCM files: audio exists only in
bounded in-memory buffers needed for current recognition. The Library keeps
the transcript and exports, but no player or media-dependent operations are
available for such a session.

System-audio capture requires macOS 13+, a built Swift helper, and Screen
Recording permission. Live is disabled by default and has not yet passed the
full release soak gate.

---

## Quick start

Requirements:

- CPython 3.11;
- `ffmpeg` and `ffprobe`;
- macOS (primary platform), Linux, or Windows 11 x64 preview;
- LM Studio only for AI features.

### macOS

```bash
./setup-mac.sh
./run-mac.sh
```

`setup-mac.sh` creates the environment, builds `pywhispercpp` with Metal on
Apple Silicon, and installs all declared runtime dependencies. Whispered
downloads the selected model when it is first needed.

### Linux

```bash
./setup.sh
./run.sh
```

`setup.sh` selects CUDA, ROCm, or CPU according to the available hardware.
The Fedora-oriented `run.sh` expects the Qt/X11 `xcb` backend.

### Windows 11 preview

Windows source setup and packaging are prepared for Python 3.11 x64 and CPU
transcription:

```powershell
.\setup-windows.ps1
.\run-windows.ps1
```

`ffmpeg` and `ffprobe` must be on `PATH` for conversion and video tools. A
PyInstaller/ZIP/installer pipeline is present under `packaging/windows/`, but
Windows remains a preview until the hardware, clean-VM installer, and release
validation gates in `docs/WINDOWS_SUPPORT_PLAN.ru.md` have been completed.

### Speaker diarization

```bash
.venv/bin/pip install "pyannote.audio>=3.1" "torch>=2.0"
.venv/bin/python setup_diarization.py
```

You need a Hugging Face read token and accepted terms for
`pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`.

### LM Studio

1. Install [LM Studio](https://lmstudio.ai).
2. Load a compatible chat model.
3. Start the local server, normally at `http://localhost:1234/v1`.
4. Change the URL in Whispered Settings if needed.

Transcription, diarization, history, editing, export, and video cutting work
without LM Studio.

---

## Building

macOS application:

```bash
.venv/bin/pip install pyinstaller
.venv/bin/python build.py
```

`build.py` creates `dist/Whispered.app`. The native whisper stack and model
weights stay outside the app bundle, so they can be updated without rebuilding
the application.

The repository also contains `appimage/build-appimage.sh`, but Linux AppImage
is not yet a validated release channel for the complete current feature set.

Windows packaging is available as an unsigned preview workflow:

```powershell
.\packaging\windows\build-windows.ps1
```

It produces an `onedir` package and ZIP. The Inno Setup installer and code
signing remain release-gated; see `packaging/windows/README.md`.

---

## Development and verification

```bash
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/ -q
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/render_ui_gallery.py --check
```

Current repository state: the unit suite and a separate offscreen-Qt smoke
suite in `tests_qt/` pass. CI runs tests and blocking `ruff` checks on Linux
(Python 3.11), and builds an unsigned Windows package with a frozen smoke test
on Python 3.11 on every push. `mypy` is blocking for the module
set listed in [CLAUDE.md](CLAUDE.md) and informational for `ui/`.

The main AI task templates live in 17 Markdown files under `prompts/`. Some
modules also keep embedded fallback text for resilience.

See [TESTING.md](TESTING.md), [ROADMAP.md](ROADMAP.md), and
[CLAUDE.md](CLAUDE.md) for more detail.

---

## Known limitations

- Windows has source, packaging, and CI-preview support, but is not yet a
  validated release platform. Its real-hardware, clean-VM, and signing gates
  remain open.
- Diarization requires separate heavyweight dependencies, a Hugging Face
  token, and accepted model terms.
- Local LM Studio calls are serialized process-wide. Long generations can
  still delay cancellation while the current HTTP response is being read.
- AI requests use a configured context cap; for long recordings, chapters and
  insights are sampled evenly across the recording.
- Cloud providers apply only to the YouTube package.
- The Cover portrait-processing modules are not yet connected to the UI;
  manual PNG/JPEG portrait selection is the supported path.
- Live system-audio capture is macOS-only and remains experimental.

---

## License

[MIT](LICENSE)
