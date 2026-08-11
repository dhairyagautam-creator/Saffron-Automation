"""Manages named, user-selected workbook file paths — e.g. the three
hierarchy workbooks (Onyx, Guardians, Xandra) and the email directory
workbook. Each is a separate, independently-connected data source.
"""

import os
from datetime import datetime

from loguru import logger

from database.connection import get_session
from database.models import WorkbookConnection

WORKBOOK_NAMES = ("Onyx", "Guardians", "Xandra")


def get_connection(workbook_name: str) -> str | None:
    """Return the stored file path for a single named workbook, or None."""
    session = get_session()
    try:
        row = session.query(WorkbookConnection).filter_by(workbook_name=workbook_name).first()
        return row.file_path if row else None
    finally:
        session.close()


def get_connections(names: tuple[str, ...] = WORKBOOK_NAMES) -> dict:
    """Return {workbook_name: file_path or None} for the given workbook names."""
    session = get_session()
    try:
        rows = {
            row.workbook_name: row.file_path
            for row in session.query(WorkbookConnection).filter(
                WorkbookConnection.workbook_name.in_(names)
            )
        }
    finally:
        session.close()

    return {name: rows.get(name) for name in names}


def set_connection(workbook_name: str, file_path: str) -> None:
    """Create or update the stored file path for a workbook. Bumps
    updated_at -- the genuine local "last modified" timestamp the
    app-wide Last-Modified-Wins sync rule compares against the cloud
    (see app/organization_data_sync_service.py) -- every time, whether
    called from a local Browse click or while applying an inbound sync
    pull (see app/organization_data_sync_service.sync_workbooks(), which
    intentionally overwrites this again with the cloud's own timestamp
    right after, so a pulled connection never looks locally-newer than
    it actually is)."""
    session = get_session()
    try:
        row = session.query(WorkbookConnection).filter_by(workbook_name=workbook_name).first()
        if row:
            row.file_path = file_path
        else:
            row = WorkbookConnection(workbook_name=workbook_name, file_path=file_path)
            session.add(row)
        row.updated_at = datetime.now()
        session.commit()
    finally:
        session.close()

    logger.info(f"Connected workbook '{workbook_name}' -> {file_path}")


def get_status(file_path: str | None) -> str:
    """Return 'Connected', 'File Not Found', or 'Not Configured' for a path."""
    if not file_path:
        return "Not Configured"
    if not os.path.isfile(file_path):
        return "File Not Found"
    return "Connected"
