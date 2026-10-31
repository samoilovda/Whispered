# domain/ — Qt-free data types and pure logic

The root `AGENTS.md` import rules apply: nothing here imports `PyQt6`, `ui`,
`core.live`, `core`, `application` or `infrastructure`.

- Put new shared data types here, not in `transcriber.py` (it only re-exports
  `Segment`, `Word`, `TranscriptionResult` for older call sites).
- Low-confidence marking uses `Segment.confidence` and `LOW_CONFIDENCE`
  (`transcription.py`).
- `paragraphs.group_paragraphs` decides how a transcript reads as paragraphs.
- `artifact.Artifact` is the provenance record of a generated file (transcript
  revision, provider, model, prompt version); cache reuse goes through its
  `cache_key()`. Its inputs come from `artifact_provenance.py`.
- `job.py` (`JobSpec`/`StepSpec`/`StepOutcome`) is only a step DAG's shape;
  `application/job_engine.py` runs it. `recipe.Recipe` is a named, editable
  set of steps plus run params.
- `watch_folder.py`/`latest_source.py` hold the pure "what's new" logic;
  `core/watch_folder.py` is the Qt side.
- `multitrack.py`/`multitrack_merge.py` model Zoom per-participant recordings;
  `diarization.py` holds the diarizer's result types and `assign_speakers`.
