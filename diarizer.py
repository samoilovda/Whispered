"""
Whispered - Speaker Diarization
Identify and label different speakers, fully offline.

Two passes:

1. sherpa-onnx runs pyannote's segmentation-3.0 model (who talks in each
   10-second window, overlaps included) with NVIDIA's TitaNet-small speaker
   embedding and a fine-grained clustering threshold. Its speech turns are
   good; its own clustering is not: asked for two speakers it hands one of
   them to a few seconds of outliers and glues the two real voices together
   (DER 0.29 on a 41-minute two-person Zoom call).
2. Every turn is cut into pieces of up to 6 s, each piece gets its own
   embedding, and the pieces are clustered spectrally on a pruned
   nearest-neighbour graph (NME-SC style). The number of speakers, when not
   given, is where the graph Laplacian's eigenvalues jump (same call: DER
   0.06, speaker confusion 0.3 %).

No torch, no Hugging Face account: both ONNX files (46 MB) come through
``core.model_repository`` from pinned, sha256-checked manifest entries the
first time diarization runs. ``tools/bench_diarization.py`` measures it
against Zoom's per-participant tracks and has the numbers.
"""

from __future__ import annotations

import importlib.util
import os
import wave
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.logger import get_logger
# Re-exported: callers historically imported these from here.
from domain.diarization import DiarizationResult, SpeakerSegment

__all__ = [
    "DiarizationCancelled", "DiarizationResult", "Diarizer", "SpeakerSegment",
    "MODEL_KEYS", "is_installed", "missing_models",
]

logger = get_logger(__name__)

SAMPLE_RATE = 16000
MODEL_KEYS = ("diarization-segmentation", "diarization-embedding")
MAX_SPEAKERS = 8

# sherpa-onnx's clustering distance for the first pass. Low enough that a
# first-pass cluster (and so a turn) never mixes two voices; the
# re-clustering joins the pieces of one voice back together.
_FIRST_PASS_THRESHOLD = 0.7
_PIECE_SECONDS = 6.0
_MIN_PIECE_SECONDS = 1.0
# Each piece keeps edges to this share of the others it is most similar to.
_NEIGHBOUR_SHARE = 0.1
# The speaker count is the largest k whose k-th smallest Laplacian
# eigenvalue is below this share of the (k+1)-th: every voice adds one
# near-zero eigenvalue, then the spectrum jumps. Measured with TitaNet:
# 0.00-0.03 at k=2 and >= 0.53 above on five two-person calls; 0.07-0.09
# at the true k for 3-5 voices (other k >= 0.36, or ~0 for a subset of
# the voices); 0.85 for k=2 on a single voice.
_EIGEN_RATIO = 0.25
# Fewer pieces than this cannot form a meaningful graph: one speaker.
_MIN_PIECES = 6


Turn = Tuple[float, float, int]  # (start, end, cluster id)
Piece = Tuple[int, float, float]  # (turn index, start, end)


class DiarizationCancelled(Exception):
    """The caller's ``is_cancelled`` returned True mid-run."""


def is_installed() -> bool:
    """True when the sherpa-onnx package is importable."""
    try:
        return importlib.util.find_spec("sherpa_onnx") is not None
    except (ImportError, ValueError):
        return False


def missing_models(models_dir: Optional[Path] = None) -> List[str]:
    """Manifest keys of the diarization models not yet downloaded and
    verified (size + sha256)."""
    from core.model_repository import ModelRepository

    repo = ModelRepository(models_dir)
    return [key for key in MODEL_KEYS if not repo.validate(key)]


def _default_threads() -> int:
    return max(1, min(8, (os.cpu_count() or 4) - 2))


def load_samples(audio_path: str) -> np.ndarray:
    """The recording as 16 kHz mono float32 in [-1, 1]. A 16 kHz mono
    16-bit WAV (what the transcriber already converted to) is read
    directly; anything else goes through FFmpeg first."""
    try:
        with wave.open(audio_path, "rb") as wav:
            if (wav.getframerate() == SAMPLE_RATE and wav.getnchannels() == 1
                    and wav.getsampwidth() == 2):
                raw = wav.readframes(wav.getnframes())
                return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    except (wave.Error, EOFError, OSError):
        pass  # not a plain PCM WAV — convert below

    from core.audio_convert import convert_to_wav

    tmp = convert_to_wav(audio_path)
    try:
        return load_samples(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


class Diarizer:
    """Offline speaker diarization (sherpa-onnx + spectral clustering)."""

    def __init__(self, models_dir: Optional[Path] = None, num_threads: Optional[int] = None):
        self._models_dir = models_dir
        self._threads = num_threads or _default_threads()
        self._extractor: Optional[Any] = None

    def is_available(self) -> bool:
        """True when sherpa-onnx is installed. Missing models are not a
        reason to be unavailable: ``diarize`` downloads them."""
        return is_installed()

    # ------------------------------------------------------------------ models

    def ensure_models(
        self,
        on_progress: Optional[Callable[[int, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Path]:
        """Paths of both models, downloading the missing ones first."""
        from core.model_repository import ModelRepository

        repo = ModelRepository(self._models_dir)
        paths: Dict[str, Path] = {}

        def progress(done: int, total: int) -> None:
            if on_progress and total:
                on_progress(int(100 * done / total), "Downloading speaker models...")

        for key in MODEL_KEYS:
            paths[key] = repo.ensure(key, progress=progress, cancel=is_cancelled)
        return paths

    def _engine(self, paths: Dict[str, Path], threshold: float) -> Any:
        import sherpa_onnx

        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(paths["diarization-segmentation"])),
                num_threads=self._threads,
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(paths["diarization-embedding"]), num_threads=self._threads),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=threshold),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not config.validate():
            raise RuntimeError("Invalid diarization configuration (model files?)")
        return sherpa_onnx.OfflineSpeakerDiarization(config)

    def _embed(self, paths: Dict[str, Path], samples: np.ndarray) -> np.ndarray:
        import sherpa_onnx

        if self._extractor is None:
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
                sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=str(paths["diarization-embedding"]), num_threads=self._threads))
        stream = self._extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        stream.input_finished()
        vec = np.asarray(self._extractor.compute(stream), dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec

    # ------------------------------------------------------------------ run

    def diarize(
        self,
        audio_path: str,
        num_speakers: Optional[int] = None,
        on_progress: Optional[Callable[[int, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DiarizationResult:
        """
        Who speaks when in *audio_path*.

        Args:
            audio_path: any audio/video FFmpeg reads (16 kHz mono WAV is
                read without conversion)
            num_speakers: exact number of speakers; None (or < 1) detects it
            on_progress: ``(percent, message)`` callback
            is_cancelled: polled during the run; True aborts with
                ``DiarizationCancelled``
        """
        cancelled = is_cancelled or (lambda: False)

        def report(pct: int, msg: str) -> None:
            if on_progress:
                on_progress(pct, msg)

        def check() -> None:
            if cancelled():
                raise DiarizationCancelled()

        report(0, "Loading speaker models...")
        paths = self.ensure_models(
            on_progress=lambda p, m: report(p // 10, m), is_cancelled=cancelled)
        samples = load_samples(audio_path)
        duration = len(samples) / SAMPLE_RATE
        check()

        def callback(done: int, total: int) -> int:
            if total:
                report(10 + int(70 * done / total), "Finding speaker turns...")
            return 1 if cancelled() else 0

        report(10, "Finding speaker turns...")
        raw = self._engine(paths, _FIRST_PASS_THRESHOLD).process(
            samples, callback=callback).sort_by_start_time()
        check()
        turns: List[Turn] = [(r.start, r.end, r.speaker) for r in raw]

        pieces = split_pieces(turns)
        embeddings: List[np.ndarray] = []
        for i, (_turn, start, end) in enumerate(pieces):
            if i % 20 == 0:
                check()
                report(80 + int(15 * i / max(1, len(pieces))), "Comparing speaker voices...")
            a, b = int(start * SAMPLE_RATE), int(end * SAMPLE_RATE)
            embeddings.append(self._embed(paths, samples[a:b]))

        wanted = num_speakers if num_speakers and num_speakers > 0 else None
        if embeddings:
            labels = spectral_labels(np.stack(embeddings), wanted)
            turns = relabel(turns, pieces, labels)
        result = to_result(turns, duration)
        logger.info("Diarized %s: %d speakers, %d turns, %d pieces",
                    audio_path, result.num_speakers, len(result.segments), len(pieces))
        report(100, f"Found {result.num_speakers} speakers")
        return result


# ============================================================================
# CLUSTERING (pure numpy — unit-tested without the models)
# ============================================================================

def split_pieces(turns: Sequence[Turn]) -> List[Piece]:
    """Cut every turn into pieces of at most ``_PIECE_SECONDS``; a remainder
    shorter than ``_MIN_PIECE_SECONDS`` joins the piece before it, and a
    turn shorter than that gets no piece at all."""
    pieces: List[Piece] = []
    for i, (start, end, _cid) in enumerate(turns):
        t = start
        while end - t >= _MIN_PIECE_SECONDS:
            stop = min(end, t + _PIECE_SECONDS)
            if end - stop < _MIN_PIECE_SECONDS:
                stop = end
            pieces.append((i, t, stop))
            t = stop
    return pieces


def _kmeans(x: np.ndarray, k: int, restarts: int = 10, iters: int = 100) -> np.ndarray:
    """Plain k-means with k-means++ seeding; deterministic (fixed seed)."""
    rng = np.random.default_rng(0)
    best: Optional[np.ndarray] = None
    best_inertia = np.inf
    for _ in range(restarts):
        centres = [x[rng.integers(len(x))]]
        for _j in range(1, k):
            dist = np.min(((x[:, None, :] - np.asarray(centres)[None]) ** 2).sum(-1), axis=1)
            total = float(dist.sum())
            pick = rng.choice(len(x), p=dist / total) if total > 0 else rng.integers(len(x))
            centres.append(x[pick])
        c = np.asarray(centres)
        labels = np.zeros(len(x), dtype=int)
        for _it in range(iters):
            labels = np.argmin(((x[:, None, :] - c[None]) ** 2).sum(-1), axis=1)
            moved = np.asarray([x[labels == j].mean(0) if (labels == j).any() else c[j]
                                for j in range(k)])
            if np.allclose(moved, c):
                break
            c = moved
        inertia = float(((x - c[labels]) ** 2).sum())
        if inertia < best_inertia:
            best, best_inertia = labels, inertia
    assert best is not None
    return best


def spectral_labels(embeddings: np.ndarray, num_speakers: Optional[int] = None,
                    max_speakers: int = MAX_SPEAKERS) -> np.ndarray:
    """A speaker index per row of *embeddings* (unit vectors).

    The affinity graph links each piece to the ``_NEIGHBOUR_SHARE`` of
    pieces most similar to it (binary, symmetrised), which cuts the long
    tail of weak similarities that make one voice look like several. With
    *num_speakers* None the count is the largest k whose eigenvalue ratio
    λ_k / λ_(k+1) is below ``_EIGEN_RATIO`` (λ ascending, λ_1 = 0), else 1.
    """
    n = len(embeddings)
    if n < _MIN_PIECES:
        k = min(num_speakers or 1, n)
        return np.arange(n) % max(1, k)
    sim = embeddings @ embeddings.T
    p = max(2, int(n * _NEIGHBOUR_SHARE))
    graph = np.zeros_like(sim)
    nearest = np.argsort(-sim, axis=1)[:, :p]
    graph[np.arange(n)[:, None], nearest] = 1.0
    graph = np.maximum(graph, graph.T)
    np.fill_diagonal(graph, 0.0)
    laplacian = np.diag(graph.sum(1)) - graph
    values, vectors = np.linalg.eigh(laplacian)

    if num_speakers:
        k = max(1, min(num_speakers, n))
    else:
        # eps keeps exact zeros (a graph that falls apart into separate
        # voices) from turning the ratio into 0/0.
        eps = 1e-6 * max(float(values[-1]), 1.0)
        k = 1
        for cand in range(2, min(max_speakers, n - 1) + 1):
            # λ_cand (1-based) is values[cand - 1]
            ratio = (max(float(values[cand - 1]), 0.0) + eps) / (max(float(values[cand]), 0.0) + eps)
            if ratio < _EIGEN_RATIO:
                k = cand
    if k == 1:
        return np.zeros(n, dtype=int)
    return _kmeans(vectors[:, :k], k)


def relabel(turns: Sequence[Turn], pieces: Sequence[Piece], labels: np.ndarray) -> List[Turn]:
    """New turns from piece labels. A turn whose pieces agree by at least
    two thirds keeps its span under the majority label; otherwise each
    piece becomes a turn of its own. A turn with no piece (shorter than ``_MIN_PIECE_SECONDS``) takes
    the label most of its first-pass cluster's pieces got, or, if that
    cluster has no pieces either, the label of the nearest piece in time."""
    by_cluster: Dict[int, Counter] = {}
    by_turn: Dict[int, List[Tuple[float, float, int]]] = {}
    out: List[Turn] = []
    for (i, start, end), label in zip(pieces, labels):
        by_cluster.setdefault(turns[i][2], Counter())[int(label)] += 1
        by_turn.setdefault(i, []).append((start, end, int(label)))
    for i, own in by_turn.items():
        votes = Counter(label for _s, _e, label in own)
        label, count = votes.most_common(1)[0]
        if count * 3 >= len(own) * 2:
            # A clear majority: one stray piece is noise, not a turn change.
            out.append((turns[i][0], turns[i][1], label))
        else:
            out.extend(own)
    for i, (start, end, cid) in enumerate(turns):
        if i in by_turn:
            continue
        if cid in by_cluster:
            label = by_cluster[cid].most_common(1)[0][0]
        elif pieces:
            mid = (start + end) / 2
            nearest = min(range(len(pieces)),
                          key=lambda j: abs((pieces[j][1] + pieces[j][2]) / 2 - mid))
            label = int(labels[nearest])
        else:
            label = 0
        out.append((start, end, label))
    out.sort()
    return out


def to_result(turns: Sequence[Turn], duration: float) -> DiarizationResult:
    """Turns with cluster ids → a result labelled "Speaker 1", "Speaker 2"…
    in order of first appearance; adjacent turns of one speaker separated
    by a short pause are joined."""
    names: Dict[int, str] = {}
    segments: List[SpeakerSegment] = []
    for start, end, cid in sorted(turns):
        if cid not in names:
            names[cid] = f"Speaker {len(names) + 1}"
        name = names[cid]
        last = segments[-1] if segments else None
        if last is not None and last.speaker == name and start - last.end <= 0.5:
            last.end = max(last.end, end)
        else:
            segments.append(SpeakerSegment(start=start, end=end, speaker=name))
    return DiarizationResult(segments=segments, num_speakers=len(names), duration=duration)


# ============================================================================
# CLI FOR TESTING
# ============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python diarizer.py <audio> [num_speakers]")
        print(f"sherpa-onnx installed: {is_installed()}")
        print(f"models missing: {missing_models() or 'none'}")
        sys.exit(0)
    n = int(sys.argv[2]) if len(sys.argv) > 2 else None
    res = Diarizer().diarize(sys.argv[1], num_speakers=n,
                             on_progress=lambda p, m: print(f"{p:3d}% {m}", flush=True))
    for seg in res.segments:
        print(f"{seg.start:8.1f} {seg.end:8.1f}  {seg.speaker}")
    print(f"{res.num_speakers} speakers")
