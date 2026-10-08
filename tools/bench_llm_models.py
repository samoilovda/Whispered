#!/usr/bin/env python3
"""Benchmark the model loaded in LM Studio on Whispered's LLM tasks.

Runs, on one history record's transcript, what the "YouTube video"
recipe asks of the local model — AI cleaning of the first N chunks and
the five YouTube package insights — and saves timings plus the outputs,
so models can be compared on speed *and* quality. Results and the
comparison so far: docs/LLM_BENCHMARKS.ru.md.

Load exactly one model first (two rarely fit in memory), e.g.:
    lms unload --all && lms load google/gemma-4-e4b -y --gpu max

Usage:
    .venv/bin/python tools/bench_llm_models.py [--record 48] [--chunks 1]
        [--tasks clean,youtube] [--out output/bench/llm]

Per-call token counts and timings go to stdout via core.lm_client's
"LLM ..." log lines; outputs land in <out>/<model>/.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config  # noqa: E402
from core.history import get_history_store  # noqa: E402
from core.lm_client import LMStudioClient  # noqa: E402
from domain.transcription import Segment, TranscriptionResult  # noqa: E402

_YOUTUBE_TYPES = ("chapters", "yt_titles", "yt_description", "yt_tags", "yt_questions")


def _load_record(record_id: int) -> TranscriptionResult:
    record = get_history_store().get(record_id)
    if record is None:
        raise SystemExit(f"No history record {record_id}")
    segments = [
        Segment(s["start"], s["end"], s["text"], s.get("speaker"), confidence=s.get("confidence"))
        for s in record["segments"]
    ]
    return TranscriptionResult(
        segments=segments, language=record["language"], duration=record["duration"],
    )


def _bench_clean(client: LMStudioClient, result: TranscriptionResult, chunks: int, out: Path) -> dict:
    from text_processor import TextCleaner

    cleaner = TextCleaner(client)
    summary = []
    for index, chunk in enumerate(cleaner._split_into_chunks(result.full_text)[:chunks]):
        started = time.monotonic()
        cleaned = cleaner._clean_with_ai(chunk)
        seconds = round(time.monotonic() - started, 1)
        (out / f"clean_chunk{index + 1}.txt").write_text(cleaned, encoding="utf-8")
        paragraphs = [p for p in cleaned.split("\n\n") if p.strip()]
        summary.append({"chunk": index + 1, "seconds": seconds, "input_chars": len(chunk),
                        "output_chars": len(cleaned), "paragraphs": len(paragraphs)})
        print(f"clean chunk {index + 1}: {seconds}s, {len(paragraphs)} paragraphs", flush=True)
    return {"chunks": summary}


def _bench_youtube(lm_url: str, result: TranscriptionResult, out: Path) -> dict:
    from core.insights import generate_insight
    from utils import language_name_for_code

    language = get_config().yt_language or language_name_for_code(result.language)
    payload, seconds = {}, {}
    for insight_type in _YOUTUBE_TYPES:
        started = time.monotonic()
        payload[insight_type] = generate_insight(
            insight_type, result.segments, lm_url=lm_url, language=language,
        )
        seconds[insight_type] = round(time.monotonic() - started, 1)
        print(f"{insight_type}: {seconds[insight_type]}s", flush=True)
    (out / "youtube_package.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"seconds": seconds, "total": round(sum(seconds.values()), 1),
            "counts": {k: len(v) for k, v in payload.items()}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--record", type=int, default=48)
    parser.add_argument("--chunks", type=int, default=1, help="cleaning chunks to run")
    parser.add_argument("--tasks", default="clean,youtube")
    parser.add_argument("--out", default="output/bench/llm")
    args = parser.parse_args()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("  %(message)s"))
    logging.getLogger("core.lm_client").addHandler(handler)
    logging.getLogger("core.lm_client").setLevel(logging.INFO)

    lm_url = get_config().lm_studio_url
    ok, model = LMStudioClient(lm_url).probe()
    if not ok or not model:
        raise SystemExit(f"LM Studio has no model loaded at {lm_url}")
    print(f"model: {model}", flush=True)

    out = ROOT / args.out / re.sub(r"[^\w.-]+", "_", model)
    out.mkdir(parents=True, exist_ok=True)
    result = _load_record(args.record)
    summary: dict = {"model": model, "record": args.record,
                     "date": time.strftime("%Y-%m-%d %H:%M")}
    tasks = set(args.tasks.split(","))
    if "clean" in tasks:
        summary["clean"] = _bench_clean(LMStudioClient(lm_url, model=model), result, args.chunks, out)
    if "youtube" in tasks:
        summary["youtube"] = _bench_youtube(lm_url, result, out)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved to {out}", flush=True)


if __name__ == "__main__":
    main()
