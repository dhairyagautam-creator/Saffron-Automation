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

from app.recipient_sync_service import check_for_recipient_update, push_recipients
from database.connection import get_config_session, utcnow
from database.models import InventoryEmailRecipient

# module_configurations.module_key this recipient list syncs under
# (parameter sync project, Phase 4) -- its own key, separate from
# app.inventory_parameters_service's "inventory_module" scalar-parameter
# blob, so a recipient change never touches (or is blocked by) a
# threshold sync failure and vice versa.
MODULE_KEY = "inventory_email_recipients"

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
    now = utcnow()
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
        row.updated_at = utcnow()
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


# --- Cloud config sync (parameter sync project, Phase 4) ------------------

def _recipients_for_sync() -> list[dict]:
    """{'name', 'email', 'divisions': list[str]} per recipient -- 'id' is
    a local autoincrement with no cross-machine meaning, never pushed."""
    return [{"name": r["name"], "email": r["email"], "divisions": r["divisions"]} for r in get_all_recipients()]


def create_recipient_synced(name: str, email: str, divisions: list[str]) -> tuple[bool, str | None, dict | None]:
    """Adds a new recipient, blocked entirely if offline/signed out --
    same "must sync immediately" contract as every scalar parameter save.
    Returns (True, None, created_row) on success, or
    (False, error_message, None) if the push failed -- nothing is
    written locally in that case."""
    pending = _recipients_for_sync() + [{"name": name.strip(), "email": email.strip(), "divisions": list(divisions)}]
    ok, error = push_recipients(MODULE_KEY, pending)
    if not ok:
        return False, error, None
    return True, None, create_recipient(name, email, divisions)


def update_recipient_synced(recipient_id: int, name: str, email: str, divisions: list[str]) -> tuple[bool, str | None]:
    """Same push-first-then-apply contract as create_recipient_synced.
    Checked BEFORE pushing anything: if `recipient_id` no longer exists
    locally (deleted from another window in the meantime), this is a
    normal, checkable outcome (matching update_recipient()'s own
    convention) -- returns (False, message) without ever calling
    push_recipients, so a stale edit never overwrites the shared cloud
    list with a row that's about to silently fail to apply locally."""
    current = get_all_recipients()
    if not any(r["id"] == recipient_id for r in current):
        return False, "This recipient no longer exists -- it may have been deleted elsewhere."

    pending = [
        {"name": name.strip(), "email": email.strip(), "divisions": list(divisions)} if r["id"] == recipient_id else
        {"name": r["name"], "email": r["email"], "divisions": r["divisions"]}
        for r in current
    ]
    ok, error = push_recipients(MODULE_KEY, pending)
    if not ok:
        return False, error
    update_recipient(recipient_id, name, email, divisions)
    return True, None


def delete_recipient_synced(recipient_id: int) -> tuple[bool, str | None]:
    """Same push-first-then-apply contract as create_recipient_synced."""
    pending = [
        {"name": r["name"], "email": r["email"], "divisions": r["divisions"]}
        for r in get_all_recipients() if r["id"] != recipient_id
    ]
    ok, error = push_recipients(MODULE_KEY, pending)
    if not ok:
        return False, error
    delete_recipient(recipient_id)
    return True, None


def check_for_update() -> dict:
    """See app.recipient_sync_service.check_for_recipient_update -- wired
    to this module's own recipients and MODULE_KEY, for
    ui.parameter_sync_panel.ParameterSyncPanel's check_fn."""
    return check_for_recipient_update(MODULE_KEY, _recipients_for_sync())


def apply_new_recipients(check_result: dict) -> tuple[bool, str | None]:
    """Union-merges check_for_update()'s own `new_recipients` into local
    storage -- for ui.parameter_sync_panel.ParameterSyncPanel's apply_fn.
    Malformed entries (missing name/email, or a non-list divisions) are
    skipped rather than raising -- a partial merge is still strictly
    better than losing the rest of a legitimate batch."""
    added = 0
    for recipient in check_result.get("new_recipients", []):
        name, email, divisions = recipient.get("name"), recipient.get("email"), recipient.get("divisions")
        if not name or not email or not isinstance(divisions, list):
            continue
        create_recipient(name, email, divisions)
        added += 1
    logger.info(f"Inventory email recipients: merged {added} new recipient(s) from sync")
    return True, None
