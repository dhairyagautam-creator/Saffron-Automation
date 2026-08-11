"""The password gate into Developer Mode.

The password is never hardcoded and never stored in plaintext: a random
salt plus a PBKDF2-HMAC-SHA256 hash are stored on the GLOBAL (user)
app_settings row (see database/models.py — dev_password_hash /
dev_password_salt are global, not per-environment). It is set and changed
only from inside Developer Mode (bootstrap: the very first time, no
existing password is required to set one).
"""

import hashlib
import hmac
import os

from loguru import logger

from app.mode_state import USER_ENVIRONMENT
from database.connection import get_config_session
from database.models import AppSettings

_PBKDF2_ITERATIONS = 100_000


def _global_row(session):
    """The dev-mode gate lives on the 'user' (global) app_settings row —
    created if it doesn't exist yet."""
    row = session.query(AppSettings).filter_by(environment=USER_ENVIRONMENT).first()
    if row is None:
        row = AppSettings(environment=USER_ENVIRONMENT)
        session.add(row)
    return row


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS).hex()


def is_dev_password_set() -> bool:
    session = get_config_session()
    try:
        row = session.query(AppSettings).filter_by(environment=USER_ENVIRONMENT).first()
        return bool(row and row.dev_password_hash and row.dev_password_salt)
    finally:
        session.close()


def set_dev_password(password: str) -> None:
    """Set or change the Developer Mode password. A fresh random salt is
    generated each time, so changing the password fully re-derives the
    stored hash."""
    salt = os.urandom(16)
    session = get_config_session()
    try:
        row = _global_row(session)
        row.dev_password_salt = salt.hex()
        row.dev_password_hash = _hash(password, salt)
        session.commit()
    finally:
        session.close()

    logger.info("Developer Mode password set/changed")


def verify_dev_password(password: str) -> bool:
    session = get_config_session()
    try:
        row = session.query(AppSettings).filter_by(environment=USER_ENVIRONMENT).first()
    finally:
        session.close()

    if not (row and row.dev_password_hash and row.dev_password_salt):
        return False
    candidate = _hash(password, bytes.fromhex(row.dev_password_salt))
    return hmac.compare_digest(candidate, row.dev_password_hash)
