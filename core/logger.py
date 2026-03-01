"""
Whispered - Centralized Logging
Configures file + console logging for the entire application.
Log file: ~/.whispered/app.log (with rotation)
"""

import os
import sys
import logging
import platform
from logging.handlers import RotatingFileHandler
from typing import Optional


_logging_configured = False


def _get_log_dir() -> str:
    """Get the platform-appropriate log directory."""
    system = platform.system()
    if system == 'Windows':
        base = os.environ.get('APPDATA', os.path.expanduser('~\\AppData\\Roaming'))
        return os.path.join(base, 'Whispered', 'logs')
    elif system == 'Darwin':
        return os.path.expanduser('~/Library/Application Support/Whispered/logs')
    else:
        base = os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
        return os.path.join(base, 'Whispered', 'logs')


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configure application-wide logging.

    Sets up two handlers:
    - Console (stderr): INFO+ with concise format
    - File (~/.whispered/logs/app.log): DEBUG+ with detailed format, 5MB rotation

    Should be called once at application startup (main.py).
    """
    global _logging_configured
    if _logging_configured:
        return

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # --- Console handler (concise) ---
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_fmt = logging.Formatter(
        '%(levelname)-8s %(name)s: %(message)s'
    )
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # --- File handler (detailed, with rotation) ---
    try:
        log_dir = _get_log_dir()
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, 'app.log')

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_fmt = logging.Formatter(
            '%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_fmt)
        root_logger.addHandler(file_handler)
    except Exception as e:
        # If file logging fails (permissions, etc.), continue with console only
        root_logger.warning(f"Could not set up file logging: {e}")

    # Silence overly chatty third-party loggers
    for noisy in ('urllib3', 'PIL', 'matplotlib', 'torch', 'pyannote'):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _logging_configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger for a module.

    Usage at the top of any file:
        from core.logger import get_logger
        logger = get_logger(__name__)
    """
    return logging.getLogger(name)
