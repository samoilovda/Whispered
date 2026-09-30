# Whispered

[![CI](https://github.com/samoilovda/Whispered/actions/workflows/ci.yml/badge.svg)](https://github.com/samoilovda/Whispered/actions/workflows/ci.yml)
[![Lint](https://img.shields.io/badge/lint-ruff-261230)](https://docs.astral.sh/ruff/)
[![Python](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platforms-macOS%20%C2%B7%20Linux%20%C2%B7%20Windows%20preview-lightgrey)]()
[![Latest release](https://img.shields.io/github/v/release/samoilovda/Whispered?include_prereleases&sort=semver)](https://github.com/samoilovda/Whispered/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Local audio and video transcription with tools for turning recordings into
usable content.**

Whispered is a PyQt6 desktop application. It transcribes recordings with
`whisper.cpp`, can label speakers with `pyannote.audio`, keeps a searchable
history, and runs *recipes* — named sets of steps that turn a transcript into
cleaned text, an article, insights, a YouTube package, a book draft, or a
cover — plus subtitles and an editing timeline.

Transcription runs locally. AI features use a local LM Studio server by
default. Text is sent to an external service only when a cloud provider is
explicitly selected in the YouTube tab.

🇷🇺 [Русская версия](README.ru.md)

---

## Download

Prebuilt applications are published on the
[**Releases**](https://github.com/samoilovda/Whispered/releases/latest) page:

| Platform | File |
|---|---|
| macOS (Apple Silicon, 13+) | `Whispered-<version>-macos-arm64.zip` |
| Windows 11 (x64) | `Whispered-<version>-windows-x64.zip` |
| Linux (x86_64, Fedora-oriented) | `Whispered-<version>-linux-x86_64.tar.gz` |

All builds are currently **unsigned**. On macOS, right-click `Whispered.app`
→ **Open** on first launch (or `xattr -dr com.apple.quarantine
/Applications/Whispered.app`); on Windows, choose **More info** → **Run
anyway** in SmartScreen. On Linux, extract the tarball and run
`./Whispered/Whispered` (needs `ffmpeg` and `xcb-util-cursor`; see
`packaging/linux/README.md`). Each release's notes spell this out in English
and Russian, and `SHA256SUMS.txt` lets you verify the download. All three
builds bundle the whisper engine — no separate install. The Linux build is a
mic-only preview: Live system-audio capture is macOS-only for now.

To build from source instead, see [Building](#building).

---

## How it works

The window is a two-column workspace: a **Library** on the left (collapsible,
and auto-collapsing on narrow windows) and the current document on the right —
the start screen, a record, the cover workspace, or a run screen. The layout
is remembered between runs.

1. **Start screen.** Pick a source — file, folder, microphone, or Live — and a
   recipe, then launch with one button. Transcription parameters stay behind a
   one-line summary and a "Change" link.
2. **Run screen.** The recipe runs as one job: a row per step (transcribe,
   diarize, clean, article, insights, YouTube package, book, cover), live
   progress, an overall progress bar with elapsed time and an ETA once two
   steps have finished, and per-step retry/cancel that does not redo what
   already succeeded. A step whose inputs (transcript, model, prompt) have not
   changed is skipped from cache.
3. **Record.** When transcription finishes, the record opens with a player
   and tabs: Transcript, Cleaned text, Articles, YouTube, Book, Insights,
   Cut, and Chat. Covers open from a **Cover** button in the record header,
   **Go → Covers** (`Ctrl+4`), or the Library.

Other interface pieces:

- `Ctrl+K` opens a command palette. It searches transcript history and
  generated materials, lists every recipe ("Run: <recipe>") and the open run's
  failed/cancelled steps ("Restart: <step>"), and includes every menu action.
- A single status bar shows the current operation and its progress, cancel,
  a popover queue for batch jobs and Course Capture, and LM Studio/GPU status.
- Dark/light theme and a Russian/English interface. The language switches at
  runtime, without a restart.

---

## What the application can do

### Recipes

A recipe is a named list of steps chosen from `transcribe`, `diarize`,
`clean`, `article`, `insights`, `youtube_package`, `book`, and `cover`.
Five are built in:

| Recipe | Steps |
|---|---|
| Transcript only | transcribe |
| YouTube video | transcribe → clean → YouTube package → cover |
| Podcast article | transcribe → clean → article |
| Meeting notes | transcribe → diarize → insights |
| Book | transcribe → clean → book |

**Configure…** opens the recipe editor, where you can save your own recipes
(Save / Save as new / Delete). A custom recipe appears alongside the built-ins
on the start screen and as a Library filter, and can carry its own model,
language, translation, performance mode, and diarization settings; anything it
does not set falls back to the global defaults.

Runs are stored per record. The Library card shows a run's failed steps
without opening the record, and a run that failed, was cancelled, or was
interrupted by closing the app can be resumed with **Continue** — steps that
already succeeded are not repeated.

Transcription and the history save always run. Video editing operations
(Cut) stay manual.

The **Folder** source queues files for transcription only; the selected
recipe's other steps are not applied to queued files.

### Transcription

- audio: MP3, WAV, FLAC, M4A, OGG, OPUS, WMA, AAC;
- video: MP4, MKV, AVI, MOV, WebM, WMV, FLV, M4V;
- eight Whisper models — Tiny, Base, Small, Medium, Large v3, Turbo, and the
  Turbo Q5 / Q8 quantizations — downloaded on demand, with a badge in the
  model list showing which are already on disk;
- language auto-detection, 19 selectable languages, and translation to English;
- Efficiency / Balanced / Performance profiles and CPU / Metal / CUDA / ROCm
  acceleration;
- custom vocabulary passed to Whisper as an initial prompt;
- hard cancellation: transcription runs in a child process that can be
  terminated without closing the application.

`ffmpeg` and `ffprobe` are required for media conversion, duration probing,
cover frames, and video editing.

### Speakers

Optional `pyannote.audio` diarization adds speaker labels. Speakers can be
renamed or merged; names are saved in history and used by exporters. Names you
have typed before are offered again as suggestions when renaming speakers in
other records. This is a hint list, not speaker recognition — the same
"SPEAKER_00" in two files is unrelated.

### Library, queue, and recorder

- SQLite history with FTS5 full-text search and a `LIKE` fallback;
- search scope toggle: **Transcripts**, **Materials** (text of generated
  articles, insights, YouTube packages, and books), or **Everything**; a
  materials hit opens the record on the tab that produced it. Records made
  before this feature can be backfilled with **Reindex materials** in Settings;
- filters by source kind and by recipe, with a reset;
- reopening saved segments, metadata, and speaker names;
- batch queue: drop several files (or a folder — expanded one level deep) on
  the window, or add files in the queue panel; they are transcribed
  sequentially and saved to history;
- **watch folder** (Settings → General, off by default): new supported files
  appearing in a local folder join the queue automatically once they stop
  growing;
- microphone recording with device selection, level meter, pause, and resume.

History stores transcripts, run state, and the searchable text of generated
materials, but it does not restore the content of previously generated AI
artifacts into their panels by itself. A recipe's output is saved as separate
files in the application data directory, each with a provenance manifest
(`<file>.manifest.json`) recording the transcript revision, model, and prompt
version that produced it.

### Transcript workspace

- built-in audio/video player with click-to-seek transcript segments;
- segment editing that preserves timestamps, plus find, replace, and copy;
- **Versions**: every edit is saved as a non-destructive revision (the last 20
  are kept by default); compare any two with a highlighted diff or restore one;
- 9 export formats: TXT, timestamped TXT, SRT, VTT, JSON, Markdown, HTML,
  DOCX, and PDF;
- **export presets** that write formats and already-generated materials into
  one folder with an `index.txt`: *YouTube* (SRT + VTT + YouTube package +
  cover), *Article draft* (Markdown + DOCX + article), and *Archive* (every
  format and every material). A material the record never generated is
  reported, not treated as an error.

### AI tools

The following features require LM Studio with a loaded chat model:

- filler removal and spoken-to-written coherence cleanup;
- five content formats: blog post, FAQ, listicle, executive summary, and social
  posts;
- transcript chat with streamed responses;
- insights: chapters, action items, and key moments;
- book pipeline: spoken-text unwrapping, an optional custom prompt, and batch
  processing of Markdown files.

The YouTube package contains timestamped chapters, title options, a
description, tags, and timestamped key questions.

Only the YouTube package can use either LM Studio, an OpenAI-compatible API, or
Anthropic with the user's key. All other AI tools currently use LM Studio.

The task templates are editable Markdown files in `prompts/`.

### Cover generator

Covers create Prosvet YouTube artwork from the bundled declarative template.
The workspace supports duo, solo, and text-only layouts, mint/warm variants,
live preview, title suggestions from the open transcript, and reproducible
PNG/JPEG exports with a `.cover.json` sidecar. The original Templegarten face
is replaced by bundled OFL-licensed Bellota Bold, with a generic system-font
fallback. Shorts export stays opt-in until the authored 9:16 adaptation
receives brand approval.

Portraits can be chosen from PNG/JPEG files, or — when the open record is a
video — pulled from it: **Frame from video** opens a dialog with a timestamp
field (prefilled from the player position) and a gallery of candidate frames,
extracted with `ffmpeg` in the background. Each photo slot also has a focal-point
(crop) selector.

Zoom-tile detection, ONNX face restoration, and the localhost ComfyUI adapter
exist as experimental core modules and are not yet wired into the workspace.

### Editing

The Cut tab lets you choose kept segments, automatically deselect pauses, seek
through the source, export a CMX3600 EDL, and assemble a draft MP4 with
`ffmpeg`. Draft assembly uses per-segment re-encoding by default for more
accurate cut boundaries.

### Live transcription and Course Capture (experimental)

Live is enabled manually in Settings and is then started from the start
screen's source picker. It includes:

- microphone and system-audio sources;
- asynchronous preflight checks;
- an incremental transcript, pause/resume, and diagnostics;
- saving finalized text segments to the Library during a session;
- application discovery and system-audio capture through a separate
  ScreenCaptureKit helper.

**Course Capture** builds on the same pipeline: queue named lessons, play each
in a browser or other app you are watching, capture its system audio, and each
finished lesson is saved to the Library as a normal record. It appears as a tab
in the status-bar queue popover whenever Live is enabled. It captures audio
only and does not download or touch the source site.

Live does not write WAV, M4A, or temporary PCM files: audio exists only in
bounded in-memory buffers needed for current recognition. The Library keeps
the transcript and exports, but no player or media-dependent operations are
available for such a session.

System-audio capture requires macOS 13+, a built Swift helper, and Screen
Recording permission. Live is disabled by default and has not yet passed the
full release soak gate.

### API-key storage

When the optional `keyring` package and a working OS backend are available,
cloud-provider API keys and the Hugging Face token are stored in macOS
Keychain, Windows Credential Manager, or a Linux Secret Service. Otherwise
Whispered falls back to its owner-only local configuration file. Use a
dedicated, least-privilege key and protect the user account accordingly.

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

`build.py` creates `dist/Whispered.app`. In a dev build the native whisper
stack and model weights stay outside the app bundle, so they can be updated
without rebuilding the application. For a self-contained build to hand to
someone else, add `--bundle-libs` (this is what the release workflow uses):

```bash
.venv/bin/python build.py --bundle-libs --no-libs
```

Tagged releases are produced by the manual **Release** workflow
(`.github/workflows/release.yml`) — run it from the Actions tab with a
`version` input; it builds macOS, Windows and Linux, attaches all three plus
`SHA256SUMS.txt`, and leaves a draft release for review. Release notes live
in `docs/release-notes/` (`TEMPLATE.md` for new versions).

Windows and Linux packaging are also runnable locally as unsigned previews:

```powershell
.\packaging\windows\build-windows.ps1
```

```bash
./packaging/linux/build-linux.sh
```

The Windows script produces an `onedir` package and ZIP (Inno Setup installer
and code signing stay release-gated; see `packaging/windows/README.md`). The
Linux script produces a PyInstaller `onedir` tree and `.tar.gz`; see
`packaging/linux/README.md`. The older `appimage/build-appimage.sh` is a
separate experiment and not a validated release channel.

---

## Development and verification

```bash
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/ -q
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests_qt/ -q
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/render_ui_gallery.py --check
```

The unit suite (`tests/`, run against PyQt6 stubs) and a separate
offscreen-Qt smoke suite (`tests_qt/`, real Qt) both pass. CI runs tests and
blocking `ruff` checks on Linux (Python 3.11), and builds an unsigned Windows
package with a frozen smoke test on every push. `mypy` is blocking for the
module set listed in [CLAUDE.md](CLAUDE.md) and informational for `ui/`.

The code is layered — `domain/` (Qt-free DTOs), `application/` (job engine,
step registry, export controller), `infrastructure/` (artifact manifests),
`core/` (workers, history, LLM clients), `ui/` — see the architecture map in
[CLAUDE.md](CLAUDE.md).

The AI task templates live in 18 Markdown files under `prompts/`. Some
modules also keep embedded fallback text for resilience.

See [TESTING.md](TESTING.md), [ROADMAP.md](ROADMAP.md), and
[CLAUDE.md](CLAUDE.md) for more detail.

---

## Known limitations

- Windows has source, packaging, and CI-preview support, but is not yet a
  validated release platform. Its real-hardware, clean-VM, and signing gates
  remain open.
- Diarization requires separate heavyweight dependencies, a Hugging Face
  token, and accepted model terms. Speaker-name suggestions are not automatic
  speaker recognition.
- Local LM Studio calls are serialized process-wide. Long generations can
  still delay cancellation while the current HTTP response is being read.
- AI requests use a configured context cap; for long recordings, chapters and
  insights are sampled evenly across the recording.
- Cloud providers apply only to the YouTube package.
- Files queued from a folder, a multi-file drop, or the watch folder are
  transcribed and saved to history only; recipe steps are not run per file.
- Zoom-tile detection, ONNX restoration, and ComfyUI for covers are not
  connected to the UI. Zoom multitrack (per-participant audio) transcript
  merging exists only as engine modules with no UI entry point.
- Live and Course Capture system-audio capture is macOS-only and remains
  experimental.

---

## License

[MIT](LICENSE)
