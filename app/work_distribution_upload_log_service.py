"""Records + reads Work Distribution's own Recent Uploads activity log
(database/models.py's WorkDistributionUploadLog) -- feeds the Dashboard's
Recent Uploads panel. Pure activity logging: never read by any parser,
calculation, threshold, or finding -- only by the Dashboard's own display.
"""

from datetime import datetime
from pathlib import Path

from database.connection import get_config_session
from database.models import WorkDistributionUploadLog


def record_upload(file_path: str, upload_type: str, division: str | None = None, status: str = "Uploaded") -> None:
    """Logs one successful Browse/parse (or hierarchy workbook connection).
    `file_path` may be a full path -- only its base filename is stored."""
    session = get_config_session()
    try:
        session.add(WorkDistributionUploadLog(
            file_name=Path(file_path).name,
            upload_type=upload_type,
            division=division or None,
            status=status,
            uploaded_at=datetime.now(),
        ))
        session.commit()
    finally:
        session.close()


def get_recent_uploads(limit: int = 10) -> list:
    """Most recent uploads first, for the Dashboard's own Recent Uploads
    table -- {file_name, upload_type, division, status, uploaded_at}."""
    session = get_config_session()
    try:
        rows = session.query(WorkDistributionUploadLog).order_by(
            WorkDistributionUploadLog.uploaded_at.desc()
        ).limit(limit).all()
        return [
            {
                "file_name": r.file_name,
                "upload_type": r.upload_type,
                "division": r.division or "",
                "status": r.status,
                "uploaded_at": r.uploaded_at.strftime("%d %b %Y, %I:%M %p") if r.uploaded_at else "",
            }
            for r in rows
        ]
    finally:
        session.close()
