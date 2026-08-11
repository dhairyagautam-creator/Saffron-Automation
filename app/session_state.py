"""Tracks the single 'active session file' — the one import that analysis,
findings, and (eventually) email generation operate on.

Importing a new workbook replaces the active session entirely; whatever was
active before is simply no longer used, though its data remains in the
database for a possible future "reload a previous import" feature.
"""

from datetime import datetime

from loguru import logger

from database.connection import get_session
from database.models import ActiveSession, ImportHistory

_SINGLETON_ID = 1


def get_active_import_id() -> int | None:
    """Return the import_id of the active session file, or None if nothing
    has been imported yet (or the active import was never set)."""
    session = get_session()
    try:
        row = session.query(ActiveSession).filter_by(id=_SINGLETON_ID).first()
        return row.import_id if row else None
    finally:
        session.close()


def get_active_import() -> ImportHistory | None:
    """Return the ImportHistory row for the active session file, if any."""
    import_id = get_active_import_id()
    if import_id is None:
        return None

    session = get_session()
    try:
        return session.query(ImportHistory).filter_by(id=import_id).first()
    finally:
        session.close()


def set_active_import(import_id: int) -> None:
    """Make `import_id` the active session file, replacing whatever was active before."""
    session = get_session()
    try:
        row = session.query(ActiveSession).filter_by(id=_SINGLETON_ID).first()
        if row:
            row.import_id = import_id
            row.activated_at = datetime.now()
        else:
            session.add(
                ActiveSession(id=_SINGLETON_ID, import_id=import_id, activated_at=datetime.now())
            )
        session.commit()
    finally:
        session.close()

    logger.info(f"Active session file set to import_id={import_id}")
