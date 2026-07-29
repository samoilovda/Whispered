"""
Whispered - Core Package
Business logic and infrastructure, independent of UI layer.

Intentionally free of re-exports: importing a leaf module such as
``core.paths`` or ``core.logger`` must not drag PyQt6 (and the whole UI
stack) into headless callers — transcription child processes, CLI
scripts, and the fast unit-test suite all import from ``core`` without a
Qt runtime.  Import from the concrete module instead::

    from core.lm_client import LMStudioClient
    from core.logger import get_logger
"""
