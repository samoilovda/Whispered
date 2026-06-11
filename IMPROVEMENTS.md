# Whispered — Improvement Instructions for a Future Agent

This document is a work order. It lists **bugs**, **structural flaws**, **UI
improvements**, and **new feature ideas** for the Whispered desktop app
(PyQt6 + whisper.cpp + LM Studio). Each item is written so an implementing
agent can act on it directly.

**Conventions**
- Line numbers were accurate at the time of writing but may drift — always
  re-locate the code by searching for the quoted snippet before editing.
- Items tagged **[VERIFIED]** were confirmed by reading the source directly.
  Items tagged **[CHECK]** were reported by analysis and should be re-confirmed
  before changing.
- Work in priority order: **P0 (correctness/data-loss/privacy) → P1
  (reliability/UX) → P2 (structure) → P3 (features)**.
- Do not regress the project's core promise: **fully offline, privacy-first.**

---

## ✅ Implementation status (updated)

**P0–P2 are implemented** on branch `claude/upbeat-mayer-gi043l`:

- **P0.1–P0.4** done: untracked the committed transcripts (no history rewrite),
  fixed LM cancellation, bounded `QThread.wait()`, removed all bare `except:`.
- **P1.1–P1.8** done: `core/json_utils.extract_json` replaces fragile fence
  parsing; explicit/configurable LM timeouts; `ai_panel` connection probe,
  model load, and server start moved to QThread workers; `book_panel` timer
  cleanup; clear-queue confirmation; file validation; tooltips/empty states;
  safer system-probe / model-list parsing.
- **P2.1–P2.5** done: removed `pipeline._client` access + factored the
  cancellation guard; processors honor configured `lm_studio_url` /
  `lm_request_timeout`; batch worker poll has a timeout + liveness check;
  added a 28-test `pytest` suite, `pytest.ini`, GitHub Actions CI, and a
  SessionStart hook.

**Caveat:** PyQt6 is not installed in the dev container, so UI changes were
**syntax-checked and import-checked but not runtime-verified**. Run the app on a
machine with PyQt6 to confirm the threaded `ai_panel` paths and dialogs.

**Remaining:** P3 (UI rework) and P4 (features) are **not started** — scoped for
a follow-up.

---

## P0 — Correctness, data loss & privacy

### P0.1 [VERIFIED] Committed real transcript data in the repo
`video1131745498.md` (172 KB) and `video1131745498.txt` (68 KB) are tracked in
git. Untracked local transcripts (`Анд1.txt`, `Анд2.txt`, `ПВК Ив.txt`) also
sit in the working tree. For a privacy-first product, shipping real
transcripts in version control is a leak and bloats the repo.

**Do:**
- `git rm --cached video1131745498.md video1131745498.txt` and remove them from
  the tree (keep a tiny synthetic `samples/example.txt` if a fixture is needed).
- Add to `.gitignore`: `*.txt` and `*.md` are too broad (would hide docs), so
  instead ignore a dedicated working dir, e.g. `samples/private/` and
  `transcripts/`, and move ad-hoc transcripts there.
- If these transcripts are sensitive, advise the user (in the PR description)
  that history rewriting (`git filter-repo`) may be needed to fully purge them.
  **Do not rewrite history without explicit user approval.**

### P0.2 [VERIFIED] LM cancellation does not actually cancel
`core/lm_client.py:118-129`. When `is_cancelled()` becomes true, the code does
`return None` *inside* the `with ThreadPoolExecutor(...)` block. Exiting the
`with` calls `executor.__exit__` → `shutdown(wait=True)`, which **blocks until
the in-flight HTTP request finishes anyway.** So "cancel" still hangs for up to
`DEFAULT_TIMEOUT` (300 s). Also, returning the exception as a value
(`return e` at line 110, re-raised at 114/128) is fragile.

**Do:**
- Use `concurrent.futures.ThreadPoolExecutor` without the `with` block, or call
  `executor.shutdown(wait=False)` on cancel so the method returns promptly.
- Prefer a cancellable HTTP approach: poll with `future.result(timeout=0.1)`
  inside a loop catching `TimeoutError`, and on cancel close the underlying
  response / abandon the worker without blocking the UI.
- Stop using `return e` as a sentinel; raise inside `do_request` and catch in
  the caller, or return a typed result object.

### P0.3 [VERIFIED] `QThread.wait()` with no timeout can freeze the app
`ui/main_window.py:60` and `:592` call `self._ai_worker.wait()` with no
timeout. If the worker deadlocks (see P0.2), the UI thread blocks forever,
including during window close.

**Do:** `self._ai_worker.wait(5000)`; if it returns `False`, log and
`terminate()` as a last resort. Apply the same pattern anywhere a worker is
joined on the UI thread.

### P0.4 [VERIFIED] Bare `except:` clauses swallow everything
Confirmed at:
- `ui/main_window.py:720` — batch export loop hides per-file export failures,
  then reports `"Exported N files"` (misleading count).
- `transcriber.py:250` — temp-file cleanup.
- `zoom_to_blog.py:271`.
- `lm_studio_manager.py:141`.
- `build.py:24`.

**Do:** Replace each `except:` with `except Exception as e:` (or a specific
type), log via `core.logger.get_logger`, and for the export loop collect the
failed files and surface them (status text + optional dialog) instead of a
silent skip.

---

## P1 — Reliability & UX

### P1.1 [CHECK] No timeouts on long LM calls from processors
`text_processor.py` (around lines 196/214/220) calls `chat_completion(...)`
without passing `timeout`, relying on the 300 s default. Long book/article runs
can appear hung. **Do:** thread an explicit, configurable timeout through and
surface a "still working…" heartbeat in the UI.

### P1.2 [CHECK] Fragile JSON/markdown-fence parsing of LLM output
Repeated in `article_generator.py` (~316), `zoom_to_blog.py` (~291),
`exporters.py` (~588). Splitting on ` ``` ` and assuming `json` prefixes / `N.`
list formats throws `IndexError` on malformed model output.
**Do:** Centralize a single robust `extract_json_block(text)` / `strip_fences()`
helper in `core/` with defensive parsing (regex for fenced blocks, fall back to
`json.loads` on the whole string, return a typed failure rather than crashing).

### P1.3 [CHECK] Model loading runs on the UI thread
`ui/ai_panel.py` `_load_model()` (~358) calls
`self._manager.load_model(model_path, gpu="auto")` synchronously; `book_panel`
shows "Loading model…" with no spinner. Large models freeze the window.
**Do:** Move model load into a `QThread`/worker, show an indeterminate progress
indicator, and keep the UI responsive (cancellable if possible).

### P1.4 [CHECK] Duplicate connection-check logic + stacking timers
`ai_panel.py` (~294-313) and `book_panel.py` (~299-328) each implement their
own LM Studio polling `QTimer`. Timers may not be disconnected/stopped on widget
destruction. **Do:** Extract one shared `LMConnectionMonitor` (single QTimer,
signal-based) reused by both panels; stop+disconnect in `closeEvent`/`cleanup`.

### P1.5 No confirmation on destructive actions
`batch_panel._clear_queue()` (~314) wipes the queue with no confirm; canceling
an active transcription has no guard. **Do:** Add `QMessageBox` confirmations
for clear-queue, cancel-active-job, and export-overwrite.

### P1.6 No file validation on selection
`ui/file_selector.py` `_set_file()` (~202) accepts any path with no size /
duration / format check. **Do:** Validate the file is a supported media type,
warn on very large files, and (optionally) show an estimated transcription time
based on duration + selected model.

### P1.7 Misleading / silent UI states
- Export buttons emit even when there's nothing to export
  (`article_view._on_export` ~241 returns silently) — disable the button or
  toast "Nothing to export".
- "Speakers" button (`transcript_view.py` ~109) and "Start LM Studio Server"
  button (`ai_panel.py` ~159) appear/disappear with no explanation — add
  tooltips explaining the precondition rather than hiding silently.

### P1.8 Robustness of system-probe parsing
`utils.py` GPU detection splits `nvidia-smi` / `rocminfo` output on `:` /
newlines without bounds checks (~70, ~89) → `IndexError` on unexpected output.
`lm_studio_manager.py` (~209) assumes the `/models` payload is a list.
**Do:** Guard all external-command/JSON parsing with length checks and
`.get(...)` access.

---

## P2 — Structure & maintainability

### P2.1 Dependency injection for the LM client
`text_processor.py`, `article_generator.py`, `book_pipeline.py`, and
`core/ai_worker.py` each instantiate their own `LMStudioClient` (and
`ai_worker` reaches into `pipeline._client`, breaking encapsulation).
**Do:** Construct one client and inject it; expose a public accessor instead of
`_client`.

### P2.2 Centralize configuration
LM Studio URL, timeouts, chunk sizes, and `whisper.cpp` paths are hardcoded
across `config.py`, `core/lm_client.py`, `zoom_to_blog.py`
(`~/whisper.cpp/build/bin/whisper-cli`), and the processors. **Do:** Route all
of these through `Config`/`get_config()` so users can change them once.

### P2.3 Reduce god objects / callback-hell
`TranscriptionWorker` (transcriber.py) mixes model load, transcription,
diarization, and progress. `core/ai_worker.py` handles cleaning + articles +
book pipeline with the cancellation check copy-pasted into every method.
**Do:** Split responsibilities; factor a single `if self._is_cancelled():`
guard helper.

### P2.4 [CHECK] `batch_processor.py` synchronization
Uses a `threading.Event().wait()` with no timeout (~151) to bridge transcriber
callbacks back to a QThread — risks deadlock if the worker dies before
signaling. **Do:** Drive batch progress with Qt signals/slots end-to-end, or
add a timeout + liveness check.

### P2.5 Testing & CI
Only `test_whisper.py` exists (an 8-line introspection script — not a real
test). `TESTING.md` describes manual steps. **Do:**
- Add `pytest` unit tests for pure logic: `extract_json_block`, chunking in
  `text_processor`, exporters, config load/save, `utils` timestamp formatting.
- Add a GitHub Actions workflow (lint with `ruff`, run `pytest`). Consider a
  SessionStart hook so web sessions can run them.

---

## P3 — UI improvements (concrete)

Prioritized, highest-value first:

1. **Progress timeline / staged status.** Replace the flat status label with a
   stage indicator: *Select → Extract audio → Transcribe (NN%) → Diarize →
   Clean → Generate*, color-coded (blue=working, green=done, red=error), each
   stage with its own progress where known.
2. **Real loading indicators.** Animated spinner/progress ring for model
   loading and any blocking LM call (replaces static "Loading model…" text).
3. **Keyboard shortcuts.** `Ctrl+O` open, `Ctrl+E` export, `Ctrl+C` copy,
   `Ctrl+A` select-all, `Ctrl+Q` quit. Wire via `QShortcut`/`QAction`.
4. **Better empty states.** Centered guidance in empty panels ("No file
   selected — drag & drop or Browse", "No transcription yet", "No article yet").
5. **Batch queue UX.** Drag-and-drop files into the queue, drag-to-reorder,
   pause/resume, total queue duration, ETA, and per-item remove (instead of
   only clear-all). The fixed 150 px list height (`batch_panel.py` ~194) should
   grow/scroll.
6. **Responsive layout.** Replace hardcoded sizes (`main_window.py` min
   900×550 / resize 1100×700, fixed list heights) with proportional layouts and
   a collapsible left panel; persist window geometry between runs.
7. **Side-by-side original vs cleaned text** with the ability to revert
   (poor-man's undo for the read-only cleaned view), addressing the "no undo
   after cleaning" gap.
8. **Per-format regenerate.** In the article view, regenerate a single format
   (e.g. just the Summary) without re-running all formats; show a short preview
   and a quality/confidence score per format.
9. **Advanced settings panel.** Collapsible section for GPU selection, thread
   count, LM Studio URL, timeouts, chunk size — backed by `Config` (P2.2).
10. **Live validation feedback.** Word/char count in custom-prompt fields,
    existence check + ✓/✗ icon for custom prompt file before "Run".

---

## P4 — New feature ideas (worth scoping with the user)

- **In-app transcript editing + find/replace**, then re-export — currently the
  transcript view is read-only.
- **Speaker label renaming**: let the user map `SPEAKER_00` → real names and
  propagate through diarized output and exports.
- **Subtitle export (SRT/VTT)** from the timestamped segments — natural fit for
  a transcription tool and easy given existing timecodes (`utils.py` formatter).
- **Search across past transcripts** / a lightweight project library with recent
  files and saved outputs.
- **Localization (i18n).** Docs and some UI are mixed English/Russian; extract
  strings and offer at least EN/RU.
- **Glossary / custom-vocabulary biasing** for domain terms to improve Whisper
  accuracy.
- **Pluggable AI backend** beyond LM Studio (any OpenAI-compatible endpoint),
  exposed in settings while staying offline-capable.
- **Auto-detect language + per-file model override** in batch mode.
- **Resumable batch processing**: persist queue to disk so an interrupted
  overnight run resumes instead of restarting.

---

## Suggested execution order

1. P0.1–P0.4 (privacy + the cancel/wait/except correctness cluster).
2. P1.1–P1.4 (timeouts, robust parsing, off-thread model load, shared monitor).
3. P2.5 (tests + CI) so subsequent refactors are safe.
4. P2.1–P2.4 refactors.
5. P3 UI work, then P4 features as scoped with the user.

When implementing, keep changes small and reviewable, add/adjust tests with each
behavioral change, and never weaken the offline/privacy guarantees.
