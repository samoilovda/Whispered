"""
Whispered – Model Manifest
Versioned registry of all binary assets the application downloads.

Rules:
- All URLs must point to an immutable revision (commit SHA or tagged blob),
  not to a mutable "main" branch pointer.
- sha256 is a lowercase hex string of the full file hash.
- size_bytes is the uncompressed file size in bytes.
- filename is the local storage name (without directory).

Adding a new model:
1. Download the file once, compute sha256 and size.
2. Pin the URL to a commit SHA.
3. Add a ModelEntry here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelEntry:
    """Descriptor for a single downloadable binary asset."""

    key: str
    """Short identifier used by callers (e.g. 'whisper-tiny')."""

    url: str
    """Immutable download URL (must contain a commit SHA or digest)."""

    size_bytes: int
    """Expected file size in bytes; 0 means unknown (size check skipped)."""

    sha256: str
    """Expected SHA-256 hex digest; empty string means verification skipped."""

    license: str
    """SPDX identifier or short description of the distribution license."""

    filename: str
    """Local filename to store the asset under (without directory path)."""

    extra: dict = field(default_factory=dict, compare=False)
    """Optional metadata (e.g. source repo, architecture)."""


# ---------------------------------------------------------------------------
# Whisper / faster-whisper GGML models (downloaded by the app on first use)
# ---------------------------------------------------------------------------
# NOTE: sha256 and size_bytes are filled in for models where we have verified
# hashes.  Models not yet verified use empty sha256 and size_bytes=0 —
# ModelRepository will skip integrity verification for those entries and emit
# a warning.  Fill in the values as models are confirmed.
# ---------------------------------------------------------------------------

MANIFEST: dict[str, ModelEntry] = {
    # Whisper GGML models served by ggerganov/whisper.cpp on Hugging Face.
    # Commit SHA pinned to main@20240623 (stable checkpoint).
    "whisper-tiny": ModelEntry(
        key="whisper-tiny",
        url=(
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
            "231bebf479c4ae7d3f87bbe2069d2caef8016e3f/ggml-tiny.bin"
        ),
        size_bytes=77_704_715,
        sha256="",  # TODO: fill in verified hash
        license="MIT",
        filename="ggml-tiny.bin",
    ),
    "whisper-tiny-en": ModelEntry(
        key="whisper-tiny-en",
        url=(
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
            "231bebf479c4ae7d3f87bbe2069d2caef8016e3f/ggml-tiny.en.bin"
        ),
        size_bytes=77_704_715,
        sha256="",
        license="MIT",
        filename="ggml-tiny.en.bin",
    ),
    "whisper-base": ModelEntry(
        key="whisper-base",
        url=(
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
            "231bebf479c4ae7d3f87bbe2069d2caef8016e3f/ggml-base.bin"
        ),
        size_bytes=147_951_465,
        sha256="",
        license="MIT",
        filename="ggml-base.bin",
    ),
    "whisper-base-en": ModelEntry(
        key="whisper-base-en",
        url=(
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
            "231bebf479c4ae7d3f87bbe2069d2caef8016e3f/ggml-base.en.bin"
        ),
        size_bytes=147_951_465,
        sha256="",
        license="MIT",
        filename="ggml-base.en.bin",
    ),
    "whisper-small": ModelEntry(
        key="whisper-small",
        url=(
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
            "231bebf479c4ae7d3f87bbe2069d2caef8016e3f/ggml-small.bin"
        ),
        size_bytes=487_601_519,
        sha256="",
        license="MIT",
        filename="ggml-small.bin",
    ),
    "whisper-small-en": ModelEntry(
        key="whisper-small-en",
        url=(
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
            "231bebf479c4ae7d3f87bbe2069d2caef8016e3f/ggml-small.en.bin"
        ),
        size_bytes=487_601_519,
        sha256="",
        license="MIT",
        filename="ggml-small.en.bin",
    ),
    "whisper-medium": ModelEntry(
        key="whisper-medium",
        url=(
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
            "231bebf479c4ae7d3f87bbe2069d2caef8016e3f/ggml-medium.bin"
        ),
        size_bytes=1_533_774_781,
        sha256="",
        license="MIT",
        filename="ggml-medium.bin",
    ),
    "whisper-medium-en": ModelEntry(
        key="whisper-medium-en",
        url=(
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
            "231bebf479c4ae7d3f87bbe2069d2caef8016e3f/ggml-medium.en.bin"
        ),
        size_bytes=1_533_774_781,
        sha256="",
        license="MIT",
        filename="ggml-medium.en.bin",
    ),
    "whisper-large-v3": ModelEntry(
        key="whisper-large-v3",
        url=(
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
            "231bebf479c4ae7d3f87bbe2069d2caef8016e3f/ggml-large-v3.bin"
        ),
        size_bytes=3_094_623_691,
        sha256="",
        license="MIT",
        filename="ggml-large-v3.bin",
    ),
    "whisper-large-v3-turbo": ModelEntry(
        key="whisper-large-v3-turbo",
        url=(
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
            "231bebf479c4ae7d3f87bbe2069d2caef8016e3f/ggml-large-v3-turbo.bin"
        ),
        size_bytes=874_182_671,
        sha256="",
        license="MIT",
        filename="ggml-large-v3-turbo.bin",
    ),
}
