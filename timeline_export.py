"""Whispered - Timeline Export: EDL (CMX3600) generator for DaVinci Resolve.

Pure functions — no Qt, no I/O except write_edl().  Segments are duck-typed:
any object with .start and .end float attributes is accepted.
"""

from core.logger import get_logger

logger = get_logger(__name__)


def seconds_to_timecode(seconds: float, fps: int, drop_frame: bool = False) -> str:
    """Convert a time in seconds to an EDL timecode string (HH:MM:SS:FF).

    For drop-frame (29.97 / 59.94) the separator before frames is ';'.
    Drop-frame is only valid for fps 30 or 60; for other rates the flag is
    silently ignored and non-drop format is returned.
    """
    fps_int = int(round(fps))

    if drop_frame and fps_int in (30, 60):
        return _seconds_to_df_timecode(seconds, fps_int)

    if drop_frame and fps_int not in (30, 60):
        logger.warning(
            "drop_frame=True is not valid for fps=%d; using non-drop timecode", fps_int
        )

    # Non-drop path
    total_frames = int(round(seconds * fps_int))
    f = total_frames % fps_int
    total_secs = total_frames // fps_int
    s = total_secs % 60
    m = (total_secs // 60) % 60
    h = total_secs // 3600
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _seconds_to_df_timecode(seconds: float, nominal: int) -> str:
    """Standard drop-frame timecode algorithm for 29.97 (nominal=30) or 59.94 (nominal=60)."""
    real_fps = nominal * 1000.0 / 1001.0          # 29.97… or 59.94…
    drop = 2 if nominal == 30 else 4               # frames dropped per minute

    frame_number = int(round(seconds * real_fps))
    fpm = nominal * 60                             # frames per nominal minute
    fp10m = nominal * 600 - drop * 9              # frames per 10 DF minutes

    d, mrem = divmod(frame_number, fp10m)
    add = drop * 9 * d
    if mrem > drop:
        add += drop * ((mrem - drop) // (fpm - drop))
    frame_number += add

    f = frame_number % nominal
    s = (frame_number // nominal) % 60
    m = (frame_number // (nominal * 60)) % 60
    h = frame_number // (nominal * 3600)
    return f"{h:02d}:{m:02d}:{s:02d};{f:02d}"


def build_edl(
    segments,
    fps: int = 30,
    drop_frame: bool = False,
    title: str = "Whispered Timeline",
    clip_name: str = "",
) -> str:
    """Build a CMX3600 EDL string from a list of kept segments.

    Each segment must have .start and .end attributes (seconds, float).
    Zero/negative-length segments are skipped.
    Returns empty string if no valid segments remain.
    """
    def tc(secs: float) -> str:
        return seconds_to_timecode(secs, fps, drop_frame)

    fcm = "DROP FRAME" if (drop_frame and int(round(fps)) in (30, 60)) else "NON-DROP FRAME"

    lines = []
    rec_seconds = 0.0
    event_num = 0

    for seg in segments:
        duration = seg.end - seg.start
        if duration <= 0:
            continue

        event_num += 1
        src_in  = tc(seg.start)
        src_out = tc(seg.end)
        rec_in  = tc(rec_seconds)
        rec_out = tc(rec_seconds + duration)

        lines.append(
            f"{event_num:03d}  AX       AA/V  C        "
            f"{src_in} {src_out} {rec_in} {rec_out}"
        )
        if clip_name:
            lines.append(f"* FROM CLIP NAME: {clip_name}")

        rec_seconds += duration

    if not lines:
        return ""

    header = [f"TITLE: {title}", f"FCM: {fcm}", ""]
    return "\n".join(header + lines) + "\n"


def write_edl(segments, filepath: str, **kwargs) -> None:
    """Write an EDL file.  kwargs are forwarded to build_edl()."""
    content = build_edl(segments, **kwargs)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
