"""
Whisper Fedora UI - Export Functions
Export transcription results to various formats
"""

from transcriber import TranscriptionResult
from utils import format_timestamp_srt, format_timestamp_vtt


def _speaker_name(result: TranscriptionResult, seg) -> str:
    """Display name for a segment's speaker ('' if none).

    Honours user renames stored in result.speaker_names; falls back to the raw
    speaker id. Works even on results that predate the speaker_names field.
    """
    speaker_id = getattr(seg, 'speaker', None)
    if not speaker_id:
        return ''
    names = getattr(result, 'speaker_names', None) or {}
    return names.get(speaker_id, speaker_id)


def export_txt(result: TranscriptionResult, filepath: str) -> None:
    """Export transcription as plain text."""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(result.full_text)


def export_txt_with_timestamps(result: TranscriptionResult, filepath: str) -> None:
    """Export transcription as text with timestamps."""
    with open(filepath, 'w', encoding='utf-8') as f:
        for seg in result.segments:
            timestamp = format_timestamp_vtt(seg.start)
            speaker = _speaker_name(result, seg)
            prefix = f"{speaker}: " if speaker else ""
            f.write(f"[{timestamp}] {prefix}{seg.text.strip()}\n")


def export_srt(result: TranscriptionResult, filepath: str) -> None:
    """Export transcription as SRT subtitle file."""
    with open(filepath, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(result.segments, start=1):
            start = format_timestamp_srt(seg.start)
            end = format_timestamp_srt(seg.end)
            speaker = _speaker_name(result, seg)
            prefix = f"{speaker}: " if speaker else ""
            text = seg.text.strip()

            f.write(f"{i}\n")
            f.write(f"{start} --> {end}\n")
            f.write(f"{prefix}{text}\n")
            f.write("\n")


def export_vtt(result: TranscriptionResult, filepath: str) -> None:
    """Export transcription as WebVTT subtitle file."""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write("WEBVTT\n\n")

        for i, seg in enumerate(result.segments, start=1):
            start = format_timestamp_vtt(seg.start)
            end = format_timestamp_vtt(seg.end)
            speaker = _speaker_name(result, seg)
            text = seg.text.strip()
            # WebVTT voice tag carries the speaker name when present.
            cue = f"<v {speaker}>{text}</v>" if speaker else text

            f.write(f"{i}\n")
            f.write(f"{start} --> {end}\n")
            f.write(f"{cue}\n")
            f.write("\n")


def export_json(result: TranscriptionResult, filepath: str) -> None:
    """Export transcription as JSON."""
    import json

    data = {
        'language': result.language,
        'duration': result.duration,
        'text': result.full_text,
        'speaker_names': getattr(result, 'speaker_names', None) or {},
        'segments': [
            {
                'start': seg.start,
                'end': seg.end,
                'text': seg.text.strip(),
                'speaker_id': getattr(seg, 'speaker', None),
                'speaker': _speaker_name(result, seg) or None,
            }
            for seg in result.segments
        ]
    }

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def export_md(result: TranscriptionResult, filepath: str) -> None:
    """Export transcription as Markdown."""
    import datetime
    
    minutes = int(result.duration // 60)
    seconds = int(result.duration % 60)
    duration_str = f"{minutes}m {seconds}s"
    date_str = datetime.datetime.now().strftime('%Y-%m-%d')
    
    with open(filepath, 'w', encoding='utf-8') as f:
        # YAML frontmatter
        f.write('---\n')
        f.write(f'date: {date_str}\n')
        f.write(f'language: {result.language}\n')
        f.write(f'duration: {duration_str}\n')
        f.write('---\n\n')
        
        # Full transcript body
        f.write('## Transcript\n\n')
        f.write(result.full_text)
        f.write('\n\n')
        
        # Timestamped segments section
        f.write('## Segments\n\n')
        for seg in result.segments:
            start = format_timestamp_vtt(seg.start)
            name = _speaker_name(result, seg)
            speaker = f'**{name}**: ' if name else ''
            f.write(f'- `{start}` {speaker}{seg.text.strip()}\n')


# Export format options
EXPORT_FORMATS = {
    'txt': ('Plain Text (.txt)', export_txt),
    'txt_ts': ('Text with Timestamps (.txt)', export_txt_with_timestamps),
    'srt': ('SRT Subtitles (.srt)', export_srt),
    'vtt': ('WebVTT Subtitles (.vtt)', export_vtt),
    'json': ('JSON (.json)', export_json),
    'md': ('Markdown (.md)', export_md),
}


def export_result(
    result: TranscriptionResult,
    filepath: str,
    format_key: str
) -> None:
    """
    Export transcription result to specified format.
    
    Args:
        result: The transcription result
        filepath: Output file path
        format_key: One of 'txt', 'txt_ts', 'srt', 'vtt', 'json'
    """
    if format_key not in EXPORT_FORMATS:
        raise ValueError(f"Unknown format: {format_key}")
    
    _, export_func = EXPORT_FORMATS[format_key]
    export_func(result, filepath)
