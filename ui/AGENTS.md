# ui/ — PyQt6 widgets and flow controllers

Root rules 2 and 3 (never block the UI; `WorkerRegistry`, `DirectConnection`,
real-Qt tests in `tests_qt/`) apply to everything here. Before touching any
user-visible string read `docs/agents/i18n.md`.

- Give a new self-contained flow its own controller module instead of more
  `MainWindow` methods. Existing ones: `recipe_run_controller.py`,
  `youtube_publish_controller.py`, `live_session_controller.py`,
  `operation_controller.py`, `export_flow_controller.py`,
  `document_lifecycle_controller.py`. Saving edits and `_prepare_leave` are
  still on `MainWindow`.
- `main_window.py` starts single-step jobs through `job_runner.py` (the QThread
  adapter for `application/job_engine.py`); a step's result reaches its panel
  via `StepDefinition.viewer`.
- `operation_controller.py` lists background work and addresses Cancel to one
  operation; its Qt-free model is `application/operations.py`.
- Export planning/writing runs through `call_worker.py`, off the UI thread.
- `youtube_queue_dialog.py` + `youtube_queue_worker.py` are the in-app run of
  `tools/youtube_autoupload.py` (same code, same rule-1 conditions).
- A missing whisper model is fetched by `model_downloader.py` through
  `MainWindow._ensure_whisper_model`; `option_labels.py` reads the manifest's
  sizes to label models as downloaded.
- Use `qt_util.must()` to unwrap a Qt accessor that PyQt6's stubs type as
  optional where the object is known to exist.
- `tray.py` mirrors the window in the menu bar; `shortcuts_dialog.py` lists keys
  from the menu itself — add shortcuts to the menu, not to the dialog.
- Imports: from concrete modules only (`ui/__init__.py` has no re-exports).
