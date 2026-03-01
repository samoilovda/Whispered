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
- [ ] Log file is created at `~/Library/Application Support/Whispered/logs/app.log`

## 3. Transcription

- [ ] **WAV file**: Select a `.wav` file → Transcribe → Text appears in the panel
- [ ] **Non-WAV file** (e.g. `.mp4`, `.m4a`): Verify FFmpeg converts automatically
- [ ] **FFmpeg missing**: Rename `ffmpeg` temporarily → Select an `.mp4` → Should show clear error with install instructions
- [ ] **Cancel**: Start a long transcription → Cancel → UI resets without crash
- [ ] **Model download**: Delete models from `~/Library/Application Support/Whispered/models/` → Start transcription → Model downloads with progress bar

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
- [ ] `dist/Whispered.app` launches successfully
- [ ] Bundle size is < 100 MB (no model weights bundled)
- [ ] Models download on first run from the built app
