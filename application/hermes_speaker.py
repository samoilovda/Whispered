"""Which diarized speaker is the user — matched by voice, for the Hermes push.

The user enrolls once from a record where they know their label
(``tools/hermes_push.py --enroll <folder> "Speaker 1"``): the average
speaker embedding of their speech there becomes ``voice_profile.json``.
For every record sent afterwards each speaker's average embedding is
compared with that profile (cosine); a speaker is "me" only when the
best score clears ``MATCH_MIN`` *and* beats the runner-up by ``MARGIN`` —
otherwise nobody is marked and the reason is sent instead of a guess.

Qt-free and model-free: the caller passes ``embed`` (seconds windows →
unit vectors), which ``tools/hermes_push.py`` builds on ``diarizer``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence

import numpy as np

from application.hermes_push import SPEAKERS_JSON, SPEAKERS_MD

if TYPE_CHECKING:
    from application.hermes_push import Annotate, PushItem

# Measured on a 44-minute two-person Zoom record (titanet-small): the same
# voice in the other half of the talk scored 0.96, the guest 0.40.
MATCH_MIN = 0.70
MARGIN = 0.15
# Bumped when the matching changes, so already-sent records are re-marked.
MATCH_VERSION = 1

Window = tuple[float, float]
Embed = Callable[[Sequence[Window]], list[np.ndarray]]


@dataclass(frozen=True)
class VoiceProfile:
    embedding: np.ndarray
    model: str          # embedding model file name; another model = no match
    source: str = ""    # "<folder> / <speaker>" it was enrolled from

    def fingerprint(self) -> str:
        h = hashlib.sha256(self.model.encode("utf-8"))
        h.update(np.asarray(self.embedding, dtype=np.float32).tobytes())
        return f"{MATCH_VERSION}:{h.hexdigest()[:16]}"


def save_profile(path: Path, profile: VoiceProfile) -> None:
    path.write_text(json.dumps({
        "model": profile.model,
        "source": profile.source,
        "embedding": [round(float(x), 6) for x in profile.embedding],
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def load_profile(path: Path) -> Optional[VoiceProfile]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        vec = np.asarray(data["embedding"], dtype=np.float32)
        return VoiceProfile(_unit(vec), str(data["model"]), str(data.get("source", "")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _unit(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def speaker_windows(
    segments: Sequence[dict[str, Any]], speaker_id: str, *,
    limit: int = 20, min_len: float = 2.0, trim: float = 0.25,
) -> list[Window]:
    """Up to *limit* of the speaker's segments, spread over the record and
    trimmed at both ends so a neighbour's voice at a turn change stays out."""
    spans = [
        (float(s["start"]) + trim, float(s["end"]) - trim)
        for s in segments
        if s.get("speaker_id") == speaker_id
        and float(s["end"]) - float(s["start"]) >= min_len
    ]
    if len(spans) <= limit:
        return spans
    step = len(spans) / limit
    return [spans[int(i * step)] for i in range(limit)]


def centroid(vectors: Sequence[np.ndarray]) -> Optional[np.ndarray]:
    if not vectors:
        return None
    return _unit(np.mean(np.stack(vectors), axis=0))


@dataclass(frozen=True)
class SpeakerMatch:
    speaker_id: Optional[str]
    score: float = 0.0
    runner_up: float = 0.0
    reason: str = ""

    def as_dict(self, name: str) -> dict[str, Any]:
        if self.speaker_id is None:
            return {"speaker_id": None, "name": name, "reason": self.reason,
                    "score": round(self.score, 3), "runner_up": round(self.runner_up, 3)}
        return {"speaker_id": self.speaker_id, "name": name, "method": "voice",
                "score": round(self.score, 3), "runner_up": round(self.runner_up, 3)}


def match(centroids: dict[str, np.ndarray], profile: np.ndarray) -> SpeakerMatch:
    """The speaker whose voice is the profile's — or nobody, with a reason."""
    if not centroids:
        return SpeakerMatch(None, reason="no speech long enough to compare")
    scores = sorted(
        ((float(vec @ profile), sid) for sid, vec in centroids.items()), reverse=True,
    )
    best, sid = scores[0]
    second = scores[1][0] if len(scores) > 1 else 0.0
    if best < MATCH_MIN:
        return SpeakerMatch(None, best, second, "no speaker sounds like the profile")
    if best - second < MARGIN:
        return SpeakerMatch(None, best, second, "two speakers sound alike")
    return SpeakerMatch(sid, best, second)


def identify(
    speakers: dict[str, Any], profile: VoiceProfile, embed: Embed,
) -> SpeakerMatch:
    """Match every speaker of a ``transcript.speakers.json`` payload."""
    segments = speakers.get("segments") or []
    ids = sorted({str(s["speaker_id"]) for s in segments if s.get("speaker_id")})
    centroids = {}
    for sid in ids:
        vec = centroid(embed(speaker_windows(segments, sid)))
        if vec is not None:
            centroids[sid] = vec
    return match(centroids, profile.embedding)


def mark_speakers_json(speakers: dict[str, Any], me: dict[str, Any]) -> dict[str, Any]:
    """A copy with "me" recorded and the user's turns named and flagged."""
    out = dict(speakers)
    out["me"] = me
    sid = me.get("speaker_id")
    if sid is None:
        return out
    names = dict(out.get("speaker_names") or {})
    names[sid] = me["name"]
    out["speaker_names"] = names
    out["segments"] = [
        {**s, "speaker": me["name"], "is_me": True} if s.get("speaker_id") == sid
        else {**s, "is_me": False}
        for s in speakers.get("segments") or []
    ]
    return out


def mark_speakers_md(text: str, old_label: str, name: str) -> str:
    """``**Speaker 1**:`` → ``**Денис (я)**:`` in the readable transcript."""
    return text.replace(f"**{old_label}**:", f"**{name} (я)**:")


def source_path(folder: Path) -> Optional[Path]:
    """The record's media file, as the artifact manifests remember it."""
    for manifest in sorted(folder.glob("*.manifest.json")):
        try:
            src = json.loads(manifest.read_text(encoding="utf-8")).get("source_path")
        except (OSError, ValueError, AttributeError):
            continue
        if src and Path(src).is_file():
            return Path(src)
    return None


def annotator(
    profile: Optional[VoiceProfile], name: str, model: str,
    embedder: Callable[[Path], Embed],
) -> "Annotate":
    """``run_push``'s *annotate*: mark the user's speaker in the staged
    ``transcript.speakers.json``/``.md`` and report it as ready.json's
    ``me``. *embedder* turns the record's media file into an ``Embed``;
    *model* is the embedding model the profile must have been made with."""

    def annotate(item: "PushItem", stage: Path) -> dict[str, Any]:
        spk_json = stage / SPEAKERS_JSON
        if not spk_json.is_file():
            return {"me": {"speaker_id": None, "name": name,
                           "reason": "record has no speaker transcript"}}
        speakers = json.loads(spk_json.read_text(encoding="utf-8"))
        if profile is None:
            found = SpeakerMatch(None, reason="no voice profile (tools/hermes_push.py --enroll)")
        elif profile.model != model:
            found = SpeakerMatch(None, reason="voice profile made with another model — enroll again")
        else:
            src = source_path(item.artifact_dir)
            found = (identify(speakers, profile, embedder(src)) if src
                     else SpeakerMatch(None, reason="source media not found"))
        me = found.as_dict(name)
        spk_json.write_text(json.dumps(mark_speakers_json(speakers, me),
                                       ensure_ascii=False, indent=2), encoding="utf-8")
        spk_md = stage / SPEAKERS_MD
        if found.speaker_id is not None and spk_md.is_file():
            old = (speakers.get("speaker_names") or {}).get(found.speaker_id, found.speaker_id)
            spk_md.write_text(mark_speakers_md(spk_md.read_text(encoding="utf-8"), old, name),
                              encoding="utf-8")
        return {"me": me}

    return annotate
