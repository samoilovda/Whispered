# Test fixtures

`batch_golden_result.json` is the small, committed contract fixture for the
existing batch output model.  It intentionally contains no raw recording.

The L1 ten-minute audio fixture must be a consented, redistributable recording
and must not be committed to this repository.  Keep a local copy at
`tests/fixtures/private/live-baseline-10m.wav` (this directory is ignored),
then run the manual baseline in `TESTING.md`. Record its transcript metrics
and the app/model versions in the release task, not in source control.
