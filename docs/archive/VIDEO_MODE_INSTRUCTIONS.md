# Video Mode — Implementation Instructions (for Sonnet)

> Hand-off spec for adding a **video editing workflow** to Whispered.
> This document is **self-contained**: you can implement from it without prior context.
> Read it fully before writing code. Implement **only Iteration 1** ((1)+(2)+(4)),
> show diffs, and stop for review. Later iterations are sketched at the end.

---

## 0. Orientation (read first)

**Repository layout.** The git repo root is `/Users/den/Whispered/Whispered/`
(note the doubled path — there is an outer `/Users/den/Whispered/` wrapper folder).
All paths below are relative to the inner project root unless stated otherwise.

**What Whispered is.** A local, privacy-first PyQt6 desktop app: audio/video →
transcript (whisper.cpp via `pywhispercpp`) → optional LLM post-processing
(LM Studio, OpenAI-compatible API on `localhost:1234`). Two existing modes:
`posts` and `book` (see `config.pipeline_mode`). Everything runs offline; **no paid
APIs**, no new pip dependencies for this work (ffmpeg is a system binary).

**Python / venv.** Use the project venv: `/Users/den/Whispered/Whispered/.venv/bin/python`.
Run tests with `.venv/bin/python -m pytest`.

**Target platform.** macOS, Apple Silicon (M4 Pro). DaVinci Resolve (free) is the
manual editor; our job is to hand Resolve a rough-cut timeline (EDL).

### Hard constraints — do not violate
1. **Do not break `posts` or `book` modes.** Video is an additional, explicitly
   switched mode. Every new code path must be gated behind `word_timestamps=True`
   or `pipeline_mode == "video"`. Defaults must preserve current behavior exactly.
2. **Reuse existing modules.** Do not duplicate transcription, ffmpeg, LLM-client,
   exporter, or UI infrastructure. Specific reuse points are called out below.
3. **Match the existing code style.** Dataclasses for data, type hints, module
   docstrings, `from core.logger import get_logger` / `logger = get_logger(__name__)`,
   `core.i18n.tr(...)` for user-facing strings where the surrounding code does.
4. **Friendly errors** when ffmpeg/ffprobe is missing (suggest `brew install ffmpeg`).
5. **Tests** for the timecode conversion and EDL generation, in the style of
   `tests/test_exporters.py` (no Qt, no whisper, no GPU at import time).

### Decisions already made (do not re-litigate)
- **UI:** add a new `"video"` entry to the mode switcher + a new right-hand tab
  **"Timeline / Cut"**. Do not build a separate window.
- **Data model:** word timings live on **each segment** as `seg.words`
  (a `list[Word]`), not as a separate top-level `result.words`.
- **Scope of Iteration 1:** only requirements **(1) audio/ffmpeg readiness +
  (2) word-level timestamps + (4) EDL exporter**, wired end-to-end through a
  minimal UI so the vertical slice "video → word-timed transcript → EDL" works.

---

## 1. Key facts about the current codebase

### 1.1 Transcription (`transcriber.py`)
- Public entry: `Transcriber.transcribe(filepath, model_name, language='auto',
  translate=False, n_threads=4, enable_diarization=False, num_speakers=None,
  initial_prompt=None, on_progress, on_finished, on_error)`.
- It creates a `TranscriptionWorker(QThread)`, which spawns a **child process**
  (`mp.get_context('spawn')`) running `_run_transcription_process(...)`. The child
  talks back over an `mp.Queue` with tuples: `('progress', pct, msg)`,
  `('result', TranscriptionResult)`, `('error', msg)`.
  **Any new argument must be threaded through all three layers** (Transcriber →
  TranscriptionWorker.__init__ + run() args tuple → _run_transcription_process
  signature + the positional `args=(...)` tuple in `TranscriptionWorker.run`).
- ffmpeg extraction **already exists**: `_convert_to_wav(input_path)` produces a
  16 kHz mono PCM wav in tmp and is invoked automatically for video extensions
  (see `FORMATS_NEEDING_CONVERSION`, which already contains `.mp4/.mov/.mkv/...`).
  A friendly "FFmpeg is not installed / `brew install ffmpeg`" error is already
  emitted there. **Do not re-extract audio yourself.**
- The actual decode call is:
  ```python
  segments_raw = model.transcribe(audio_path, new_segment_callback=segment_cb, **params)
  ```
  and results are converted with `seg.t0 / 100.0`, `seg.t1 / 100.0` (centiseconds → s).

### 1.2 Segment data model (`transcriber.py`)
```python
@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: Optional[str] = None

@dataclass
class TranscriptionResult:
    segments: List[Segment]
    language: str
    duration: float
    speaker_names: Dict[str, str] = field(default_factory=dict)
    # full_text property joins segment texts
```
History (`core/history.py`) serializes segments **field-by-field**
(`start/end/text/speaker`), so adding an optional `words` field is backward-safe;
words simply won't persist to history. That's acceptable.

### 1.3 pywhispercpp word-level timestamps — VERIFIED
The `pywhispercpp` `Segment` only exposes `t0`, `t1`, `text`, `probability`. There
is **no nested word list**. To get word-level timing, pass these decode params
(all confirmed present in `pywhispercpp.constants.PARAMS_SCHEMA`):
```python
params['token_timestamps'] = True
params['split_on_word']    = True
params['max_len']          = 1     # force ~one word per emitted segment
```
With `max_len=1`, whisper emits **one Segment per word** (each with its own
`t0/t1`). You then **regroup those word-segments into phrase segments yourself**
(see §2.2). Note: pywhispercpp's `transcribe` persists overridden params on the
model instance, but each job runs in a fresh process with a fresh `Model`, so
there is no cross-run leakage — no special reset needed.

### 1.4 LLM layer (for later iterations, know it exists)
- `core/lm_client.py` → `LMStudioClient` (HTTP; `complete`/`chat_completion`/
  `chat_completion_stream`). **This is what actually talks to the model.**
- `lm_studio_manager.py` → server/model control via `lms` CLI (start/load). Not chat.
- `core/prompts.py` → `load_prompt(name, fallback)` reads `prompts/<name>.md`.
- `core/insights_worker.py` → `InsightsWorker` (`chapters|action_items|key_moments`),
  builds a timestamped prompt and parses a JSON array. `core/youtube_description.py`
  + `ui/youtube_panel.py` already render chapter timecodes. **Chapters already work.**

### 1.5 Exporters (`exporters.py`)
- `EXPORT_FORMATS: dict[key] = (label, func)`; `export_result(result, path, key)`.
- Timecode helpers live in `utils.py`: `format_timestamp_srt` (`HH:MM:SS,mmm`),
  `format_timestamp_vtt` (`HH:MM:SS.mmm`). These are **wall-clock**, not frame-based;
  EDL needs frame-based `HH:MM:SS:FF` — that's a new helper (§3), do not reuse these.

### 1.6 UI structure (`ui/main_window.py`)
- Mode switcher: `self.mode_combo` with items `("posts")`, `("book")`, switched by
  `_on_mode_changed(index)` which just shows/hides left-hand panels
  (`ai_panel`, `batch_panel`, `book_panel`). Saved/restored via
  `config.pipeline_mode` in `closeEvent` / `__init__`.
- Right side: `self.content_tabs` (QTabWidget) with tabs Transcript, Cleaned,
  Articles, Chat, Insights, YouTube, History — all created in `_setup_ui`.
- `_start_transcription()` reads header controls and calls `self.transcriber.transcribe(...)`.
  `_on_finished(result)` distributes the result to the panels and switches to the
  Transcript tab. `self._source_filepath` holds the **original** media path (needed
  later for ffmpeg cutting — keep it).
- `ui/transcript_view.py` already has an edit mode (one line per segment) and
  player-sync highlighting. Use it as the **style reference** for the new cut view,
  but do not modify it in Iteration 1.

---

## 2. Iteration 1 — implementation steps

Implement in this order. Commit nothing; just show diffs and stop.

### 2.1 `config.py` — add video settings
Add three fields to the `Config` dataclass (keep them next to the book settings,
matching the existing comment style):
```python
# Video pipeline settings
video_fps: int = 30            # 24 | 25 | 30 | 60
video_drop_frame: bool = False # DF timecode (only meaningful for 30/60 == 29.97/59.94)
```
`pipeline_mode` already accepts arbitrary strings; we'll add `"video"` in the UI.
The `Config.load()` field-filtering already ignores unknown keys, so this is safe.

### 2.2 `transcriber.py` — word timestamps (gated, default off)

**(a) New `Word` dataclass** next to `Segment`:
```python
@dataclass
class Word:
    start: float
    end: float
    text: str
```

**(b) Extend `Segment`** with an optional words list (backward-safe default):
```python
@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    words: List[Word] = field(default_factory=list)
```
(`field` is already imported from dataclasses.)

**(c) Thread a `word_timestamps: bool = False` flag** through every layer:
- `Transcriber.transcribe(...)` — add the kwarg, pass it into `TranscriptionWorker`.
- `TranscriptionWorker.__init__(...)` — store `self.word_timestamps`.
- `TranscriptionWorker.run()` — add it to the `args=(...)` tuple (keep positional
  order consistent with the function signature; it's safest to add it **after**
  `initial_prompt`, the current last arg).
- `_run_transcription_process(..., initial_prompt=None, word_timestamps=False)` —
  add the parameter.

**(d) In `_run_transcription_process`, when `word_timestamps` is True**, before the
`model.transcribe(...)` call add:
```python
if word_timestamps:
    params['token_timestamps'] = True
    params['split_on_word'] = True
    params['max_len'] = 1
```
Then change the result-building section. Currently it builds one `Segment` per
`seg` in `segments_raw`. When `word_timestamps` is on, `segments_raw` is **word-level**;
build a `Word` list and regroup into phrase segments:

```python
if word_timestamps:
    words = [Word(start=s.t0/100.0, end=s.t1/100.0, text=s.text) for s in segments_raw]
    segments = _group_words_into_segments(words)
else:
    segments = []
    for seg in segments_raw:
        segments.append(Segment(start=seg.t0/100.0, end=seg.t1/100.0, text=seg.text, speaker=None))
```

**(e) Add the grouping helper** `_group_words_into_segments(words)` (module-level
function near `_build_initial_prompt`). Rules:
- Accumulate words into a phrase. Start a **new** phrase when any of:
  - the previous word's text ends with sentence punctuation `.?!…` (after strip), **or**
  - the gap `word.start - prev_word.end` exceeds `_PHRASE_PAUSE_GAP` (use `0.6` seconds), **or**
  - the accumulated phrase already has `>= _PHRASE_MAX_WORDS` words (use `14`).
- Each emitted `Segment`: `start = words[0].start`, `end = words[-1].end`,
  `text = "".join(w.text for w in phrase).strip()` (whisper word text usually
  carries a leading space — join then strip, collapse double spaces), `speaker=None`,
  `words=phrase`.
- Define the two thresholds as module constants with a short comment.
- Edge cases: empty input → `[]`; a single word → one segment.

Diarization currently runs after segment building and assigns speakers by midpoint;
it works on phrase segments unchanged. Leave that block as-is (it's already guarded
by `enable_diarization`). Video mode will pass `enable_diarization=False` anyway.

The progress callback `segment_cb` uses `seg.t1/100.0`; with `max_len=1` it just
fires per word — harmless, leave it.

### 2.3 `video_input.py` — NEW module (ffmpeg/ffprobe readiness + probe)
Small, dependency-free, importable without Qt. Provide:
```python
"""Whispered - Video Input helpers: ffmpeg/ffprobe checks and media probing."""
import shutil, subprocess, json, platform
from core.logger import get_logger
logger = get_logger(__name__)

class FFmpegNotFoundError(RuntimeError): ...

def _install_hint() -> str:
    if platform.system() == "Darwin":
        return "brew install ffmpeg"
    if shutil.which("dnf"):
        return "sudo dnf install ffmpeg"
    return "sudo apt install ffmpeg"

def ensure_ffmpeg() -> None:
    """Raise FFmpegNotFoundError with an install hint if ffmpeg is missing."""
    if not shutil.which("ffmpeg"):
        raise FFmpegNotFoundError(
            f"FFmpeg is not installed.\n\nInstall it with:\n  {_install_hint()}"
        )

def ensure_ffprobe() -> None:
    if not shutil.which("ffprobe"):
        raise FFmpegNotFoundError(
            f"ffprobe is not installed (part of FFmpeg).\n\nInstall it with:\n  {_install_hint()}"
        )

def probe_video(path: str) -> tuple[float, float]:
    """Return (fps, duration_seconds) for a video file via ffprobe.

    fps is parsed from the video stream's r_frame_rate ("30000/1001" → 29.97).
    Falls back to (30.0, 0.0) on any parsing failure (never raises except when
    ffprobe itself is missing).
    """
    ensure_ffprobe()
    # ffprobe -v quiet -print_format json -show_streams -show_format <path>
    # parse first stream with codec_type == "video": r_frame_rate "num/den";
    # duration from format.duration (fallback stream.duration).
    ...
```
Keep `probe_video` defensive: wrap subprocess/JSON parsing in try/except, log a
warning and return sane fallbacks. Use `subprocess.run([...], capture_output=True,
text=True, timeout=30)`.

> Note: We do **not** call `ensure_ffmpeg()` from the transcription path —
> `transcriber._convert_to_wav` already handles that. `video_input` is for the
> UI to (a) fail fast with a clear message when entering video mode on a machine
> without ffmpeg, and (b) auto-detect fps to pre-fill the EDL framerate.

### 2.4 `timeline_export.py` — NEW module (the core deliverable)
Pure functions, no Qt, no I/O except the optional `write_edl` convenience.
Duck-type the segments (anything with `.start`/`.end`, like `tests/test_exporters.py`
does) so tests don't need the real `Segment`.

**Public API:**
```python
def seconds_to_timecode(seconds: float, fps: int, drop_frame: bool = False) -> str: ...
def build_edl(segments, fps: int = 30, drop_frame: bool = False,
              title: str = "Whispered Timeline", clip_name: str = "") -> str: ...
def write_edl(segments, filepath: str, **kwargs) -> None: ...   # thin wrapper
```

**`seconds_to_timecode` — non-drop (the must-have path):**
```python
fps_int = int(round(fps))                 # 24/25/30/60
total_frames = int(round(seconds * fps_int))
f = total_frames % fps_int
total_seconds = total_frames // fps_int
s = total_seconds % 60
m = (total_seconds // 60) % 60
h = total_seconds // 3600
return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"
```

**`seconds_to_timecode` — drop-frame (only for fps 30 or 60; secondary, verify):**
Use the standard drop-frame algorithm and a `;` before the frames field
(`HH:MM:SS;FF`). Reference implementation to adapt:
```python
nominal = 30 if int(round(fps)) == 30 else 60     # DF only valid here
real_fps = nominal * 1000.0 / 1001.0              # 29.97 / 59.94
drop = 2 if nominal == 30 else 4                  # frames dropped per minute
frame_number = int(round(seconds * real_fps))
fpm = nominal * 60                                # frames per nominal minute
fp10m = nominal * 600 - drop * 9                  # frames per 10 DF minutes
d, mrem = divmod(frame_number, fp10m)
add = drop * 9 * d
if mrem > drop:
    add += drop * ((mrem - drop) // (fpm - drop))
frame_number += add
f = frame_number % nominal
s = (frame_number // nominal) % 60
m = (frame_number // (nominal * 60)) % 60
h = frame_number // (nominal * 3600)
return f"{h:02d}:{m:02d}:{s:02d};{f:02d}"
```
If `drop_frame=True` but fps is not 30/60, ignore drop_frame (fall back to non-drop)
and log a warning — DF is undefined for 24/25.

**`build_edl` — CMX3600.** Produce text Resolve imports as a draft timeline:
```
TITLE: <title>
FCM: NON-DROP FRAME            (or "FCM: DROP FRAME")

001  AX       AA/V  C        <src_in> <src_out> <rec_in> <rec_out>
* FROM CLIP NAME: <clip_name>
002  AX       AA/V  C        ...
* FROM CLIP NAME: <clip_name>
```
Rules:
- Header `TITLE:` then `FCM: NON-DROP FRAME` / `FCM: DROP FRAME`, then a blank line.
- One event per kept segment, numbered `001`, `002`, … (3-digit, 1-based).
- Reel `AX`, channels `AA/V` (audio+video), transition `C` (cut).
- **Source** timecodes from the original media: `src_in = seconds_to_timecode(seg.start, ...)`,
  `src_out = seconds_to_timecode(seg.end, ...)`.
- **Record** timecodes are sequential (the rough cut): keep a running
  `rec_seconds` starting at `0.0`; `rec_in = tc(rec_seconds)`,
  `rec_out = tc(rec_seconds + (seg.end - seg.start))`; then
  `rec_seconds += (seg.end - seg.start)`.
- Emit a `* FROM CLIP NAME:` comment line under each event when `clip_name` is set.
- Skip zero/negative-length segments. Return `""` for empty input.
- Column spacing: match the example above (it's whitespace-tolerant in Resolve, but
  keep it readable and consistent). End the file with a trailing newline.

`write_edl(segments, filepath, **kwargs)` = open utf-8, write `build_edl(...)`.

### 2.5 `tests/test_timeline_export.py` — NEW (required)
Mirror `tests/test_exporters.py` conventions: a local `_Seg` dataclass with
`start/end`, no Qt/whisper imports. Cover:
- **Timecode, non-drop:**
  - `seconds_to_timecode(0.0, 30) == "00:00:00:00"`
  - `seconds_to_timecode(1.0, 30) == "00:00:01:00"`
  - `seconds_to_timecode(1.5, 30) == "00:00:01:15"`
  - `seconds_to_timecode(3661.0, 30) == "01:01:01:00"`
  - `seconds_to_timecode(1.0, 25) == "00:00:01:00"` and `seconds_to_timecode(0.04, 25) == "00:00:00:01"`
  - `seconds_to_timecode(1.0, 24) == "00:00:01:00"`
  - `seconds_to_timecode(0.5, 60) == "00:00:00:30"`
- **Timecode, drop-frame (safe assertions):**
  - `seconds_to_timecode(0.0, 30, drop_frame=True) == "00:00:00;00"`
  - `seconds_to_timecode(600.0, 30, drop_frame=True) == "00:10:00;00"`
  - `drop_frame=True` with fps 25 falls back to non-drop format (contains `:`, not `;`).
- **EDL:**
  - Empty segments → `""`.
  - Two segments produce a string starting with `TITLE:`, containing
    `FCM: NON-DROP FRAME`, two numbered events `001`/`002`, and correct sequential
    record timecodes (event 1 rec_in `00:00:00:00`; event 2 rec_in equals event 1's
    duration). Assert source timecodes match the segment start/end.
  - `drop_frame=True` puts `FCM: DROP FRAME` in the header.

Run: `.venv/bin/python -m pytest tests/test_timeline_export.py -q` — must pass.

### 2.6 UI wiring (minimal)

**`ui/cut_view.py` — NEW.** A right-hand tab widget showing kept/cut segments.
- Header row styled like `transcript_view` (title + buttons). Provide a scrollable
  list (e.g. `QListWidget` with `QListWidgetItem` carrying a checkbox via
  `Qt.ItemFlag.ItemIsUserCheckable`, **checked = keep**). Each row label:
  `f"[{format_timestamp_vtt(seg.start)} → {format_timestamp_vtt(seg.end)}]  {seg.text}"`.
- Public methods:
  - `set_result(result)` — populate rows, all checked by default; store `result`.
  - `get_kept_segments() -> list[Segment]` — return segments whose row is checked,
    in order.
  - `clear()`.
- Add a "Select all / none" convenience and a count label ("N of M kept"). Keep it
  simple and consistent with the dark theme (reuse existing stylesheet patterns;
  don't invent new colors beyond what `transcript_view`/`youtube_panel` use).
- Optional: clicking a row emits `seek_requested(float)` like `transcript_view`
  (nice-to-have; only if cheap).

**`ui/video_panel.py` — NEW** (left-hand panel, modeled on `book_panel`).
- An fps `QComboBox` (items 24/25/30/60, data = int), seeded from `config.video_fps`.
- A "Drop frame" `QCheckBox`, seeded from `config.video_drop_frame`, enabled only
  when fps is 30 or 60 (disable + uncheck otherwise; wire to the combo).
- An "Export EDL…" `QPushButton` (`variant="primary"`), disabled until there's a
  transcript. Expose a signal `export_edl_requested = pyqtSignal()`.
- A `set_has_transcript(bool)` method (matches book_panel's pattern) to enable/disable.
- Persist fps/drop_frame back to config on change (`get_config(); cfg.video_fps=...; save_config()`),
  matching how other panels persist.

**`ui/main_window.py` — wire it up.**
- Add the video mode to the switcher (after the book item):
  ```python
  self.mode_combo.addItem(tr("label_mode_video"), "video")
  ```
  Add the i18n key (see §2.7).
- Instantiate `self.video_panel = VideoPanel()` in the left column near `book_panel`,
  and `self.cut_view = CutView()` as a new tab:
  ```python
  self.content_tabs.addTab(self.cut_view, tr("tab_cut"))
  ```
- In `_on_mode_changed`, extend the show/hide logic for three modes. Pattern:
  ```python
  mode = self.mode_combo.currentData()
  is_book  = mode == "book"
  is_video = mode == "video"
  self.ai_panel.setVisible(mode == "posts")
  self.batch_panel.setVisible(mode == "posts")
  self.book_panel.setVisible(is_book)
  self.video_panel.setVisible(is_video)
  ```
- In `_start_transcription`, compute and pass the flag (and force diarization off in
  video mode — speaker labels aren't used for cutting and slow things down):
  ```python
  is_video = self.mode_combo.currentData() == "video"
  # ... existing setup ...
  self.transcriber.transcribe(
      ...,
      enable_diarization=False if is_video else enable_diarization,
      word_timestamps=is_video,
      ...,
  )
  ```
  Also clear `self.cut_view.clear()` alongside the other `*.clear()` calls.
- In `_on_finished`, after the existing distribution, populate the cut view and
  jump to it in video mode:
  ```python
  self.video_panel.set_has_transcript(True)
  self.cut_view.set_result(result)
  if self.mode_combo.currentData() == "video":
      self.content_tabs.setCurrentWidget(self.cut_view)
  ```
- Connect `video_panel.export_edl_requested` to a new handler:
  ```python
  def _export_edl(self):
      from timeline_export import write_edl
      from video_input import ensure_ffmpeg  # not strictly needed for EDL; skip if unused
      segs = self.cut_view.get_kept_segments()
      if not segs:
          self.status_label.setText(tr("status_no_segments")); return
      cfg = get_config()
      src = self._source_filepath or "clip"
      clip_name = os.path.basename(src)
      default_name = os.path.splitext(clip_name)[0] + ".edl"
      filepath, _ = QFileDialog.getSaveFileName(self, "Export EDL", default_name,
                                                "EDL (*.edl);;All Files (*)")
      if not filepath:
          return
      try:
          write_edl(segs, filepath, fps=cfg.video_fps, drop_frame=cfg.video_drop_frame,
                    title=os.path.splitext(clip_name)[0], clip_name=clip_name)
          show_toast(self, tr("toast_edl_exported", name=os.path.basename(filepath)), kind="success")
      except Exception as e:
          QMessageBox.critical(self, tr("error_export"), str(e))
  ```
  (Drop the unused `ensure_ffmpeg` import — EDL generation doesn't need ffmpeg.)
- In `closeEvent`, the mode is already saved via `cfg.pipeline_mode = 'book' if ...`.
  Update that line to preserve `"video"` too, e.g.:
  ```python
  cfg.pipeline_mode = self.mode_combo.currentData()
  ```
  (Verify `__init__` restore logic handles a `"video"` value — it currently does
  `setCurrentIndex(1 if saved == 'book' else 0)`; change to look up the index by
  data so `"video"` restores correctly.)

### 2.7 i18n
Add keys to **both** locale files (`locales/en.json` and `locales/ru.json` — check
exact filenames under `locales/`; `core/i18n.py` + `tr()` is the accessor). Needed:
- `label_mode_video` → "Video" / "Видео"
- `tab_cut` → "Timeline / Cut" / "Таймлайн / Нарезка"
- `status_no_segments` → "No segments selected" / "Не выбрано ни одного сегмента"
- `toast_edl_exported` → "EDL exported: {name}" / "EDL сохранён: {name}"
- plus any button/label strings you introduce in `video_panel` / `cut_view`.
Follow the existing key naming and the `tr("key", arg=...)` formatting convention
already used (e.g. `tr("toast_exported_one", name=...)`).

### 2.8 requirements.txt
No new pip deps. Add a short comment noting that **video mode requires the system
`ffmpeg`/`ffprobe` binaries** (`brew install ffmpeg` on macOS), in the same comment
style as the existing notes.

### 2.9 README
Add a short "Video mode" section: switch the mode selector to **Video**, drop in an
`.mp4/.mov`, Transcribe (produces word-timed phrase segments), open the
**Timeline / Cut** tab, untick segments to drop, set fps / drop-frame in the Video
panel, click **Export EDL…**, then **File → Import → Timeline → Pre-conform / Import
AAF/EDL** in DaVinci Resolve and point it at the source clip.

---

## 3. Verification checklist (Iteration 1)
1. `.venv/bin/python -m pytest -q` — all tests pass, including the new file.
2. `.venv/bin/python -c "import transcriber, timeline_export, video_input"` — imports clean.
3. Existing modes unchanged: posts/book transcription still produce normal
   (non-word) segments — confirm `word_timestamps` defaults to `False` everywhere
   and the `args=(...)` tuple order in `TranscriptionWorker.run` matches the
   `_run_transcription_process` signature exactly (off-by-one here silently
   corrupts behavior — double-check it).
4. Manual (if a sample video + ffmpeg are available): video mode → transcribe →
   Cut tab lists phrase segments with checkboxes → Export EDL writes a file whose
   header is `TITLE:` / `FCM:` and whose events have monotonic record timecodes.
5. Show diffs grouped by file, with a one-line rationale each. **Stop for review.**

---

## 4. Later iterations (do NOT build yet — for context only)

- **(3) Richer cut UX:** bulk operations, keyboard shortcuts, live "kept duration"
  readout, optional pause-trim preview.
- **(5) Headless ffmpeg rough-cut** → new `video_cut.py`: cut kept segments from
  `self._source_filepath` and concat via the **concat demuxer** with `-c copy`;
  on stream-boundary failure, fall back to re-encode. Uses `video_input.ensure_ffmpeg`.
  Add an "Assemble draft MP4" button to `video_panel`.
- **(6) Auto-cleanup** → new `video_edit.py`: `mark_pauses(words, threshold)` using
  inter-word gaps (the `seg.words` we now store); optional filler/repetition pass via
  a new `prompts/fillers.md` + `LMStudioClient` (reuse the `InsightsWorker` pattern,
  do not re-implement LLM connection). Pre-marks rows as cut in `cut_view`.
- **(7) YouTube metadata** → extend insight types with `titles | description | tags`
  (new `prompts/*.md`); chapters already exist via `InsightsWorker("chapters")` +
  `core/youtube_description.format_youtube_description`. Surface in `youtube_panel`
  or a video-specific section.

Each later iteration is its own review cycle: implement, show diffs, stop.
