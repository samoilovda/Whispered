#!/usr/bin/env python3
"""Benchmark harness for Zoom multitrack recordings vs the plain mixed file.

See docs/MULTITRACK_ZOOM_PLAN.ru.md (M0, M5) for the methodology this
implements. Standalone script — spawns whisper.cpp child processes exactly
like the app does (transcriber.py's ``_run_transcription_process``), so it
needs the same ``if __name__ == "__main__":`` guard.

Usage:
    .venv/bin/python tools/bench_multitrack.py <recording_folder> \\
        --model large-v3-turbo-q5_0 --language ru \\
        --modes mixed,multitrack,multitrack-naive \\
        --out-dir output/bench/multitrack

Modes:
    mixed              Mode A: transcribe the single mixed-down file.
    mixed+diarization  Mode B: mixed file + pyannote diarization (skipped
                        with a note if pyannote/HF token aren't configured).
    multitrack         Mode C: per-track VAD gating + bleed suppression.
    multitrack-naive   Mode C-naive: per-track, whole file, no VAD gate,
                        no bleed suppression — the control that shows what
                        the processing in M2/M3 actually buys.

Only the reference-free ("*") metrics from the plan (§6.3.4, 6.3.5, 6.3.7,
6.3.8, plus a candidate term list) are computed here. WER, speaker
attribution accuracy, and overlap recall (§6.3.1-6.3.3) need a
human-reviewed reference transcript — this script emits
reference_draft.md for a human to correct, and leaves those metrics out of
metrics.json (has_reference: false) until that happens.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import platform
import queue
import resource
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.live.echo_detector import normalize_text  # noqa: E402
from core.multitrack_audio import (  # noqa: E402
    build_gated_track,
    convert_track_to_wav,
    detect_speech_windows,
    probe_media_duration,
    read_wav_int16_mono,
    remap_segment_to_track_time,
    speech_coverage_seconds,
    write_wav,
)
from core.multitrack_bleed import TrackAudio, suppress_bleed  # noqa: E402
from domain.multitrack import MultiTrackRecording, detect_multitrack  # noqa: E402
from domain.multitrack_merge import TrackResult, merge_track_results  # noqa: E402
from domain.transcription import TranscriptionResult  # noqa: E402

ALL_MODES = ("mixed", "mixed+diarization", "multitrack", "multitrack-naive")


# --------------------------------------------------------------------------
# Synchronous transcription (no Qt, no GUI event loop)
# --------------------------------------------------------------------------

def transcribe_sync(
    filepath: str,
    model_name: str,
    language: str = "auto",
    n_threads: int = 4,
    use_gpu: bool = True,
    enable_diarization: bool = False,
    num_speakers: Optional[int] = None,
    initial_prompt: Optional[str] = None,
    quiet: bool = False,
) -> TranscriptionResult:
    """Run one whisper.cpp pass and block for the result — the same child
    process function the real TranscriptionWorker uses, minus Qt signals."""
    from transcriber import _run_transcription_process

    ctx = mp.get_context("spawn")
    q: mp.Queue = ctx.Queue()
    proc = ctx.Process(
        target=_run_transcription_process,
        args=(
            filepath, model_name, language, False, n_threads,
            enable_diarization, num_speakers, q, initial_prompt, False, use_gpu,
        ),
    )
    proc.start()
    result: Optional[TranscriptionResult] = None
    error: Optional[str] = None
    try:
        while True:
            try:
                msg = q.get(timeout=0.2)
            except queue.Empty:
                if not proc.is_alive():
                    break
                continue
            if msg[0] == "progress":
                if not quiet:
                    print(f"    [{msg[1]:3d}%] {msg[2]}", file=sys.stderr)
            elif msg[0] == "result":
                result = msg[1]
            elif msg[0] == "error":
                error = msg[1]
            elif msg[0] == "terminal":
                break
    finally:
        proc.join(timeout=10)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)

    if error:
        raise RuntimeError(f"transcription failed for {filepath}: {error}")
    if result is None:
        raise RuntimeError(
            f"transcription of {filepath} exited without a result (exit code {proc.exitcode})"
        )
    return result


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

@dataclass
class RunStats:
    mode: str
    wall_clock_seconds: float
    audio_fed_to_asr_seconds: float
    recording_seconds: float
    whisper_invocations: int
    peak_rss_mb: float

    @property
    def rtf(self) -> float:
        return self.wall_clock_seconds / self.recording_seconds if self.recording_seconds else 0.0


def _peak_rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    # macOS reports bytes, Linux reports KB.
    return usage / (1024 * 1024) if platform.system() == "Darwin" else usage / 1024


def run_mode_mixed(
    recording: MultiTrackRecording, *, model_name: str, language: str,
    n_threads: int, use_gpu: bool, enable_diarization: bool = False,
    num_speakers: Optional[int] = None, mode_label: str = "mixed",
) -> Tuple[TranscriptionResult, RunStats]:
    if recording.mixed_audio is None:
        raise RuntimeError("recording has no mixed audio file")
    recording_seconds = probe_media_duration(str(recording.mixed_audio))
    t0 = time.monotonic()
    result = transcribe_sync(
        str(recording.mixed_audio), model_name, language, n_threads, use_gpu,
        enable_diarization=enable_diarization, num_speakers=num_speakers,
    )
    wall = time.monotonic() - t0
    stats = RunStats(
        mode=mode_label, wall_clock_seconds=wall, audio_fed_to_asr_seconds=recording_seconds,
        recording_seconds=recording_seconds, whisper_invocations=1, peak_rss_mb=_peak_rss_mb(),
    )
    return replace_duration(result, recording_seconds), stats


def replace_duration(result: TranscriptionResult, duration: float) -> TranscriptionResult:
    from dataclasses import replace
    return replace(result, duration=duration)


def _track_source_id(index: int) -> str:
    return f"track_{index}"


def run_mode_multitrack(
    recording: MultiTrackRecording, *, model_name: str, language: str,
    n_threads: int, use_gpu: bool, apply_vad: bool, apply_bleed: bool,
    mode_label: str,
) -> Tuple[TranscriptionResult, RunStats, Dict]:
    if not recording.tracks:
        raise RuntimeError("recording has no per-participant tracks")

    recording_seconds = max(
        probe_media_duration(str(t.path)) for t in recording.tracks
    )
    wall_start = time.monotonic()
    audio_fed_seconds = 0.0
    invocations = 0
    per_track_audio: List[TrackAudio] = []
    track_results: List[TrackResult] = []

    for i, track in enumerate(recording.tracks, start=1):
        source = _track_source_id(i)
        wav_path = convert_track_to_wav(track.path)
        gated_wav = wav_path.replace(".wav", f"_{source}_gated.wav")
        try:
            pcm, sample_rate = read_wav_int16_mono(wav_path)

            if apply_vad:
                windows = detect_speech_windows(wav_path, source=source)
                if not windows:
                    track_results.append(TrackResult(
                        source=source, display_name=track.display_name,
                        segments=(), language=language,
                    ))
                    per_track_audio.append(TrackAudio(source=source, segments=(), pcm=pcm, sample_rate=sample_rate))
                    continue
                gated = build_gated_track(windows, sample_rate=sample_rate)
                audio_fed_seconds += speech_coverage_seconds(windows)
                write_wav(gated_wav, gated.pcm, sample_rate=sample_rate)
                raw_result = transcribe_sync(gated_wav, model_name, language, n_threads, use_gpu, quiet=True)
                invocations += 1
                segments = tuple(
                    remap_segment_to_track_time(seg, gated.mapping) for seg in raw_result.segments
                )
            else:
                audio_fed_seconds += probe_media_duration(str(track.path))
                raw_result = transcribe_sync(wav_path, model_name, language, n_threads, use_gpu, quiet=True)
                invocations += 1
                segments = tuple(raw_result.segments)

            per_track_audio.append(TrackAudio(source=source, segments=segments, pcm=pcm, sample_rate=sample_rate))
            track_results.append(TrackResult(
                source=source, display_name=track.display_name,
                segments=segments, language=raw_result.language or language,
            ))
            print(f"    track {i}/{len(recording.tracks)} ({track.display_name}): "
                  f"{len(segments)} segments", file=sys.stderr)
        finally:
            Path(wav_path).unlink(missing_ok=True)
            Path(gated_wav).unlink(missing_ok=True)

    if apply_bleed:
        bleed_result = suppress_bleed(per_track_audio)
        kept_by_source = bleed_result.kept
        track_results = [
            TrackResult(source=t.source, display_name=t.display_name,
                        segments=tuple(kept_by_source.get(t.source, t.segments)), language=t.language)
            for t in track_results
        ]
        bleed_suppressed_seconds = bleed_result.bleed_suppressed_seconds
    else:
        bleed_suppressed_seconds = 0.0

    wall = time.monotonic() - wall_start
    merged = merge_track_results(track_results, total_duration=recording_seconds)
    stats = RunStats(
        mode=mode_label, wall_clock_seconds=wall, audio_fed_to_asr_seconds=audio_fed_seconds,
        recording_seconds=recording_seconds, whisper_invocations=invocations, peak_rss_mb=_peak_rss_mb(),
    )
    stats_extra = {"bleed_suppressed_seconds": bleed_suppressed_seconds}
    return merged, stats, stats_extra


# --------------------------------------------------------------------------
# Reference-free (*) metrics — §6.3.4, 6.3.5, 6.3.7, 6.3.8
# --------------------------------------------------------------------------

def _ngrams(words: List[str], n: int = 3) -> List[Tuple[str, ...]]:
    return [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]


def loop_ratio(result: TranscriptionResult) -> float:
    """Fraction of segments where a 3-gram repeats 3+ times — a classic
    whisper.cpp hallucination signature."""
    if not result.segments:
        return 0.0
    looping = 0
    for seg in result.segments:
        words = normalize_text(seg.text).split()
        counts = Counter(_ngrams(words))
        if counts and max(counts.values()) >= 3:
            looping += 1
    return looping / len(result.segments)


def duplicate_ratio(result: TranscriptionResult) -> float:
    """Fraction of adjacent segment pairs whose normalized text is >90% similar."""
    segs = result.segments
    if len(segs) < 2:
        return 0.0
    dup = 0
    for a, b in zip(segs, segs[1:]):
        na, nb = normalize_text(a.text), normalize_text(b.text)
        if not na or not nb:
            continue
        if SequenceMatcher(None, na, nb).ratio() > 0.9:
            dup += 1
    return dup / (len(segs) - 1)


def silence_hallucination_seconds(result: TranscriptionResult, speech_windows) -> float:
    """Total duration of segments with no overlap at all with any detected
    speech window — segments whisper produced from what VAD called silence."""
    total = 0.0
    for seg in result.segments:
        overlap = 0.0
        for w in speech_windows:
            overlap = max(overlap, min(seg.end, w.end) - max(seg.start, w.start))
            if overlap > 0:
                break
        if overlap <= 0:
            total += seg.end - seg.start
    return total


def speech_coverage_ratio(result: TranscriptionResult, speech_windows) -> float:
    """speech_seconds_transcribed / vad_speech_seconds (§6.3.5)."""
    vad_seconds = speech_coverage_seconds(speech_windows)
    if vad_seconds <= 0:
        return 0.0
    transcribed = 0.0
    for seg in result.segments:
        for w in speech_windows:
            overlap = min(seg.end, w.end) - max(seg.start, w.start)
            if overlap > 0:
                transcribed += overlap
    return min(1.0, transcribed / vad_seconds)


_STOPWORDS_RU = {
    "и", "в", "не", "на", "я", "с", "что", "а", "то", "он", "она", "мы",
    "вы", "они", "как", "это", "у", "но", "по", "к", "за", "из", "да",
    "нет", "же", "бы", "ну", "вот", "просто", "там", "тут", "ты", "если",
    "так", "уже", "или", "все", "ещё", "еще", "быть", "было", "когда",
}


def candidate_terms(reference_text: str, top_n: int = 20) -> List[Tuple[str, int]]:
    """Heuristic candidate term list from mode A's transcript: frequent
    words of 4+ chars, not in a small Russian stopword list. NOT
    human-confirmed — the plan (§6.3.6) requires that before using this
    list as an acceptance metric; treat these counts as a starting point.
    """
    words = normalize_text(reference_text).split()
    counts = Counter(w for w in words if len(w) >= 4 and w not in _STOPWORDS_RU)
    return counts.most_common(top_n)


def term_hits(text: str, terms: List[Tuple[str, int]]) -> Dict[str, int]:
    normalized = normalize_text(text)
    return {term: normalized.count(term) for term, _ in terms}


def _bucketed_words(result: TranscriptionResult, bucket_seconds: float, total_duration: float) -> Dict[int, set]:
    buckets: Dict[int, set] = {}
    n_buckets = int(total_duration // bucket_seconds) + 1
    for i in range(n_buckets):
        buckets[i] = set()
    for seg in result.segments:
        mid = (seg.start + seg.end) / 2
        idx = int(mid // bucket_seconds)
        if idx in buckets:
            buckets[idx].update(normalize_text(seg.text).split())
    return buckets


def mode_agreement(a: TranscriptionResult, b: TranscriptionResult, bucket_seconds: float = 30.0) -> Dict:
    """§6.3.8: word-overlap agreement between two modes' transcripts,
    bucketed by time, with the worst-agreeing buckets surfaced for a human
    to look at. Not a quality metric by itself — a diagnostic tool that
    also works without any reference transcript."""
    total_duration = max(a.duration, b.duration)
    buckets_a = _bucketed_words(a, bucket_seconds, total_duration)
    buckets_b = _bucketed_words(b, bucket_seconds, total_duration)
    ratios = []
    for idx in sorted(buckets_a):
        wa, wb = buckets_a[idx], buckets_b.get(idx, set())
        if not wa and not wb:
            continue
        union = wa | wb
        ratio = len(wa & wb) / len(union) if union else 1.0
        ratios.append((idx * bucket_seconds, ratio))
    if not ratios:
        return {"mean_agreement": 1.0, "worst_buckets": []}
    mean_agreement = sum(r for _, r in ratios) / len(ratios)
    worst = sorted(ratios, key=lambda x: x[1])[:20]
    return {
        "mean_agreement": mean_agreement,
        "worst_buckets": [{"time_seconds": t, "agreement": r} for t, r in worst],
    }


# --------------------------------------------------------------------------
# Serialization / report
# --------------------------------------------------------------------------

def result_to_dict(result: TranscriptionResult) -> Dict:
    return {
        "language": result.language,
        "duration": result.duration,
        "speaker_names": result.speaker_names,
        "segments": [
            {"start": s.start, "end": s.end, "text": s.text, "speaker": s.speaker}
            for s in result.segments
        ],
    }


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def run_benchmark(
    recording_path: Path, modes: List[str], model_name: str, language: str,
    n_threads: int, use_gpu: bool, out_dir: Path,
) -> Path:
    recording = detect_multitrack(recording_path)
    if recording is None:
        raise SystemExit(f"{recording_path}: not a Zoom multitrack folder "
                          "(missing recording.conf or Audio Record/)")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_dir / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    results: Dict[str, TranscriptionResult] = {}
    stats: Dict[str, RunStats] = {}
    extras: Dict[str, Dict] = {}
    skipped: Dict[str, str] = {}

    print(f"Recording: {recording.root}", file=sys.stderr)
    print(f"Tracks: {[(t.display_name, t.participant_index) for t in recording.tracks]}", file=sys.stderr)
    print(f"Model: {model_name}  Language: {language}  Threads: {n_threads}  GPU: {use_gpu}", file=sys.stderr)

    for mode in modes:
        print(f"\n=== Mode: {mode} ===", file=sys.stderr)
        if mode == "mixed":
            result, run_stats = run_mode_mixed(
                recording, model_name=model_name, language=language,
                n_threads=n_threads, use_gpu=use_gpu, mode_label=mode,
            )
        elif mode == "mixed+diarization":
            try:
                from diarizer import Diarizer
                if not Diarizer().is_available():
                    skipped[mode] = "pyannote/HF token not configured (Diarizer.is_available() == False)"
                    print(f"  skipped: {skipped[mode]}", file=sys.stderr)
                    continue
            except Exception as exc:
                skipped[mode] = f"diarizer import failed: {exc}"
                print(f"  skipped: {skipped[mode]}", file=sys.stderr)
                continue
            result, run_stats = run_mode_mixed(
                recording, model_name=model_name, language=language,
                n_threads=n_threads, use_gpu=use_gpu, enable_diarization=True,
                num_speakers=len(recording.tracks) or None, mode_label=mode,
            )
        elif mode == "multitrack":
            result, run_stats, extra = run_mode_multitrack(
                recording, model_name=model_name, language=language,
                n_threads=n_threads, use_gpu=use_gpu, apply_vad=True, apply_bleed=True,
                mode_label=mode,
            )
            extras[mode] = extra
        elif mode == "multitrack-naive":
            result, run_stats, extra = run_mode_multitrack(
                recording, model_name=model_name, language=language,
                n_threads=n_threads, use_gpu=use_gpu, apply_vad=False, apply_bleed=False,
                mode_label=mode,
            )
            extras[mode] = extra
        else:
            raise SystemExit(f"unknown mode: {mode}")

        results[mode] = result
        stats[mode] = run_stats
        (run_dir / f"{mode.replace('+', '_')}.json").write_text(
            json.dumps(result_to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"  wall_clock={run_stats.wall_clock_seconds:.1f}s rtf={run_stats.rtf:.3f} "
              f"segments={len(result.segments)}", file=sys.stderr)

    # ---- reference-free metrics ----
    metrics: Dict[str, Dict] = {}
    mixed_windows = None
    if "mixed" in results and recording.mixed_audio is not None:
        mixed_wav = convert_track_to_wav(recording.mixed_audio)
        try:
            mixed_windows = detect_speech_windows(mixed_wav, source="mixed")
        finally:
            Path(mixed_wav).unlink(missing_ok=True)

    reference_terms = None
    if "mixed" in results:
        reference_terms = candidate_terms(results["mixed"].full_text)

    for mode, result in results.items():
        m: Dict = {
            "loop_ratio": loop_ratio(result),
            "duplicate_ratio": duplicate_ratio(result),
            "segment_count": len(result.segments),
            "cost": asdict(stats[mode]),
        }
        m["cost"]["rtf"] = stats[mode].rtf
        if mode in extras:
            m.update(extras[mode])
        if mode in ("mixed", "mixed+diarization") and mixed_windows is not None:
            m["silence_hallucination_seconds"] = silence_hallucination_seconds(result, mixed_windows)
            m["speech_coverage_ratio"] = speech_coverage_ratio(result, mixed_windows)
        if reference_terms:
            m["term_hits"] = term_hits(result.full_text, reference_terms)
        metrics[mode] = m

    agreement = {}
    if "mixed" in results and "multitrack" in results:
        agreement["mixed_vs_multitrack"] = mode_agreement(results["mixed"], results["multitrack"])
    if "multitrack" in results and "multitrack-naive" in results:
        agreement["multitrack_vs_naive"] = mode_agreement(results["multitrack"], results["multitrack-naive"])

    metrics_payload = {
        "has_reference": False,
        "run": {
            "timestamp_utc": ts,
            "git_commit": git_commit(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "model": model_name,
            "language": language,
            "n_threads": n_threads,
            "use_gpu": use_gpu,
            "recording_root": str(recording.root),
            "modes_run": list(results.keys()),
            "modes_skipped": skipped,
        },
        "candidate_terms": reference_terms,
        "per_mode": metrics,
        "agreement": agreement,
        "note": (
            "WER, speaker attribution accuracy, and overlap recall "
            "(plan §6.3.1-6.3.3) require a human-reviewed reference "
            "transcript and are not computed here. See reference_draft.md "
            "and plan §6.2/§6.5 before drawing an accept/reject conclusion."
        ),
    }
    (run_dir / "metrics.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
    )

    write_report_md(run_dir, metrics_payload, results, stats)
    if "mixed" in results:
        write_reference_draft(run_dir, results, recording)

    print(f"\nReport: {run_dir / 'report.md'}", file=sys.stderr)
    return run_dir


def write_report_md(run_dir: Path, payload: Dict, results: Dict[str, TranscriptionResult], stats: Dict[str, RunStats]) -> None:
    lines = ["# Multitrack benchmark report", ""]
    run = payload["run"]
    lines.append(f"- Timestamp (UTC): {run['timestamp_utc']}")
    lines.append(f"- Git commit: {run['git_commit']}")
    lines.append(f"- Platform: {run['platform']} / {run['processor']}")
    lines.append(f"- Model: {run['model']}  Language: {run['language']}  Threads: {run['n_threads']}  GPU: {run['use_gpu']}")
    lines.append(f"- Recording: {run['recording_root']}")
    if run["modes_skipped"]:
        lines.append(f"- Skipped modes: {run['modes_skipped']}")
    lines.append("")
    lines.append("**`has_reference: false`** — WER, speaker attribution accuracy, and overlap "
                  "recall (plan §6.3.1-6.3.3) are not computed in this report. See "
                  "reference_draft.md; a human needs to correct it before those metrics exist.")
    lines.append("")

    lines.append("## Reference-free metrics (§6.3.4, 6.3.5, 6.3.7)")
    lines.append("")
    lines.append("| mode | segments | RTF | wall clock (s) | audio fed to ASR (s) | whisper calls | peak RSS (MB) | loop_ratio | duplicate_ratio | silence_halluc (s) | coverage |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for mode, m in payload["per_mode"].items():
        cost = m["cost"]
        lines.append(
            f"| {mode} | {m['segment_count']} | {cost['rtf']:.3f} | {cost['wall_clock_seconds']:.1f} | "
            f"{cost['audio_fed_to_asr_seconds']:.1f} | {cost['whisper_invocations']} | "
            f"{cost['peak_rss_mb']:.0f} | {m['loop_ratio']:.3f} | {m['duplicate_ratio']:.3f} | "
            f"{m.get('silence_hallucination_seconds', '—')} | {m.get('speech_coverage_ratio', '—')} |"
        )
    lines.append("")

    for mode, m in payload["per_mode"].items():
        if "bleed_suppressed_seconds" in m:
            lines.append(f"- {mode}: bleed_suppressed_seconds = {m['bleed_suppressed_seconds']:.1f}")
    lines.append("")

    if payload.get("candidate_terms"):
        lines.append("## Candidate terms (§6.3.6, NOT human-confirmed)")
        lines.append("")
        lines.append("Auto-extracted from the mixed transcript by frequency. A human must confirm "
                      "this list before term coverage is used as an acceptance signal.")
        lines.append("")
        lines.append("| term | count in mixed |")
        lines.append("|---|---|")
        for term, count in payload["candidate_terms"]:
            lines.append(f"| {term} | {count} |")
        lines.append("")
        lines.append("| mode | term hits |")
        lines.append("|---|---|")
        for mode, m in payload["per_mode"].items():
            if "term_hits" in m:
                total_hits = sum(m["term_hits"].values())
                lines.append(f"| {mode} | {total_hits} |")
        lines.append("")

    if payload.get("agreement"):
        lines.append("## Mode agreement (§6.3.8, diagnostic only — not a quality metric)")
        lines.append("")
        for pair, data in payload["agreement"].items():
            lines.append(f"### {pair}")
            lines.append(f"- mean word-overlap agreement: {data['mean_agreement']:.3f}")
            lines.append("- worst-agreeing 30s buckets (time, agreement):")
            for b in data["worst_buckets"][:10]:
                mm, ss = divmod(int(b["time_seconds"]), 60)
                lines.append(f"  - {mm:02d}:{ss:02d} — {b['agreement']:.3f}")
            lines.append("")

    lines.append("## Manual checks still required before a verdict (plan §6.5)")
    lines.append("")
    lines.append("- [ ] Correct reference_draft.md into reference.md for the 3 chosen fragments.")
    lines.append("- [ ] Re-run WER/CER, speaker attribution accuracy, overlap recall against reference.md.")
    lines.append("- [ ] Confirm the candidate term list (or replace it).")
    lines.append("- [ ] Spot-check 20 overlap cases for the bleed-suppression decision quality (plan §5.3).")
    lines.append("- [ ] Check track alignment (plan §5.1) if this is a new recording, not the sample already verified in the plan doc.")
    lines.append("")

    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _find_dense_overlap_window(mixed: TranscriptionResult, window_seconds: float = 240.0) -> float:
    """Rough heuristic: bucket segments into window_seconds chunks, return
    the start of the chunk with the most segments (proxy for a busy,
    back-and-forth stretch of conversation worth using as a reference
    fragment)."""
    if not mixed.segments:
        return 0.0
    buckets: Counter = Counter()
    for seg in mixed.segments:
        buckets[int(seg.start // window_seconds)] += 1
    best = buckets.most_common(1)[0][0]
    return best * window_seconds


def write_reference_draft(run_dir: Path, results: Dict[str, TranscriptionResult], recording: MultiTrackRecording) -> None:
    """Best-available transcript for 3 fragments (start / busiest / end),
    with speaker labels from whichever multitrack mode ran — a starting
    point for a human to correct into reference.md (plan §6.2)."""
    best = results.get("multitrack") or results.get("mixed+diarization") or results.get("mixed")
    if best is None:
        return
    duration = best.duration
    fragment_seconds = 240.0
    busiest_start = _find_dense_overlap_window(results.get("mixed", best), fragment_seconds)
    fragments = [
        ("start", 0.0, min(fragment_seconds, duration)),
        ("busiest", busiest_start, min(busiest_start + fragment_seconds, duration)),
        ("end", max(0.0, duration - fragment_seconds), duration),
    ]

    lines = [
        "# Reference draft — NOT reviewed by a human yet",
        "",
        "Best-available machine transcript for 3 four-minute fragments, per plan §6.2. "
        "A human must listen and correct this (text AND speaker labels) before it can "
        "be used as reference.md for WER/attribution/overlap-recall metrics.",
        "",
    ]
    for label, start, end in fragments:
        lines.append(f"## Fragment: {label} ({_fmt_ts(start)} - {_fmt_ts(end)})")
        lines.append("")
        for seg in best.segments:
            if seg.start < start or seg.start >= end:
                continue
            speaker = best.speaker_label(seg.speaker) or ""
            prefix = f"{speaker}: " if speaker else ""
            lines.append(f"[{_fmt_ts(seg.start)}] {prefix}{seg.text.strip()}")
        lines.append("")

    (run_dir / "reference_draft.md").write_text("\n".join(lines), encoding="utf-8")


def _fmt_ts(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recording", type=Path, help="Path to a Zoom multitrack recording folder")
    parser.add_argument("--modes", default="mixed,multitrack,multitrack-naive",
                         help=f"Comma-separated subset of {ALL_MODES}")
    parser.add_argument("--model", default="large-v3-turbo-q5_0")
    parser.add_argument("--language", default="ru")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--no-gpu", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "output" / "bench" / "multitrack")
    args = parser.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    for m in modes:
        if m not in ALL_MODES:
            parser.error(f"unknown mode {m!r}, must be one of {ALL_MODES}")

    run_benchmark(
        args.recording, modes, args.model, args.language,
        args.threads, not args.no_gpu, args.out_dir,
    )


if __name__ == "__main__":
    main()
