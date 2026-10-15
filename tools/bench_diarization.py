#!/usr/bin/env python3
"""Measure diarizer.py against Zoom's per-participant tracks.

A Zoom recording with "Record a separate audio file of each participant"
has the mixed call (``audio*.m4a`` next to the video) and one track per
person in ``Audio Record/``. The tracks give the reference: a 100 ms frame
belongs to the participant whose track is above its own noise floor and
clearly (10 dB) louder than the others' (the quieter copy is bleed).
The mixed file goes through ``Diarizer`` exactly as the app runs it.

Metric: frame-level DER without a collar — missed speech + false alarm +
speaker confusion, as shares of reference speech, after the best mapping of
found speakers onto participants. Frames where two participants talk at
once are left out (one label per frame on each side).

``--synthetic N`` builds an N-speaker conversation instead (all calls are
two-person): the non-host track of N different recordings, their solo
speech cut into turns of a few seconds and interleaved at random.

Usage:
    .venv/bin/python tools/bench_diarization.py ~/Documents/Zoom/<call> [...]
    .venv/bin/python tools/bench_diarization.py --synthetic 3 ~/Documents/Zoom/<call> [...]
    add --num-speakers N to give the count instead of detecting it

Measured 2026-10-15 on an M-series Mac, speaker count detected (not given):

    two-person Zoom calls        min   DER    miss   fa     conf   found  time
    2026-10-06                   40.8  0.059  0.019  0.037  0.003  2/2     93s
    2026-10-08                   44.8  0.083  0.019  0.061  0.002  2/2    152s
    Эфир Рома Вайбкодинг         39.3  0.052  0.021  0.028  0.003  2/2     95s
    Интервью Рома Атри Creatio   48.3  0.086  0.038  0.045  0.003  2/2    169s
    Кастдев Clarus Роман Атри    38.4  0.101  0.014  0.061  0.026  2/2    137s
    synthetic, 3 voices          15.0  0.086  0.023  0.019  0.045  3/3
    synthetic, 4 voices          15.0  0.114  0.020  0.024  0.071  4/4
    synthetic, 5 voices          15.1  0.122  0.020  0.028  0.075  5/5

Most of miss/fa is the energy reference itself (laughs, breaths, bleed), so
conf is the number to watch. The synthetic mixes switch voices every 3-15 s
with 0.3-1 s pauses — harder than a real call, where a 6-second piece rarely
spans two people. 2026-10-10 is not in the table: the guest's own track is
silent (-83 dB at p90) although they are audible in the mix, so it has no
valid reference.

Tried and dropped: sherpa-onnx's own clustering with the count given
(conf 0.23 on 2026-10-06) or a distance threshold (one voice split into
3-36 clusters); the WeSpeaker ResNet34 embedding (2026-10-08: within-voice
similarity 0.82-0.85 vs 0.79 between the two voices, so neither the
clustering nor the count works); merging clusters by averaged embeddings.
"""

from __future__ import annotations

import argparse
import itertools
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from diarizer import SAMPLE_RATE, Diarizer  # noqa: E402

HOP = 0.1  # seconds per scoring frame
SILENCE, OVERLAP = -1, -2


def load(path: Path) -> np.ndarray:
    """Any media file → 16 kHz mono float32."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", str(path), "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def frame_db(x: np.ndarray) -> np.ndarray:
    n = int(SAMPLE_RATE * HOP)
    k = len(x) // n
    energy = (x[: k * n].reshape(k, n) ** 2).mean(axis=1)
    return 10 * np.log10(energy + 1e-10)


def reference(tracks: Sequence[np.ndarray]) -> np.ndarray:
    """Per-frame participant index, SILENCE or OVERLAP."""
    dbs = [frame_db(t) for t in tracks]
    k = min(len(d) for d in dbs)
    kernel = np.ones(5) / 5  # 0.5 s, so a pause inside a word doesn't flip the label
    db = np.stack([np.convolve(d[:k], kernel, mode="same") for d in dbs])
    floor = np.percentile(db, 20, axis=1, keepdims=True)
    active = db > np.maximum(floor + 15, -50)
    ref = np.full(k, SILENCE)
    for t in range(k):
        on = np.where(active[:, t])[0]
        if len(on) == 1:
            ref[t] = on[0]
        elif len(on) > 1:
            loud = on[np.argsort(db[on, t])[::-1]]
            ref[t] = loud[0] if db[loud[0], t] - db[loud[1], t] > 10 else OVERLAP
    return ref


def hypothesis(segments, k: int) -> np.ndarray:
    names: Dict[str, int] = {}
    hyp = np.full(k, SILENCE)
    for seg in segments:
        idx = names.setdefault(seg.speaker, len(names))
        hyp[int(round(seg.start / HOP)):min(int(round(seg.end / HOP)), k)] = idx
    return hyp


def score(ref: np.ndarray, hyp: np.ndarray) -> Dict[str, float]:
    keep = ref != OVERLAP
    ref, hyp = ref[keep], hyp[keep]
    speech = ref >= 0
    total = max(1, int(speech.sum()))
    miss = int((speech & (hyp < 0)).sum())
    false_alarm = int((~speech & (hyp >= 0)).sum())
    rs = sorted(set(ref[speech].tolist()))
    hs = sorted(set(hyp[hyp >= 0].tolist()))
    if len(hs) >= len(rs):
        mappings = [list(zip(rs, p)) for p in itertools.permutations(hs, len(rs))]
    else:
        mappings = [list(zip(p, hs)) for p in itertools.permutations(rs, len(hs))]
    correct = max((sum(int(((ref == r) & (hyp == h)).sum()) for r, h in m) for m in mappings),
                  default=0)
    confusion = int((speech & (hyp >= 0)).sum()) - correct
    return {
        "der": (miss + false_alarm + confusion) / total,
        "miss": miss / total, "fa": false_alarm / total, "conf": confusion / total,
        "found": len(hs), "speakers": len(rs),
    }


def participant_tracks(folder: Path) -> List[Path]:
    tracks = sorted((folder / "Audio Record").glob("*.m4a"))
    if not tracks:
        raise SystemExit(f"{folder}: no Audio Record/*.m4a")
    return tracks


def recording(folder: Path) -> Tuple[Path, List[Path]]:
    mixed = sorted(folder.glob("audio*.m4a")) or sorted(folder.glob("*.mp4"))
    if not mixed:
        raise SystemExit(f"{folder}: needs the mixed audio*.m4a or *.mp4")
    return mixed[0], participant_tracks(folder)


def synthetic(folders: Sequence[Path], n: int, minutes: float, seed: int = 0):
    """An n-speaker mix from the guest track of n recordings (the host is
    the same person in all of them, so only one host track is used)."""
    rng = np.random.default_rng(seed)
    per_folder = [participant_tracks(f) for f in folders[:n]]
    # Zoom names a track audio<Name><n><id>.m4a. The host is whoever
    # appears in every recording; each recording gives its other voice.
    def person(track: Path) -> str:
        return re.sub(r"\d+$", "", track.stem[len("audio"):])
    common = set.intersection(*({person(t) for t in tracks} for tracks in per_folder))
    voices: List[np.ndarray] = []
    for tracks in per_folder:
        guests = [t for t in tracks if person(t) not in common] or tracks
        voices.append(load(guests[0]))
    if len(voices) < n:
        raise SystemExit(f"--synthetic {n} needs {n} recordings")
    solo: List[List[Tuple[int, int]]] = []
    for v in voices:
        db = frame_db(v)
        on = db > max(np.percentile(db, 20) + 15, -50)
        runs, start = [], None
        for t, flag in enumerate(np.append(on, False)):
            if flag and start is None:
                start = t
            elif not flag and start is not None:
                if t - start >= 15:  # at least 1.5 s
                    runs.append((start, t))
                start = None
        solo.append(runs)
    hop = int(SAMPLE_RATE * HOP)
    out: List[np.ndarray] = []
    ref: List[int] = []
    cursor = [0] * n
    last = -1
    while len(ref) * HOP < minutes * 60:
        left = [s for s in range(n) if s != last and cursor[s] < len(solo[s])]
        if not left:
            break
        who = int(rng.choice(left))
        last = who
        a, b = solo[who][cursor[who]]
        cursor[who] += 1
        b = min(b, a + int(rng.uniform(3, 15) / HOP))
        out.append(voices[who][a * hop:b * hop])
        ref.extend([who] * (b - a))
        gap = int(rng.uniform(0.3, 1.0) / HOP)
        out.append(np.zeros(gap * hop, dtype=np.float32))
        ref.extend([SILENCE] * gap)
    return np.concatenate(out), np.asarray(ref)


def write_wav(path: str, samples: np.ndarray) -> None:
    import wave

    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())


def run(label: str, audio: str, ref: np.ndarray, num_speakers) -> None:
    started = time.monotonic()
    result = Diarizer().diarize(audio, num_speakers=num_speakers)
    elapsed = time.monotonic() - started
    s = score(ref, hypothesis(result.segments, len(ref)))
    print(f"{label[:34]:34s} {len(ref) * HOP / 60:5.1f}m  DER {s['der']:.3f}  "
          f"miss {s['miss']:.3f}  fa {s['fa']:.3f}  conf {s['conf']:.3f}  "
          f"found {s['found']}/{s['speakers']}  {elapsed:5.0f}s", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folders", nargs="+", type=Path)
    parser.add_argument("--num-speakers", type=int, default=None)
    parser.add_argument("--synthetic", type=int, default=0)
    parser.add_argument("--minutes", type=float, default=15.0)
    args = parser.parse_args()

    if args.synthetic:
        mix, ref = synthetic(args.folders, args.synthetic, args.minutes)
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            write_wav(tmp.name, mix)
            run(f"synthetic, {args.synthetic} voices", tmp.name, ref, args.num_speakers)
        return
    for folder in args.folders:
        mixed, tracks = recording(folder)
        ref = reference([load(t) for t in tracks])
        run(folder.name, str(mixed), ref, args.num_speakers)


if __name__ == "__main__":
    main()
