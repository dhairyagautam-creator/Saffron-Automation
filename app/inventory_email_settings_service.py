"""Reads/writes the Inventory Automated Email System's own Gmail SMTP
sending configuration -- Version 2.0, Milestone 56, mirroring
app/email_settings_service.py (Path Validator's identical-purpose module)
as closely as possible, per explicit instruction to reuse that
architecture rather than reinvent it.

Deliberately a SEPARATE credential set from Path Validator's own
Settings-page sender email/app password -- Inventory reports may need to
be sent from a different Gmail account than Path Validator uses, and the
task explicitly asked for "the same Email Configuration section" to be
added to Inventory Settings, not for Inventory to reuse Path Validator's
saved values.

Persisted as three named rows in the `inventory_parameters` table (see
database/models.py's InventoryParameter, app/inventory_parameters_service.py)
-- NOT via that module's own get_full_configuration()/push_config()
cloud-sync wiring. This is a deliberate exclusion, not an oversight:
Path Validator's own AppSettings.gmail_app_password is likewise NEVER
pushed to the cloud by app/email_settings_service.py (confirmed: it has
no push_config/pull_config call anywhere) -- an app password is exactly
the kind of credential that should never leave the machine it was typed
into via a shared cloud config table. Reading/writing InventoryParameter
directly here, instead of going through inventory_parameters_service.py's
synced get/set helpers, keeps these three keys out of that sync path
entirely, matching Path Validator's own security posture for the same
kind of secret.
"""

from datetime import datetime

from loguru import logger

from database.connection import get_config_session
from database.models import InventoryParameter

INVENTORY_SENDER_EMAIL = "inventory_sender_email"
INVENTORY_SENDER_APP_PASSWORD = "inventory_sender_app_password"
INVENTORY_AUTOMATIC_EMAIL_ENABLED = "inventory_automatic_email_enabled"

_SETTINGS_KEYS = (INVENTORY_SENDER_EMAIL, INVENTORY_SENDER_APP_PASSWORD, INVENTORY_AUTOMATIC_EMAIL_ENABLED)


def _get_parameter(session, name: str) -> str:
    row = session.query(InventoryParameter).filter_by(parameter_name=name).first()
    return row.parameter_value if row else ""


def _set_parameter(session, name: str, value: str, now: datetime) -> None:
    row = session.query(InventoryParameter).filter_by(parameter_name=name).first()
    if row:
        row.parameter_value = value
    else:
        session.add(InventoryParameter(parameter_name=name, parameter_value=value))
    # InventoryParameter has no updated_at column of its own (see
    # database/models.py) -- `now` is accepted for signature symmetry with
    # email_settings_service.save_settings() but currently unused; kept so
    # a future column addition doesn't require touching every call site.
    _ = now


def get_settings() -> dict:
    """Return {'sender_email', 'app_password', 'automatic_sending_enabled'}
    -- the Inventory Automated Email System's own saved credentials.
    Values are empty/False if nothing has been saved yet. Mirrors
    app.email_settings_service.get_settings()'s exact shape, minus
    `master_email` (Inventory has no hierarchy-resolution-fallback
    "master consolidated report" concept -- every configured recipient
    IS the full recipient list, there's no separate rollup address)."""
    session = get_config_session()
    try:
        sender_email = _get_parameter(session, INVENTORY_SENDER_EMAIL)
        app_password = _get_parameter(session, INVENTORY_SENDER_APP_PASSWORD)
        automatic_raw = _get_parameter(session, INVENTORY_AUTOMATIC_EMAIL_ENABLED)
    finally:
        session.close()

    settings = {
        "sender_email": sender_email,
        "app_password": app_password,
        "automatic_sending_enabled": automatic_raw == "1",
    }
    logger.info(
        f"Inventory email settings loaded: sender={settings['sender_email'] or '(none)'}, "
        f"automatic_sending_enabled={settings['automatic_sending_enabled']}"
    )
    return settings


def save_settings(sender_email: str, app_password: str, automatic_sending_enabled: bool) -> None:
    """Upsert all three settings. Mirrors
    app.email_settings_service.save_settings()'s exact shape."""
    now = datetime.now()
    session = get_config_session()
    try:
        _set_parameter(session, INVENTORY_SENDER_EMAIL, sender_email, now)
        _set_parameter(session, INVENTORY_SENDER_APP_PASSWORD, app_password, now)
        _set_parameter(session, INVENTORY_AUTOMATIC_EMAIL_ENABLED, "1" if automatic_sending_enabled else "0", now)
        session.commit()
    finally:
        session.close()

    logger.info(
        f"Inventory email settings saved: sender={sender_email or '(none)'}, "
        f"automatic_sending_enabled={automatic_sending_enabled}"
    )


def is_automatic_sending_enabled() -> bool:
    return get_settings()["automatic_sending_enabled"]
