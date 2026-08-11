"""Reads/writes the Geoapify Places API key used by Hospital Suppression
(see app/hospital_service.py), set on the Settings page.

Stored per environment on the `app_settings` table (see
app/email_settings_service.py) — Developer Mode and User Mode keep separate
Geoapify keys, resolved by the current mode (app/mode_state.py).
"""

from loguru import logger

from app.mode_state import current_environment
from database.connection import get_config_session
from database.models import AppSettings


def get_geoapify_api_key() -> str:
    """Return the current mode's saved Geoapify API key, or "" if none set."""
    session = get_config_session()
    try:
        row = session.query(AppSettings).filter_by(environment=current_environment()).first()
    finally:
        session.close()

    return (row.geoapify_api_key or "") if row else ""


def save_geoapify_api_key(api_key: str) -> None:
    """Upsert the current mode's Geoapify key — saving in Developer Mode
    never touches the User Mode key and vice versa."""
    session = get_config_session()
    try:
        row = session.query(AppSettings).filter_by(environment=current_environment()).first()
        if row is None:
            row = AppSettings(environment=current_environment())
            session.add(row)
        row.geoapify_api_key = api_key.strip()
        session.commit()
    finally:
        session.close()

    logger.info(f"Geoapify API key {'saved' if api_key.strip() else 'cleared'}")
