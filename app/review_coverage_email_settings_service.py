"""Reads/writes the Coverage Summary automated-email workflow's own
"automatic sending enabled" toggle -- the ONE thing this workflow needs a
setting of its own for (see app/review_coverage_notification_service.py).

Deliberately does NOT store a sender email/app password here: the actual
sending account always comes from app/email_settings_service.py -- the
SAME account already used by the rest of the application's email
functionality (Path Validator), never a second, independently-configured
one. Mirrors app/work_distribution_email_settings_service.py's own
`automatic_sending_enabled` field, minus the credential fields that
module (before today) deliberately kept separate from the shared account
-- a pattern this workflow starts from correctly rather than repeats.

Persisted as a named row in a small dedicated
`review_coverage_parameters` table (ReviewCoverageParameter) -- a plain
name/value store, same convention as
app/work_distribution_email_settings_service.py's own use of
WorkDistributionParameter for this exact purpose.
"""

from loguru import logger

from database.connection import get_config_session
from database.models import ReviewCoverageParameter

AUTOMATIC_EMAIL_ENABLED = "review_coverage_automatic_email_enabled"


def is_automatic_sending_enabled() -> bool:
    """False (manual-only) if nothing has been saved yet -- new automated-
    send capability defaults OFF until explicitly turned on, same
    convention as every other module's own automatic-sending toggle."""
    session = get_config_session()
    try:
        row = session.query(ReviewCoverageParameter).filter_by(parameter_name=AUTOMATIC_EMAIL_ENABLED).first()
        return row is not None and row.parameter_value == "1"
    finally:
        session.close()


def set_automatic_sending_enabled(enabled: bool) -> None:
    session = get_config_session()
    try:
        row = session.query(ReviewCoverageParameter).filter_by(parameter_name=AUTOMATIC_EMAIL_ENABLED).first()
        value = "1" if enabled else "0"
        if row:
            row.parameter_value = value
        else:
            session.add(ReviewCoverageParameter(parameter_name=AUTOMATIC_EMAIL_ENABLED, parameter_value=value))
        session.commit()
    finally:
        session.close()
    logger.info(f"Coverage Summary automatic email sending set to {enabled}")
