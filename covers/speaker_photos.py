"""Ready-made speaker photo candidates from a video-call recording.

For each participant of a Zoom-style gallery recording, pick a few good
stills — so the cover's photo slots are filled by choosing among
prepared variants instead of scrubbing the video by hand:

1. sample frames across the recording (a new round samples new moments,
   never the ones already shown),
2. split every frame into participant tiles (``covers.tiles.speaker_tiles``),
3. rate each participant's picture: Apple Vision's face capture quality
   where available (``covers.face_vision``), plain sharpness otherwise,
4. keep the best few per participant, spread out over the recording,
5. crop them to the tile and work out the slot framing (focus and zoom)
   that puts the face at the same size and height in every slot.

The result is cached next to the record (``cover_candidates/``) so the
workspace shows the same variants until new ones are requested.

Qt-free: frames come from FFmpeg as PNG files plus raw RGB for numpy.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np

from core.external_tools import resolve_tool
from core.logger import get_logger
from covers import face_vision
from covers.face_vision import Face
from covers.tiles import Rect, _sharpness, speaker_tiles

logger = get_logger(__name__)

CANDIDATE_DIR = "cover_candidates"
_INDEX_FILE = "candidates.json"
PER_PARTICIPANT = 4
# Frames sampled per round: enough to find a few good moments for each
# participant, few enough that a round takes seconds.
FRAMES_PER_ROUND = 24
# Where the face goes in a photo slot: its height as a share of the slot,
# and its centre's height from the slot's top.
FACE_SHARE = 0.45
FACE_Y = 0.42
_MAX_ZOOM = 2.5          # covers.renderer.MAX_PHOTO_ZOOM
_GOLDEN = (math.sqrt(5) - 1) / 2
_FFMPEG_TIMEOUT_S = 30


@dataclass(frozen=True)
class PhotoCandidate:
    """One participant's still: *path* is the tile crop, *face* its face
    as fractions of that crop (``None`` without a face detector)."""

    participant: int
    time: float
    path: str
    score: float
    face: Optional[Face] = None


@dataclass
class CandidateSet:
    """What the workspace shows: the current variants per participant and
    every moment already sampled (so "new variants" never repeat one)."""

    video: str
    candidates: list[PhotoCandidate] = field(default_factory=list)
    sampled: list[float] = field(default_factory=list)
    rounds: int = 0

    def for_participant(self, participant: int) -> list[PhotoCandidate]:
        return [c for c in self.candidates if c.participant == participant]

    @property
    def participants(self) -> list[int]:
        return sorted({c.participant for c in self.candidates})


# ── Framing ────────────────────────────────────────────────────────────


def framing_for(
    face: Optional[Face],
    slot_size: tuple[float, float],
    image_size: tuple[float, float],
    face_share: float = FACE_SHARE,
    face_y: float = FACE_Y,
) -> tuple[tuple[float, float], float]:
    """``(focus, zoom)`` for covers.renderer's cover-fit photo drawing so
    *face* fills *face_share* of the slot height with its centre at
    *face_y* (and centred across). Without a face: plain centring."""
    if face is None or face.h <= 0:
        return (0.5, 0.5), 1.0
    slot_w, slot_h = slot_size
    image_w, image_h = image_size
    base = max(slot_w / image_w, slot_h / image_h)
    zoom = min(_MAX_ZOOM, max(1.0, face_share * slot_h / (face.h * image_h * base)))
    # Rendered image size in slot units; the renderer aligns image point
    # f with slot point f, so the face centre c lands at f*(1-t) + c*t.
    scale_x = image_w * base * zoom / slot_w
    scale_y = image_h * base * zoom / slot_h
    cx, cy = face.center

    def axis(centre: float, target: float, scale: float) -> float:
        if abs(scale - 1.0) < 1e-6:
            return 0.5
        return min(1.0, max(0.0, (centre * scale - target) / (scale - 1.0)))

    return (round(axis(cx, 0.5, scale_x), 4), round(axis(cy, face_y, scale_y), 4)), round(zoom, 4)


def slot_size(template, layout: str, slot: str) -> Optional[tuple[float, float]]:
    """A photo slot's box size in *layout* of *template*, if it has one."""
    spec = template.layouts.get(layout) if hasattr(template.layouts, "get") else None
    if spec is None:
        return None
    for layer in spec.layers:
        data = getattr(layer, "data", {}) or {}
        if getattr(layer, "type", "") == "photo" and data.get("slot") == slot:
            box = data.get("box") or []
            if len(box) == 4:
                return float(box[2]), float(box[3])
    return None


# ── Sampling ───────────────────────────────────────────────────────────


def sample_times(
    duration: float, count: int, round_index: int, taken: Iterable[float] = (),
) -> list[float]:
    """*count* moments spread over the recording (skipping the first and
    last 3%, where people join and say goodbye). Each round shifts the
    grid by the golden ratio, so new rounds land between earlier ones; a
    moment closer than a third of the step to one already taken is
    skipped."""
    if duration <= 0 or count <= 0:
        return []
    start, end = duration * 0.03, duration * 0.97
    step = (end - start) / count
    offset = (0.5 + round_index * _GOLDEN) % 1.0
    taken = sorted(taken)
    times = []
    for index in range(count):
        moment = start + (index + offset) * step
        if any(abs(moment - other) < step / 3 for other in taken):
            continue
        times.append(round(moment, 2))
    return times


def _video_size(video: str) -> tuple[int, int]:
    ffprobe = resolve_tool("ffprobe")
    if not ffprobe:
        raise RuntimeError("FFprobe is not installed")
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", video],
        capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_S, check=True,
    ).stdout.strip()
    width, height = out.split("x")[:2]
    return int(width), int(height)


def _grab(ffmpeg: str, video: str, moment: float, png: Path, size: tuple[int, int]) -> np.ndarray:
    """Decode one frame once into *png* (for Vision) and an RGB array."""
    raw = png.with_suffix(".rgb")
    subprocess.run(
        [ffmpeg, "-v", "error", "-ss", f"{moment:.3f}", "-i", video,
         "-frames:v", "1", "-y", str(png),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-y", str(raw)],
        capture_output=True, timeout=_FFMPEG_TIMEOUT_S, check=True,
    )
    width, height = size
    data = np.frombuffer(raw.read_bytes(), dtype=np.uint8)
    raw.unlink(missing_ok=True)
    return data[: width * height * 3].reshape(height, width, 3)


def _crop(ffmpeg: str, source: Path, rect: Rect, target: Path) -> None:
    subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(source),
         "-vf", f"crop={rect.w}:{rect.h}:{rect.x}:{rect.y}", "-y", str(target)],
        capture_output=True, timeout=_FFMPEG_TIMEOUT_S, check=True,
    )


@dataclass
class _Shot:
    time: float
    png: Path
    tiles: list[Rect]
    sharpness: list[float]
    faces: list[Optional[Face]] = field(default_factory=list)


def _face_in_tile(faces: list[Face], tile: Rect, size: tuple[int, int]) -> Optional[Face]:
    """The largest face whose centre lies in *tile*, re-expressed as
    fractions of the tile."""
    width, height = size
    inside = []
    for face in faces:
        cx, cy = face.center[0] * width, face.center[1] * height
        if tile.x <= cx <= tile.x + tile.w and tile.y <= cy <= tile.y + tile.h:
            inside.append(face)
    if not inside:
        return None
    best = max(inside, key=lambda f: f.w * f.h)
    return Face(
        (best.x * width - tile.x) / tile.w, (best.y * height - tile.y) / tile.h,
        best.w * width / tile.w, best.h * height / tile.h, best.quality,
    )


def split_by_faces(tiles: list[Rect], faces: list[Face], size: tuple[int, int]) -> list[Rect]:
    """Split a tile holding several faces into equal cells, one per face.

    Zoom's gallery puts equal tiles edge to edge; when their backgrounds
    meet without a flat seam (a pale wall next to a textured one),
    ``speaker_tiles`` sees one tile — but Vision still finds a face per
    participant. Faces spread sideways split the tile into columns,
    otherwise into rows."""
    width, height = size
    result: list[Rect] = []
    for tile in tiles:
        inside = [
            face for face in faces
            if tile.x <= face.center[0] * width <= tile.x + tile.w
            and tile.y <= face.center[1] * height <= tile.y + tile.h
        ]
        count = len(inside)
        if count < 2:
            result.append(tile)
            continue
        xs = [face.center[0] * width for face in inside]
        ys = [face.center[1] * height for face in inside]
        if max(xs) - min(xs) >= max(ys) - min(ys):
            step = tile.w // count
            result.extend(Rect(tile.x + i * step, tile.y, step, tile.h) for i in range(count))
        else:
            step = tile.h // count
            result.extend(Rect(tile.x, tile.y + i * step, tile.w, step) for i in range(count))
    return sorted(result, key=lambda rect: (rect.y // max(1, height // 4), rect.x))


def _pick(
    scored: list[tuple[float, float, int, int]], count: int, min_gap: float,
) -> list[tuple[float, float, int, int]]:
    """Best *count* of ``(score, time, shot, tile)``, at least *min_gap*
    seconds apart, so variants are different moments, not one smile."""
    chosen: list[tuple[float, float, int, int]] = []
    for item in sorted(scored, key=lambda s: -s[0]):
        if all(abs(item[1] - other[1]) >= min_gap for other in chosen):
            chosen.append(item)
        if len(chosen) == count:
            break
    return sorted(chosen, key=lambda s: -s[0])


def find_candidates(
    video: str | Path,
    out_dir: str | Path,
    *,
    duration: float,
    per_participant: int = PER_PARTICIPANT,
    frames: int = FRAMES_PER_ROUND,
    previous: Optional[CandidateSet] = None,
    cancel: Callable[[], bool] = lambda: False,
    progress: Optional[Callable[[int], None]] = None,
) -> CandidateSet:
    """A fresh set of variants: a new round of moments (after *previous*'s),
    rated and cropped into *out_dir*. Earlier variants' files are removed
    — a photo already put on the cover lives in the record's
    ``cover_photos/`` (application/cover_setup.py), not here."""
    video = str(video)
    out = Path(out_dir)
    ffmpeg = resolve_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is not installed")
    rounds = previous.rounds if previous and previous.video == video else 0
    sampled = list(previous.sampled) if previous and previous.video == video else []
    times = sample_times(duration, frames, rounds, sampled)
    if not times:  # every moment of a short video has been shown: start over
        rounds, sampled = 0, []
        times = sample_times(duration, frames, rounds)
    size = _video_size(video)
    work = Path(tempfile.mkdtemp(prefix="whispered-speakers-"))
    try:
        shots: list[_Shot] = []
        grays: list[np.ndarray] = []
        for index, moment in enumerate(times):
            if cancel():
                return previous or CandidateSet(video=video)
            png = work / f"frame-{index:02d}.png"
            try:
                rgb = _grab(ffmpeg, video, moment, png, size)
            except (subprocess.SubprocessError, OSError, ValueError) as exc:
                logger.warning("Could not read a frame at %.1fs: %s", moment, exc)
                continue
            grays.append(rgb.astype(np.float32).mean(axis=2))
            shots.append(_Shot(moment, png, speaker_tiles(rgb), []))
            if progress:
                progress(round((index + 1) / len(times) * 80))
        if not shots:
            raise RuntimeError("No frames could be read from the video")
        vision = face_vision.analyse_faces(shot.png for shot in shots)
        for shot, gray in zip(shots, grays):
            if vision is not None:
                shot.tiles = split_by_faces(shot.tiles, vision.get(str(shot.png), []), size)
            shot.sharpness = [
                _sharpness(gray[t.y:t.y + t.h, t.x:t.x + t.w]) for t in shot.tiles
            ]
        # Participants are tile positions; keep the frames showing the
        # usual number of tiles (a screen share or a solo view differs).
        usual = Counter(len(shot.tiles) for shot in shots).most_common(1)[0][0]
        shots = [shot for shot in shots if len(shot.tiles) == usual]
        scored: dict[int, list[tuple[float, float, int, int]]] = {}
        for shot_index, shot in enumerate(shots):
            faces = vision.get(str(shot.png), []) if vision is not None else []
            shot.faces = [_face_in_tile(faces, tile, size) for tile in shot.tiles]
            top = max(shot.sharpness) or 1.0
            for tile_index, face in enumerate(shot.faces):
                if vision is not None:
                    if face is None:
                        continue  # looking away, or out of the picture
                    score = face.quality
                else:
                    score = shot.sharpness[tile_index] / top
                scored.setdefault(tile_index, []).append(
                    (score, shot.time, shot_index, tile_index)
                )
        span = duration * 0.94
        min_gap = span / (per_participant * 3) if span > 0 else 0.0
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)
        candidates = []
        for participant in sorted(scored):
            for score, moment, shot_index, tile_index in _pick(
                scored[participant], per_participant, min_gap
            ):
                shot = shots[shot_index]
                target = out / f"p{participant}-{moment:08.2f}.png"
                _crop(ffmpeg, shot.png, shot.tiles[tile_index], target)
                candidates.append(PhotoCandidate(
                    participant, moment, str(target), round(score, 4),
                    shot.faces[tile_index],
                ))
        if progress:
            progress(100)
        result = CandidateSet(
            video=video, candidates=candidates,
            sampled=sorted(sampled + times), rounds=rounds + 1,
        )
        save_candidates(out, result)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ── Cache ──────────────────────────────────────────────────────────────


def save_candidates(out_dir: str | Path, result: CandidateSet) -> None:
    data = {
        "video": result.video,
        "rounds": result.rounds,
        "sampled": result.sampled,
        "candidates": [
            {**asdict(c), "face": asdict(c.face) if c.face else None}
            for c in result.candidates
        ],
    }
    path = Path(out_dir) / _INDEX_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_candidates(out_dir: str | Path, video: str | Path) -> Optional[CandidateSet]:
    """The cached variants for *video*, or ``None`` when there are none,
    they were made for another video, or their files are gone."""
    path = Path(out_dir) / _INDEX_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("video") != str(video):
        return None
    candidates = []
    for raw in data.get("candidates", []):
        try:
            face = raw.get("face")
            candidate = PhotoCandidate(
                int(raw["participant"]), float(raw["time"]), str(raw["path"]),
                float(raw["score"]), Face(**face) if isinstance(face, dict) else None,
            )
        except (KeyError, TypeError, ValueError):
            continue
        if Path(candidate.path).is_file():
            candidates.append(candidate)
    if not candidates:
        return None
    return CandidateSet(
        video=str(video), candidates=candidates,
        sampled=[float(t) for t in data.get("sampled", [])],
        rounds=int(data.get("rounds", 0)),
    )
