"""Reads/writes the single-row Gmail SMTP sending configuration, set on the
Settings page's Email Settings section.

Persisted in the `app_settings` table (sender_gmail_address,
gmail_app_password, automatic_email_enabled, master_email_address — see
database/models.py). Saving always updates that one row (upsert) rather
than inserting a new one, and loading always reads straight from the
database, so automatic sending and the Settings page both see whatever was
last saved without the user needing to re-enter anything each session.
"""

from datetime import datetime

from loguru import logger

from app.mode_state import current_environment
from database.connection import get_config_session
from database.models import AppSettings

# The consolidated master report's recipient when nothing has been saved
# yet — the address every installation was already hardcoded to use before
# this became editable from the Settings page.
DEFAULT_MASTER_EMAIL = "gddesk@saffronformulations.com"


def get_settings() -> dict:
    """Return {'sender_email', 'app_password', 'automatic_sending_enabled',
    'master_email'} for the CURRENT mode's environment (Developer Mode and
    User Mode keep separate credentials — see app/mode_state.py). Values are
    empty/False if nothing has been saved yet, except `master_email` which
    falls back to DEFAULT_MASTER_EMAIL."""
    session = get_config_session()
    try:
        row = session.query(AppSettings).filter_by(environment=current_environment()).first()
    finally:
        session.close()

    if row is None:
        logger.info("Email settings loaded: none saved yet, using blank defaults")
        return {
            "sender_email": "",
            "app_password": "",
            "automatic_sending_enabled": False,
            "master_email": DEFAULT_MASTER_EMAIL,
        }

    settings = {
        "sender_email": row.sender_gmail_address or "",
        "app_password": row.gmail_app_password or "",
        "automatic_sending_enabled": bool(row.automatic_email_enabled),
        "master_email": row.master_email_address or DEFAULT_MASTER_EMAIL,
    }
    logger.info(
        f"Email settings loaded: sender={settings['sender_email'] or '(none)'}, "
        f"automatic_sending_enabled={settings['automatic_sending_enabled']}, "
        f"master_email={settings['master_email']}"
    )
    return settings


def save_settings(
    sender_email: str, app_password: str, automatic_sending_enabled: bool, master_email: str | None = None
) -> None:
    """Upsert the CURRENT mode's environment settings row — saving in
    Developer Mode never touches the User Mode row and vice versa.
    `master_email` left as None keeps whatever was already saved (or the
    default, if nothing was) rather than blanking it out."""
    session = get_config_session()
    try:
        row = session.query(AppSettings).filter_by(environment=current_environment()).first()
        if row is None:
            row = AppSettings(environment=current_environment())
            session.add(row)
        row.sender_gmail_address = sender_email
        row.gmail_app_password = app_password
        row.automatic_email_enabled = 1 if automatic_sending_enabled else 0
        if master_email is not None:
            row.master_email_address = master_email.strip() or DEFAULT_MASTER_EMAIL
        elif not row.master_email_address:
            row.master_email_address = DEFAULT_MASTER_EMAIL
        row.updated_at = datetime.now()
        session.commit()
        saved_master_email = row.master_email_address
    finally:
        session.close()

    logger.info(
        f"Email settings saved: sender={sender_email or '(none)'}, "
        f"automatic_sending_enabled={automatic_sending_enabled}, "
        f"master_email={saved_master_email}"
    )


def is_automatic_sending_enabled() -> bool:
    return get_settings()["automatic_sending_enabled"]


def get_master_email() -> str:
    return get_settings()["master_email"]
