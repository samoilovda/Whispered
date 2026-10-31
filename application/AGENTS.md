# application/ — Qt-free coordination between UI and engines

The root `AGENTS.md` import rules apply: no `PyQt6`, `ui` or `core.live`.

- Register new panel dependencies and export formats with the existing
  controllers (`document_session.py`, `export_controller.py`) instead of adding
  another hand-copied call site in `ui/main_window.py`.
- `DocumentSession.apply_result()` is the single fan-out of a new, loaded or
  edited `TranscriptionResult` to every panel.
- Every recipe run and every single-step generator (clean, article, insights,
  YouTube, book) executes through `job_engine.py` (dependency order,
  per-resource limits such as `local_llm=1`, `Artifact`-based cache-skip),
  wrapped by `ui/job_runner.py`. Don't start generators any other way.
- `steps.py` owns the step registry (`STEP_REGISTRY`: runner, artifact factory,
  loader, `viewer` panel per step). `build_step_context()`/`llm_params()` are
  the one place a `StepContext` is assembled.
- `launch_params.py` is the one rule for recipe-over-widget transcription
  settings, shared by the single-file path and the queue.
- History writes made of several store calls go through `records.py`
  (`save_new_record()` honours `history_enabled`, `add_record_badges()`); UI code
  calls these instead of composing store writes. Single reads/edits (title,
  bookmark) stay direct `core.history` calls.
- Files a panel saves get a provenance record via `artifacts.record_export()`;
  manifests are written through `infrastructure/persistence/artifact_store.py`.
- User edits to a generated result live apart from it (`user_edits.py`).
- `style_examples.py` feeds approved/edited titles and descriptions to
  `core.insights` (`Config.yt_style_examples`); their fingerprint is part of
  the `youtube_package` prompt version, so changing them invalidates its cache.
- `cover_setup.py` stores a record's Cover workspace in
  `<artifact_dir>/cover.setup.json`; photos are copied into `cover_photos/`,
  but the Settings host photo is stored as a reference, not copied.
- `youtube_autoupload.py` keeps the queue of approved packages
  (`youtube_upload.queued.json` + frozen `cover.queued.png` per record);
  network rules for it are root rule 1.
