"""Reads/writes the Work Distribution Email Center's own Gmail SMTP
sending configuration, mirroring app/inventory_email_settings_service.py's
identical-purpose module as closely as possible, per explicit instruction
to reuse that architecture rather than reinvent it.

Deliberately a SEPARATE credential set from Path Validator's and
Inventory's own sender email/app password -- Work Distribution reports may
need to be sent from a different Gmail account.

Persisted as three named rows in the existing `work_distribution_parameters`
table (see database/models.py's WorkDistributionParameter,
app/work_distribution_parameters_service.py) rather than a new table --
this is a plain name/value store, and KPI thresholds and email credentials
can share it without conflict (distinct parameter_name keys). Reading/
writing WorkDistributionParameter directly here, instead of going through
work_distribution_parameters_service.py's get_all()/ensure_defaults(),
keeps these three keys out of that module's KPI-specific contract -- an app
password should never be swept up in a generic "get every KPI threshold"
call.

`automatic_sending_enabled` mirrors app.inventory_email_settings_service's
identical-purpose field exactly: when True, ui/work_distribution_upload_page.py
automatically builds and sends the notification batch right after RGD
Coverage or Manager Work Allocation Run Analysis completes (no manual
Preview/Send click) -- see that page's own module docstring. When False,
the Email Center's manual Preview/Send controls are the only way to send.
"""

from loguru import logger

from database.connection import get_config_session
from database.models import WorkDistributionParameter

SENDER_EMAIL = "work_distribution_sender_email"
SENDER_APP_PASSWORD = "work_distribution_sender_app_password"
AUTOMATIC_EMAIL_ENABLED = "work_distribution_automatic_email_enabled"


def _get_parameter(session, name: str) -> str:
    row = session.query(WorkDistributionParameter).filter_by(parameter_name=name).first()
    return row.parameter_value if row else ""


def _set_parameter(session, name: str, value: str) -> None:
    row = session.query(WorkDistributionParameter).filter_by(parameter_name=name).first()
    if row:
        row.parameter_value = value
    else:
        session.add(WorkDistributionParameter(parameter_name=name, parameter_value=value))


def get_settings() -> dict:
    """Return {'sender_email', 'app_password', 'automatic_sending_enabled'}
    -- empty/False if nothing has been saved yet. Mirrors
    app.inventory_email_settings_service.get_settings()'s exact shape."""
    session = get_config_session()
    try:
        sender_email = _get_parameter(session, SENDER_EMAIL)
        app_password = _get_parameter(session, SENDER_APP_PASSWORD)
        automatic_raw = _get_parameter(session, AUTOMATIC_EMAIL_ENABLED)
    finally:
        session.close()
    return {
        "sender_email": sender_email,
        "app_password": app_password,
        "automatic_sending_enabled": automatic_raw == "1",
    }


def save_settings(sender_email: str, app_password: str, automatic_sending_enabled: bool) -> None:
    session = get_config_session()
    try:
        _set_parameter(session, SENDER_EMAIL, sender_email)
        _set_parameter(session, SENDER_APP_PASSWORD, app_password)
        _set_parameter(session, AUTOMATIC_EMAIL_ENABLED, "1" if automatic_sending_enabled else "0")
        session.commit()
    finally:
        session.close()
    logger.info(
        f"Work Distribution email settings saved: sender={sender_email or '(none)'}, "
        f"automatic_sending_enabled={automatic_sending_enabled}"
    )


def is_automatic_sending_enabled() -> bool:
    return get_settings()["automatic_sending_enabled"]
