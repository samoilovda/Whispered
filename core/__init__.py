"""
Whispered - Core Package
Business logic and infrastructure, independent of UI layer.

Imports are lazy (PEP 562 ``__getattr__``) so that pure-logic modules
(``lm_client``, ``json_utils``, ``logger``) can be imported and unit-tested
without pulling in heavy/optional dependencies such as PyQt6 (only needed by
``ai_worker``).
"""

from typing import Any

__all__ = [
    'LMStudioClient',
    'DEFAULT_LM_STUDIO_URL',
    'AIProcessingWorker',
    'setup_logging',
    'get_logger',
]


def __getattr__(name: str) -> Any:
    if name in ('LMStudioClient', 'DEFAULT_LM_STUDIO_URL'):
        from core.lm_client import LMStudioClient, DEFAULT_LM_STUDIO_URL
        return {'LMStudioClient': LMStudioClient,
                'DEFAULT_LM_STUDIO_URL': DEFAULT_LM_STUDIO_URL}[name]
    if name == 'AIProcessingWorker':
        from core.ai_worker import AIProcessingWorker
        return AIProcessingWorker
    if name in ('setup_logging', 'get_logger'):
        from core.logger import setup_logging, get_logger
        return {'setup_logging': setup_logging, 'get_logger': get_logger}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
