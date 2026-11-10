#!/usr/bin/env python3
"""Benchmark speech recognition on recordings that have a reference text.

Runs each audio/video file through the same path as the app
(``Transcriber.transcribe``) under one named configuration and scores it
against ``<refs>/<name>.txt`` (the file's stem). Optional
``<refs>/<name>.words.json`` — a list of ``{"start", "end", "text"}`` —
adds hallucination-in-silence and word-boundary metrics. Results and the
comparison so far: docs/ASR_RESEARCH.ru.md.

Metrics: WER, CER, RTF (transcription time / audio duration), words
written inside reference silences of >= 2 s, excess repeated words, and
mean / p90 word-boundary error (needs words in the result and a
``.words.json`` reference).

An optional <name>.terms.json list reports missing and extra occurrences
of annotated names, numbers, or other terms against the text reference.

Usage:
    .venv/bin/python tools/bench_asr.py input/talk.m4a [more files] \
        [--config baseline] [--refs output/bench/asr/refs] \
        [--out output/bench/asr] [--language ru]

Configurations are the keyword overrides of ``Transcriber.transcribe`` in
``CONFIGS``; add one per feature being measured. Report:
<out>/<timestamp>/report.md and results.json.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config  # noqa: E402
from core import asr_metrics as am  # noqa: E402
from domain.transcription import TranscriptionResult  # noqa: E402

CONFIGS: dict[str, dict[str, Any]] = {
    "baseline": {},
    "words": {"word_timestamps": True},
    "diarize": {"enable_diarization": True},
}


def _load_ref_words(path: Path) -> list[am.TimedWord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [am.TimedWord(float(w["start"]), float(w["end"]), str(w["text"])) for w in data]


def _spans(result: TranscriptionResult) -> list[am.TimedSpan]:
    return [
        am.TimedSpan(
            s.start, s.end, s.text,
            tuple(am.TimedWord(w.start, w.end, w.text) for w in s.words),
        )
        for s in result.segments
    ]


def transcribe(path: Path, overrides: dict[str, Any], language: str, model: str):
    """Run one file; returns (result, seconds) or raises RuntimeError."""
    from PyQt6.QtCore import QCoreApplication

    from transcriber import Transcriber

    app = QCoreApplication.instance() or QCoreApplication([])
    done = threading.Event()
    box: dict[str, Any] = {}

    def finished(result: TranscriptionResult) -> None:
        box["result"] = result
        done.set()

    def error(message: str) -> None:
        box["error"] = message
        done.set()

    transcriber = Transcriber()
    started = time.monotonic()
    worker = transcriber.transcribe(
        str(path), model, language=language, n_threads=_threads(),
        on_finished=finished, on_error=error, direct_callbacks=True,
        **overrides,
    )
    if worker is None:
        raise RuntimeError(box.get("error", "transcription did not start"))
    done.wait()
    elapsed = time.monotonic() - started
    transcriber.shutdown()
    del app
    if "error" in box:
        raise RuntimeError(box["error"])
    return box["result"], elapsed


def _threads() -> int:
    from utils import get_thread_count

    return get_thread_count("balanced")


def score(result: TranscriptionResult, seconds: float,
          ref_text: Optional[str], ref_words: Optional[list[am.TimedWord]],
          terms: tuple[str, ...] = ()) -> dict[str, Any]:
    spans = _spans(result)
    duration = result.source_duration
    row: dict[str, Any] = {
        "duration": duration,
        "seconds": seconds,
        "stage_seconds": result.stage_seconds,
        "peak_rss_bytes": result.peak_rss_bytes,
        "rtf": seconds / duration if duration else None,
        "segments": len(spans),
        "repeats": am.repeat_count(spans),
    }
    if ref_text is not None:
        row["wer"] = am.wer(ref_text, result.full_text)
        row["cer"] = am.cer(ref_text, result.full_text)
        if terms:
            row["terms"] = am.term_counts(ref_text, result.full_text, terms)
    if ref_words is not None:
        if duration is not None:
            silences = am.silence_intervals(ref_words, duration)
            row["silence_words"] = am.silence_hallucination_words(spans, silences)
        row["reference_words_outside_segments"] = am.uncovered_reference_words(ref_words, spans)
        hyp_words = [w for s in spans for w in s.words]
        if hyp_words:
            err = am.boundary_error(ref_words, hyp_words)
            if err is not None:
                row["boundary_mean"] = err.mean
                row["boundary_p90"] = err.p90
                row["boundary_matched"] = err.matched
    return row


def _fmt(value: Any, spec: str = ".3f") -> str:
    return "—" if value is None else format(value, spec)


def _run_metadata() -> dict:
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True, timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        revision = None
    try:
        binding = version("pywhispercpp")
    except PackageNotFoundError:
        binding = None
    return {"commit": revision, "platform": platform.platform(), "threads": _threads(), "pywhispercpp": binding}


def render_report(config: str, model: str, rows: dict[str, dict[str, Any]]) -> str:
    lines = [
        f"# ASR bench — config `{config}`, model `{model}`",
        "",
        "| file | min | WER | CER | RTF | silence words | repeats | bnd mean | bnd p90 | elapsed (s) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, r in rows.items():
        if "error" in r:
            lines.append(f"| {name} | error: {r['error']} |||||||")
            continue
        lines.append(
            f"| {name} | {_fmt(r['duration'] / 60 if r['duration'] else None, '.1f')} | {_fmt(r.get('wer'))} | "
            f"{_fmt(r.get('cer'))} | {_fmt(r.get('rtf'), '.2f')} | "
            f"{_fmt(r.get('silence_words'), 'd')} | {r['repeats']} | "
            f"{_fmt(r.get('boundary_mean'))} | {_fmt(r.get('boundary_p90'))} | "
            f"{r['seconds']:.1f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--config", default="baseline", choices=sorted(CONFIGS))
    parser.add_argument("--refs", type=Path, default=ROOT / "output/bench/asr/refs")
    parser.add_argument("--out", type=Path, default=ROOT / "output/bench/asr")
    parser.add_argument("--language", default=None, help="default: Config.default_language")
    parser.add_argument("--model", default=None, help="default: Config.default_model")
    parser.add_argument("--cpu", action="store_true", help="disable GPU acceleration")
    parser.add_argument("--cache-state", choices=("cold", "warm", "unknown"), default="unknown")
    args = parser.parse_args()

    cfg = get_config()
    language = args.language or cfg.default_language
    model = args.model or cfg.default_model
    out = args.out / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True, exist_ok=True)

    rows: dict[str, dict[str, Any]] = {}
    for path in args.files:
        ref_txt = args.refs / f"{path.stem}.txt"
        ref_words_path = args.refs / f"{path.stem}.words.json"
        terms_path = args.refs / f"{path.stem}.terms.json"
        ref_text = ref_txt.read_text(encoding="utf-8") if ref_txt.is_file() else None
        ref_words = _load_ref_words(ref_words_path) if ref_words_path.is_file() else None
        terms = tuple(json.loads(terms_path.read_text(encoding="utf-8"))) if terms_path.is_file() else ()
        print(f"{path.name}: {args.config} ...", flush=True)
        try:
            result, seconds = transcribe(
                path, {**CONFIGS[args.config], "use_gpu": not args.cpu}, language, model,
            )
        except RuntimeError as exc:
            rows[path.stem] = {"error": str(exc)}
            print(f"  error: {exc}")
            continue
        rows[path.stem] = score(result, seconds, ref_text, ref_words, terms)
        (out / f"{path.stem}.txt").write_text(result.full_text, encoding="utf-8")
        print(f"  {rows[path.stem]}")

    report = render_report(args.config, model, rows)
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "results.json").write_text(
        json.dumps({"config": args.config, "model": model, "language": language,
                    **_run_metadata(),
                    "cache_state": args.cache_state, "use_gpu": not args.cpu,
                    "files": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(report)
    print(f"saved to {out}")
    return int(any("error" in row for row in rows.values()))


if __name__ == "__main__":
    sys.exit(main())
