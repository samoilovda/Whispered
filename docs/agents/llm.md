# Local LLM (LM Studio) and prompts

Read `docs/LLM_RESEARCH.ru.md` first: it is the single record of local-LLM
limits, measurements and decisions. Add every new experiment and idea there
(its §9 template); a variant that distorts meaning is disqualified whatever its
speed.

## Prompts

- Every LLM task is an editable Markdown prompt in `prompts/`, loaded via
  `core.prompts.load_prompt`.
- `prompts/glossary.md` is a term list, not a task: `core/glossary.py` attaches
  only the entries a text uses to the cleaning and insight prompts.
- Prompts embed the transcript as ~25-second coalesced blocks
  (`core/insights.py::_build_prompt_text`); never reintroduce per-segment
  timestamp lines.

## Client and model behaviour

- gemma-4 is a reasoning model: hidden reasoning shares the `max_tokens` budget
  (`core/insights.py::_RESPONSE_MAX_TOKENS`) and streams no visible content.
  A starved budget shows as an empty response with `finish_reason: length`.
- In `core/lm_client.py::chat_completion_stream` the socket keeps the caller's
  full timeout. Never give it a short poll timeout: after the first
  `socket.timeout` CPython's buffered reader fails every later read, which
  breaks every reasoning-model request. Poll cancellation on a queue fed by a
  reader thread.
- Never retry a stream by reopening the request: it fires duplicate
  generations at LM Studio, and a broad `except OSError` turns a refused
  connection (`URLError` subclasses `OSError`) into a busy loop.
- Never fire parallel LLM requests at LM Studio on long transcripts
  (`application/job_engine.py` caps `local_llm` at 1): the server can stop
  responding while `lms server status` still says running. Recovery:
  `lms server stop && lms server start`, then reload the model.

## Running LM Studio from a shell

- CLI: `/Users/den/.lmstudio/bin/lms`. Load with the full key and flags
  (`lms load google/gemma-4-12b -y --gpu max`); bare `lms load` opens an
  interactive picker that hangs non-interactive shells.
- One model at a time: two don't fit in memory on the 24 GB Mac.
- Keep test prompts under ~50K tokens on that Mac: a ~90K-token prompt froze
  the whole system. Ask the user before any long-context stress test and stop
  on swap growth.
- Model comparisons: `tools/bench_llm_models.py`; results go to
  `docs/LLM_RESEARCH.ru.md`.
