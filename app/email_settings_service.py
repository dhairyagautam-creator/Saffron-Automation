"""Reads/writes the single-row Gmail SMTP sending configuration, set on the
Settings page's Email Settings section.

Persisted in the `app_settings` table (sender_gmail_address,
gmail_app_password — see database/models.py). Saving always updates that
one row (upsert) rather than inserting a new one, and loading always reads
straight from the database, so the Settings page always sees whatever was
last saved without the user needing to re-enter anything each session.

`automatic_email_enabled` and `master_email_address` were removed from
this table (Phase 1 email-authority dead-code cleanup, parameter sync
project): automatic sending has had zero callers anywhere in the app
since manual "Send Emails" buttons replaced it, and the master-report
recipient concept was fully superseded by the `MasterEmailRecipient`
table (see app/master_email_recipients_service.py) with no code left
reading the old column. See database/migrations.py's
drop_dead_automatic_email_flags() for the migration that dropped both
columns.
"""

from loguru import logger

from database.connection import get_config_session, utcnow
from database.models import AppSettings


def get_settings() -> dict:
    """Return {'sender_email', 'app_password'} -- empty if nothing has
    been saved yet."""
    session = get_config_session()
    try:
        row = session.query(AppSettings).first()
    finally:
        session.close()

    if row is None:
        logger.info("Email settings loaded: none saved yet, using blank defaults")
        return {"sender_email": "", "app_password": ""}

    settings = {
        "sender_email": row.sender_gmail_address or "",
        "app_password": row.gmail_app_password or "",
    }
    logger.info(f"Email settings loaded: sender={settings['sender_email'] or '(none)'}")
    return settings


def save_settings(sender_email: str, app_password: str) -> None:
    """Upsert the settings row."""
    session = get_config_session()
    try:
        row = session.query(AppSettings).first()
        if row is None:
            row = AppSettings()
            session.add(row)
        row.sender_gmail_address = sender_email
        row.gmail_app_password = app_password
        row.updated_at = utcnow()
        session.commit()
    finally:
        session.close()

    logger.info(f"Email settings saved: sender={sender_email or '(none)'}")
