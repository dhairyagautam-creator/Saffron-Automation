"""CRUD for the Inventory Automated Email System's manually-configured
recipients -- Version 2.0, Milestone 56.

Path Validator resolves its recipients dynamically at send time by
walking the employee hierarchy (app.hierarchy_service.compute_seniors());
Inventory has no such per-employee hierarchy to resolve FROM, so its
recipients are instead a small, directly-managed list (see
database.models.InventoryEmailRecipient, ui/inventory_automated_emails_page.py)
-- add/edit/delete here, read back by
app.inventory_notification_service.build_inventory_email_batch() every
time a report is generated.

`divisions` is stored on the row as a single comma-separated string (see
the model's own docstring) but every function in THIS module's public
API works in terms of a plain `list[str]` -- callers (the UI page, the
notification service) never touch the comma-joining/splitting
themselves.
"""

from datetime import datetime

from loguru import logger

from database.connection import get_config_session
from database.models import InventoryEmailRecipient

# The real Division values currently present in InventoryThreshold/
# InventoryReplenishment data (confirmed against the live database,
# 2026-07-28) -- the fixed set of checkboxes the Automated Emails page
# offers. Uppercase, matching exactly how they're stored/displayed
# throughout the rest of Inventory Monitoring -- never re-cased here.
DIVISION_OPTIONS = ("GUARDIANS", "ONYX", "XANDRA")


def _serialize_divisions(divisions: list[str]) -> str:
    # Sorted so the same set of divisions always serializes identically
    # regardless of the order checkboxes were clicked in -- makes
    # equality-style comparisons/tests predictable and keeps the stored
    # string tidy.
    return ",".join(sorted(d for d in divisions if d))


def _deserialize_divisions(value: str) -> list[str]:
    return [d for d in (value or "").split(",") if d]


def _row_to_dict(row: InventoryEmailRecipient) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "email": row.email,
        "divisions": _deserialize_divisions(row.divisions),
    }


def get_all_recipients() -> list[dict]:
    """Every configured recipient, ordered by name -- {'id', 'name',
    'email', 'divisions': list[str]}."""
    session = get_config_session()
    try:
        rows = session.query(InventoryEmailRecipient).order_by(InventoryEmailRecipient.name).all()
        return [_row_to_dict(row) for row in rows]
    finally:
        session.close()


def create_recipient(name: str, email: str, divisions: list[str]) -> dict:
    """Adds a new recipient. Returns the created row as a dict (see
    _row_to_dict) so the caller has the generated id immediately."""
    now = datetime.now()
    session = get_config_session()
    try:
        row = InventoryEmailRecipient(
            name=name.strip(),
            email=email.strip(),
            divisions=_serialize_divisions(divisions),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        result = _row_to_dict(row)
    finally:
        session.close()

    logger.info(f"Inventory email recipient added: {result['name']} <{result['email']}> divisions={result['divisions']}")
    return result


def update_recipient(recipient_id: int, name: str, email: str, divisions: list[str]) -> bool:
    """Updates an existing recipient. Returns False (no-op) if the id no
    longer exists -- e.g. deleted from another window in the meantime --
    rather than raising, matching this codebase's existing "missing row
    is a normal, checkable outcome, not an exception" convention."""
    session = get_config_session()
    try:
        row = session.query(InventoryEmailRecipient).filter_by(id=recipient_id).first()
        if row is None:
            return False
        row.name = name.strip()
        row.email = email.strip()
        row.divisions = _serialize_divisions(divisions)
        row.updated_at = datetime.now()
        session.commit()
    finally:
        session.close()

    logger.info(f"Inventory email recipient updated: id={recipient_id} name={name} divisions={divisions}")
    return True


def delete_recipient(recipient_id: int) -> bool:
    """Removes a recipient. Returns False (no-op) if the id no longer
    exists, same reasoning as update_recipient()."""
    session = get_config_session()
    try:
        row = session.query(InventoryEmailRecipient).filter_by(id=recipient_id).first()
        if row is None:
            return False
        session.delete(row)
        session.commit()
    finally:
        session.close()

    logger.info(f"Inventory email recipient deleted: id={recipient_id}")
    return True
