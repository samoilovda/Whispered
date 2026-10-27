# core/ — workers, clients, persistence helpers

Root rules apply in full: `core/` never imports `application/`, `ui/`,
`transcriber.py` or `batch_processor.py`; every `QThread` follows rule 3.
Before changing `lm_client.py`, `insights*.py`, `prompts.py` or `glossary.py`
read `docs/agents/llm.md`.

- New background work subclasses `base_worker.BaseWorker` and is owned through
  `worker_registry.WorkerRegistry`.
- `audio_convert.py` (FFmpeg → 16 kHz WAV) is shared by the transcriber and
  multitrack.
- `history.py` is the SQLite+FTS5 store; records carry a user `title` and
  `bookmarks`. Multi-call writes go through `application/records.py`.
- `model_repository.py`/`model_manifest.py` are the Qt-free verified download:
  `ModelRepository.ensure()` checks size and sha256 from the manifest, whose
  sizes and sha256 come from a pinned Hugging Face commit.
- UI text, plural forms and list dates come from `i18n.py` and
  `date_format.py` (see `docs/agents/i18n.md`).
- `youtube_oauth.py`/`youtube_upload.py` are Qt-free clients and
  `youtube_upload_worker.py` the `BaseWorker` wrapper; network use only under
  root rule 1. Tokens live in the OS keyring.
- `recording_watch.py` watches `Config.source_folder` and emits each finished
  new recording; `MainWindow` then offers a recipe via
  `ui/recording_offer_dialog.py` (opt-in: `Config.recording_offer_enabled`).
- `ai_provider.py` is the optional user-keyed cloud provider (root rule 1).
- `thumb_titles.py` parses the LLM's cover title proposals; `fuzzy.py` does
  command-palette matching.
