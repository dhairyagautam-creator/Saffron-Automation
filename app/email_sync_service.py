"""Cloud sync for email_notifications -- the Send Log data. This is what
the cloud migration's "generated reports" requirement maps to (see
supabase/migrations/0012_path_validator_email_notifications_sync.sql's
comment) -- there is no separate report-export/PDF pipeline anywhere in
this codebase. Version 2.0 Path Validator cloud migration, Milestone 16,
unified onto the app-wide Last-Modified-Wins rule in Milestone 35 (see
app/sync_service.reconcile_rows()).
"""

import uuid
from datetime import datetime

from loguru import logger

from app.mode_state import is_developer_mode
from app.sync_service import push_rows, reconcile_rows
from database.connection import get_session
from database.models import EmailNotification, ImportHistory

TABLE = "path_validator_email_notifications"

_COLUMNS = (
    "manager_name",
    "manager_email",
    "subject",
    "body",
    "finding_ids",
    "status",
    "error_message",
    "sent_at",
)


def _parse_ts(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _serialize(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _get_import_cloud_id(import_id: int) -> str | None:
    session = get_session()
    try:
        history = session.query(ImportHistory).filter_by(id=import_id).first()
        return history.cloud_id if history else None
    finally:
        session.close()


def sync_email_notifications_for_import(import_id: int) -> bool:
    """Last-Modified-Wins reconciliation for every email notification
    belonging to `import_id`, mirroring
    app.findings_sync_service.sync_findings_for_import() exactly: pulls
    the cloud's current rows for this import, compares each against the
    matching local row (by cloud_id) using updated_at, pushes whichever
    local rows are newer/new, applies whichever cloud rows are newer/new.
    Returns True if anything changed locally. No-op (returns False) in
    Developer Mode.

    The one function both the module-wide Refresh action and the
    immediate push right after a real send (see
    app/notification_service.py) call -- no separate push-only/pull-only
    pair anymore."""
    if is_developer_mode():
        return False

    import_cloud_id = _get_import_cloud_id(import_id)
    if import_cloud_id is None:
        logger.warning(f"Sync: cannot sync email notifications for import_id={import_id} -- import has no cloud_id yet")
        return False

    session = get_session()
    try:
        notifications = session.query(EmailNotification).filter_by(import_id=import_id).all()
        local_rows = []
        for notification in notifications:
            if not notification.cloud_id:
                notification.cloud_id = str(uuid.uuid4())
            row = {"cloud_id": notification.cloud_id, "import_cloud_id": import_cloud_id}
            for column in _COLUMNS:
                row[column] = _serialize(getattr(notification, column))
            row["updated_at"] = _serialize(notification.updated_at)
            local_rows.append(row)
        session.commit()
    finally:
        session.close()

    plan = reconcile_rows(
        TABLE, local_rows, key_columns=["cloud_id"], filters={"import_cloud_id": import_cloud_id}
    )
    if not plan.success:
        logger.warning(f"Sync: failed to reconcile email notifications for import_id={import_id}: {plan.error_message}")
        return False

    changed = False

    if plan.to_push:
        result = push_rows(TABLE, plan.to_push, on_conflict="cloud_id")
        if not result.success:
            logger.warning(f"Sync: failed to push {len(plan.to_push)} email notification(s): {result.error_message}")
        else:
            now = datetime.now()
            session = get_session()
            try:
                for row in plan.to_push:
                    notification = session.query(EmailNotification).filter_by(cloud_id=row["cloud_id"]).first()
                    if notification is not None:
                        notification.synced_at = now
                session.commit()
            finally:
                session.close()
            logger.info(f"Sync: pushed {len(plan.to_push)} email notification(s) for import_id={import_id}")

    if plan.to_pull:
        session = get_session()
        try:
            now = datetime.now()
            for cloud_row in plan.to_pull:
                cloud_id = cloud_row["cloud_id"]
                notification = session.query(EmailNotification).filter_by(cloud_id=cloud_id).first()
                if notification is None:
                    notification = EmailNotification(cloud_id=cloud_id, import_id=import_id)
                    session.add(notification)
                for column in _COLUMNS:
                    value = cloud_row.get(column)
                    if column == "sent_at":
                        value = _parse_ts(value)
                    setattr(notification, column, value)
                notification.updated_at = _parse_ts(cloud_row.get("updated_at"))
                notification.synced_at = now
            session.commit()
        finally:
            session.close()
        logger.info(f"Sync: pulled {len(plan.to_pull)} email notification(s) for import_id={import_id}")
        changed = True

    return changed
