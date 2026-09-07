"""Loguru logging configuration for the application.

Must never raise — logging is a diagnostic aid, not something the app's
ability to start should depend on. This matters concretely here: the
PyInstaller build is windowed (console=False in Saffron Automation.spec), and
when a windowed executable is launched with no console attached (a genuine
double-click, as opposed to launching it from a terminal that already has
one — which is what masked this during all of this project's development
and testing), Python/PyInstaller leaves `sys.stdout`/`sys.stderr` as `None`.
Passing that directly to `logger.add()` raises
`TypeError: Cannot log to objects of type 'NoneType'` — and since that
happens on the very first line of `configure_logging()`, called before
anything else in `main()`, the whole application died before the GUI window
ever appeared. No error dialog, nothing in Task Manager for more than a
moment — it just looked like double-clicking the icon did nothing.
"""

import sys
from pathlib import Path

from loguru import logger

from app.config import LOGS_DIR

_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)


def _fallback_log_dir() -> Path:
    """%LOCALAPPDATA%\\Saffron Validator\\logs — used only if the normal
    BASE_DIR-relative LOGS_DIR can't be created or written to (e.g. an
    install location the current user can't write into)."""
    local_app_data = Path(__import__("os").environ.get("LOCALAPPDATA") or Path.home())
    return local_app_data / "Saffron Validator" / "logs"


def configure_logging() -> None:
    """Set up loguru sinks for console and rotating file output. Every step
    is individually guarded so a problem with one sink (or with logging
    entirely) never stops the application from starting."""
    logger.remove()  # drop the default handler so we control format/sinks

    if sys.stderr is not None:
        try:
            logger.add(sys.stderr, level="INFO", format=_LOG_FORMAT)
        except Exception:
            pass  # no visible console to report this to anyway

    for candidate_dir in (LOGS_DIR, _fallback_log_dir()):
        try:
            candidate_dir.mkdir(parents=True, exist_ok=True)
            logger.add(
                candidate_dir / "saffron_validator.log",
                level="DEBUG",
                rotation="5 MB",
                retention="10 days",
                encoding="utf-8",
            )
            break  # first directory that actually works wins
        except Exception:
            continue  # try the next candidate; if all fail, run with no file sink at all
