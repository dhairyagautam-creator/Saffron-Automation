"""Reads/writes small pieces of whole-application state that don't belong
to any single settings section — currently just whether the first-run
Setup Wizard (ui/setup_wizard.py) has been completed.

`setup_completed` is GLOBAL, not per-environment: onboarding is an app-wide
fact, so it's always stored on the 'user' app_settings row regardless of
which mode is active (see database/models.py).
"""

from loguru import logger

from app.mode_state import USER_ENVIRONMENT
from database.connection import get_config_session
from database.models import AppSettings


def is_setup_completed() -> bool:
    session = get_config_session()
    try:
        row = session.query(AppSettings).filter_by(environment=USER_ENVIRONMENT).first()
    finally:
        session.close()

    return bool(row.setup_completed) if row else False


def set_setup_completed(completed: bool) -> None:
    """Called with True when the Setup Wizard finishes, and with False by
    the About page's "Reset Configuration" button — flipping it back to
    False only makes the wizard reappear next launch; it never touches any
    other saved setting. Always stored on the global (user) row."""
    session = get_config_session()
    try:
        row = session.query(AppSettings).filter_by(environment=USER_ENVIRONMENT).first()
        if row is None:
            row = AppSettings(environment=USER_ENVIRONMENT)
            session.add(row)
        row.setup_completed = 1 if completed else 0
        session.commit()
    finally:
        session.close()

    logger.info(f"Setup {'completed' if completed else 'reset'}")
