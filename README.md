# Whispered

[![CI](https://github.com/samoilovda/Whispered/actions/workflows/ci.yml/badge.svg)](https://github.com/samoilovda/Whispered/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-403%20passing-brightgreen)](TESTING.md)
[![Lint](https://img.shields.io/badge/lint-ruff-261230)](https://docs.astral.sh/ruff/)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
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

## What the application can do

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
generated AI artifacts. Preset output is saved as separate files in the
application data directory.

### Transcript workspace

- built-in audio/video player with click-to-seek transcript segments;
- segment editing while preserving timestamps;
- find, replace, copy, and speaker renaming;
- 9 formats implemented by the export module: TXT, timestamped TXT, SRT, VTT,
  JSON, Markdown, HTML, DOCX, and PDF.

The current multi-export UI exposes TXT, SRT, VTT, JSON, Markdown, HTML, and
DOCX. Timestamped TXT and PDF exporters exist in code but are not yet exposed
in that menu.

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

Cloud-provider API keys and the optional Hugging Face token are stored in the
local application configuration file. Whispered restricts this file to the
current user where the operating system supports POSIX permissions, but it is
not encrypted or stored in the OS credential vault. Use a dedicated,
least-privilege key and protect the user account accordingly.

### Presets

The Library launch bar offers four workflows:

| Preset | What it runs |
|---|---|
| Transcribe only | transcription and history save |
| Transcribe + YouTube package | transcription and five YouTube artifacts |
| Transcribe + Article | transcription, cleanup, and all five article formats |
| Full package | transcription, YouTube package, cleanup, and all five article formats |

Insights, the book pipeline, and editing operations are started manually from
the record workspace and are not part of the Full package preset.

### Editing

The Cut tab lets you choose kept segments, automatically deselect pauses, seek
through the source, export a CMX3600 EDL, and assemble a draft MP4 with
`ffmpeg`. Draft assembly uses per-segment re-encoding by default for more
accurate cut boundaries.

### Live transcription

The experimental Live section can be enabled manually in Settings. It includes:

- microphone and system-audio sources;
- asynchronous preflight checks;
- an incremental transcript, pause/resume, and diagnostics;
- saving a completed session to the Library;
- application discovery and system-audio capture through a separate
  ScreenCaptureKit helper.

System-audio capture requires macOS 13+, a built Swift helper, and Screen
Recording permission. Live is disabled by default and has not yet passed the
full release soak gate.

---

## Quick start

Requirements:

- Python 3.10+;
- `ffmpeg` and `ffprobe`;
- macOS (primary platform), Linux, or Windows 11 x64 preview;
- LM Studio only for AI features.

### macOS

```bash
./setup-mac.sh
.venv/bin/pip install -r requirements.txt
./run-mac.sh
```

`setup-mac.sh` creates the environment and builds `pywhispercpp` with Metal on
Apple Silicon. Whispered downloads the selected runtime model when needed.
Installing `requirements.txt` adds the microphone-recording and DOCX-export
dependencies.

### Linux

```bash
./setup.sh
.venv/bin/pip install -r requirements.txt
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

Current repository state: 403 tests pass. CI runs tests and blocking `ruff`
checks on Linux (Python 3.10 and 3.12), and is configured to build an unsigned
Windows package with a frozen smoke test on Python 3.11. `mypy` is
informational.

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
- Long or parallel requests can overwhelm LM Studio; YouTube and Insights
  start several generations concurrently.
- AI requests use a configured context cap; for long recordings, chapters and
  insights are sampled evenly across the recording.
- Cloud providers apply only to the YouTube package.
- Live system-audio capture is macOS-only and remains experimental.

---

## License

[MIT](LICENSE)
