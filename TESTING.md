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

- [ ] Enable Live in Settings and restart; the Live sidebar item appears.
- [ ] Run preflight for microphone, meeting audio, and both sources.
- [ ] Start a 15-minute session from the UI; verify meters, elapsed time,
  lag/drops, partial revisions, immutable finals, Pause/Resume and Stop.
- [ ] After Stop, verify the record appears in Library, survives restart, and
  opens in the normal Record view with Microphone/Meeting audio labels.
- [ ] Export an overlapping section to all nine formats. SRT/VTT preserve
  simultaneous cues; JSON declares `overlap_policy: preserve`.
- [ ] Run the existing content preset on the live record.
- [ ] Revoke permission/kill the helper: the failed source is visible and the
  surviving source continues.
- [ ] Record 16/24-GB profile, drops, partial/final p95, drift and Stop time.
