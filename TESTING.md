# Whispered — Testing Checklist

Pre-release checklist for local testing before building with PyInstaller.

---

## 1. Prerequisites

- [ ] Python 3.10+ virtual environment activated
- [ ] `pip install -r requirements.txt` succeeds without errors
- [ ] `ffmpeg` is installed: `ffmpeg -version`
- [ ] LM Studio is running (optional, for AI features)

## 2. Application Launch

```bash
source .venv/bin/activate
python main.py
```

- [ ] Application window opens without any exceptions in the terminal
- [ ] Dark theme renders correctly (no white flashes)
- [ ] Log file is created:
  - macOS: `~/Library/Application Support/Whispered/logs/app.log`
  - Linux: `~/.local/share/Whispered/logs/app.log`

## 3. Transcription

- [ ] **WAV file**: Select a `.wav` file → Transcribe → Text appears in the panel
- [ ] **Non-WAV file** (e.g. `.mp4`, `.m4a`): Verify FFmpeg converts automatically
- [ ] **FFmpeg missing**: Rename `ffmpeg` temporarily → Select an `.mp4` → Should show clear error with install instructions
- [ ] **Cancel**: Start a long transcription → Cancel → UI resets without crash
- [ ] **Model download**: Delete models directory → Start transcription → Model downloads with progress bar
  - macOS: `~/Library/Application Support/Whispered/models/`
  - Linux: `~/.local/share/Whispered/models/`

### L1 batch baseline (before any live work)

- [ ] Run `python -m pytest tests/ -q` and save the passing test count.
- [ ] Transcribe the consented ten-minute fixture named in
  `tests/fixtures/README.md` with the release candidate model and settings.
- [ ] Verify the resulting transcript and every export still use the stable
  `Segment(start, end, text, speaker, words)` / `TranscriptionResult` contract.
- [ ] Record elapsed time, model, machine profile, WER (when a reference is
  available), and any failures in the release task. Do not commit private
  recordings or their transcript.

## 4. Speaker Diarization

- [ ] **Enable diarization** with valid HF token → Speaker labels appear in output
- [ ] **Invalid HF token**: Set a garbage token → Should show "Invalid Hugging Face token" error
- [ ] **No token**: Leave HF token blank → Diarization silently skipped

## 5. AI Processing (requires LM Studio)

- [ ] **Text cleaning**: After transcription → "Clean Text" → Cleaned text appears
- [ ] **Article generation**: "Generate" → One article renders in the Article tab
- [ ] **Generate all formats**: "Generate All" → 5 articles appear (Blog, FAQ, Listicle, Summary, Social)
- [ ] **LM Studio offline**: Stop LM Studio → Attempt AI ops → Graceful fallback, no crash

## 6. Export

- [ ] **TXT export**: Files → Export → Plain Text → File created correctly
- [ ] **SRT export**: Subtitles with timecodes
- [ ] **JSON export**: Valid JSON structure with segments
- [ ] **Copy to clipboard**: Copy button → Paste elsewhere → Content matches

## 7. Batch Processing

- [ ] Add 2+ files to batch → Start → All files process in sequence
- [ ] Cancel mid-batch → Remaining files marked as cancelled

## 8. Build

```bash
python build.py
```

- [ ] Build completes without errors
- [ ] Application launches successfully:
  - macOS: `dist/Whispered.app`
  - Linux: `dist/Whispered/Whispered`
- [ ] Bundle size is < 100 MB (no model weights bundled)
- [ ] Models download on first run from the built app

## 9. Live transcription (experimental opt-in)

- [ ] Enable Live in Settings; the Live sidebar item appears without restart.
- [ ] Start Zoom, open Live, refresh targets, and verify the running Zoom
  process is selected ahead of other shareable applications.
- [ ] Run preflight for microphone, meeting audio, and both sources. Changing
  any setup value invalidates the result; warnings permit Start and failures do not.
- [ ] Start a 15-minute session from the UI; verify meters, elapsed time,
  incremental partial revisions, immutable finals, overlap/ambiguity labels,
  Pause/Resume and the `starting → running → finalizing → completed` states.
- [ ] Open Diagnostics and verify profile, per-source backlog/drops, ASR and
  clock data. Copied diagnostics must contain no transcript, paths, prompts,
  audio, API keys, or tokens.
- [ ] After Stop, verify the record appears in Library, survives restart, and
  the completed-state button opens the normal Record view.
- [ ] Export an overlapping section to all nine formats. SRT/VTT preserve
  simultaneous cues; JSON declares `overlap_policy: preserve`.
- [ ] Run the existing content preset on the live record.
- [ ] Revoke permission/kill the helper: the failed source is visible and the
  surviving source continues.
- [ ] Record 16/24-GB profile, drops, partial/final p95, drift and Stop time.

### UI release gate

```bash
ruff check .
python -m pytest tests/ -q
python -m compileall -q . -x '.venv|.claude|build|dist|docs/archive'
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/render_ui_gallery.py --check
```

### Real-Qt regression suite

The ordinary unit-test suite deliberately uses PyQt stubs. Run this separate
suite with the project virtualenv so lifecycle, queued signals and widget
state are exercised by a real Qt runtime:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests_qt/ -q
```

CI runs the same suite in the `qt-smoke` job (ubuntu, offscreen) using
`requirements-qt-ci.txt` — the pinned UI dependencies without the lazily
imported engines (pywhispercpp, sounddevice), which have no Linux wheel and
are not needed to construct the UI.

- [ ] Review RU/EN × dark/light at 1100×700 and 1440×900.
- [ ] Repeat the primary flows at the supported minimum 900×550 using only
  the keyboard; focus must remain visible and every primary action reachable.
- [ ] Close the window during Queue, Recorder, preset, and every active Live
  state. Workers must cancel or finish cleanly without a hung process.
- [ ] On macOS 26.5.1 / M4 Pro / 24 GB complete the 15-minute Zoom walkthrough
  above before changing Live from opt-in to enabled by default.

---

## Windows preview gate

Windows is not a released platform until every item in
[docs/WINDOWS_SUPPORT_PLAN.ru.md](docs/WINDOWS_SUPPORT_PLAN.ru.md) section 12
has been completed on Windows 11 x64. The current Windows CI job packages an
unsigned test artifact; it does not replace a clean-VM installer check, a real
transcription/cancel run, microphone verification, or code-signing validation.

```powershell
.\setup-windows.ps1
.\packaging\windows\build-windows.ps1
```

- [ ] Run `Whispered.exe --smoke-test` from the frozen package.
- [ ] Run the full manual Windows preview gate from the Windows support plan.
- [ ] Verify the installer in a clean VM before publishing any Windows build.
