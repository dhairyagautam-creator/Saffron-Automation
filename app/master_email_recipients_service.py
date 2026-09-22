"""CRUD for the Path Validator Master Email system's manually-configured
recipients -- built using app/inventory_email_recipients_service.py as its
explicit reference architecture, per instruction: same shape (add/edit/
delete a small directly-managed list, read back by
app.notification_service.build_email_batch() every processing run), only
the report being filtered is different (Employee Findings here, Inventory
Replenishment there).

Individual manager emails (one per RBM, resolved dynamically via
app.hierarchy_service) are a completely separate system and are never
touched by this module -- see app/notification_service.py's own docstring.

`division` is a SINGLE value per recipient (unlike Inventory's
comma-separated multi-division list) -- see database.models.MasterEmailRecipient's
own docstring for why, and for why the real stored values are "ONYX"/
"GUARDIANS"/"XANDRA" (confirmed against live InvestigationFinding data),
not the "Onyx"/"Guardian"/"Zandra" spelling from the task's own examples.
"""

from datetime import datetime

from loguru import logger

from app.recipient_sync_service import check_for_recipient_update, push_recipients
from database.connection import get_config_session, utcnow
from database.models import MasterEmailRecipient

# module_configurations.module_key this recipient list syncs under
# (parameter sync project, Phase 4) -- its own key, separate from
# app.rule_parameters' "employee_module" scalar-parameter blob, so a
# recipient change never touches (or is blocked by) a rule-threshold
# sync failure and vice versa.
MODULE_KEY = "master_email_recipients"

# "ALL" is a sentinel meaning "every division, no filter" -- not a real
# Division value any finding ever carries -- see division_matches() below.
ALL_DIVISIONS = "ALL"

# The real Division values Path Validator's own findings currently carry
# (InvestigationFinding.division, sourced straight from the uploaded
# call-report's own "Division" column) -- confirmed against live data.
# Stored/compared uppercase for a stable key; DIVISION_LABELS below holds
# the real display spelling.
DIVISION_OPTIONS = (ALL_DIVISIONS, "ONYX", "GUARDIANS", "XANDRA")

DIVISION_LABELS = {
    ALL_DIVISIONS: "All Divisions",
    "ONYX": "Onyx",
    "GUARDIANS": "Guardians",
    "XANDRA": "Xandra",
}


def division_matches(recipient_division: str, finding_division: str | None) -> bool:
    """True if a finding whose own `division` field is `finding_division`
    should be included in a recipient's filtered master report.
    `recipient_division` is one of DIVISION_OPTIONS (always uppercase, as
    stored). ALL_DIVISIONS matches every finding, including one with no
    division recorded at all. Any other recipient division requires an
    actual, non-blank match -- case/whitespace-insensitive, since the
    uploaded workbook's own casing for its Division column isn't
    guaranteed."""
    if recipient_division == ALL_DIVISIONS:
        return True
    if not finding_division:
        return False
    return finding_division.strip().upper() == recipient_division


def _row_to_dict(row: MasterEmailRecipient) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "email": row.email,
        "division": row.division,
        "division_label": DIVISION_LABELS.get(row.division, row.division),
    }


def get_all_recipients() -> list[dict]:
    """Every configured recipient, ordered by name -- {'id', 'name',
    'email', 'division', 'division_label'}."""
    session = get_config_session()
    try:
        rows = session.query(MasterEmailRecipient).order_by(MasterEmailRecipient.name).all()
        return [_row_to_dict(row) for row in rows]
    finally:
        session.close()


def create_recipient(name: str, email: str, division: str) -> dict:
    """Adds a new recipient. Returns the created row as a dict (see
    _row_to_dict) so the caller has the generated id immediately."""
    now = utcnow()
    session = get_config_session()
    try:
        row = MasterEmailRecipient(
            name=name.strip(),
            email=email.strip(),
            division=division,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        result = _row_to_dict(row)
    finally:
        session.close()

    logger.info(f"Master email recipient added: {result['name']} <{result['email']}> division={result['division']}")
    return result


def update_recipient(recipient_id: int, name: str, email: str, division: str) -> bool:
    """Updates an existing recipient. Returns False (no-op) if the id no
    longer exists -- e.g. deleted from another window in the meantime --
    rather than raising, matching this codebase's existing "missing row is
    a normal, checkable outcome, not an exception" convention."""
    session = get_config_session()
    try:
        row = session.query(MasterEmailRecipient).filter_by(id=recipient_id).first()
        if row is None:
            return False
        row.name = name.strip()
        row.email = email.strip()
        row.division = division
        row.updated_at = utcnow()
        session.commit()
    finally:
        session.close()

    logger.info(f"Master email recipient updated: id={recipient_id} name={name} division={division}")
    return True


def delete_recipient(recipient_id: int) -> bool:
    """Removes a recipient. Returns False (no-op) if the id no longer
    exists, same reasoning as update_recipient()."""
    session = get_config_session()
    try:
        row = session.query(MasterEmailRecipient).filter_by(id=recipient_id).first()
        if row is None:
            return False
        session.delete(row)
        session.commit()
    finally:
        session.close()

    logger.info(f"Master email recipient deleted: id={recipient_id}")
    return True


# --- Cloud config sync (parameter sync project, Phase 4) ------------------

def _recipients_for_sync() -> list[dict]:
    """{'name', 'email', 'division'} per recipient -- 'id' is a local
    autoincrement with no cross-machine meaning, never pushed."""
    return [{"name": r["name"], "email": r["email"], "division": r["division"]} for r in get_all_recipients()]


def create_recipient_synced(name: str, email: str, division: str) -> tuple[bool, str | None, dict | None]:
    """Adds a new recipient, blocked entirely if offline/signed out --
    same "must sync immediately" contract as every scalar parameter save
    (see app.parameter_sync_service's own module docstring). Returns
    (True, None, created_row) on success, or (False, error_message, None)
    if the push failed -- nothing is written locally in that case."""
    pending = _recipients_for_sync() + [{"name": name.strip(), "email": email.strip(), "division": division}]
    ok, error = push_recipients(MODULE_KEY, pending)
    if not ok:
        return False, error, None
    return True, None, create_recipient(name, email, division)


def update_recipient_synced(recipient_id: int, name: str, email: str, division: str) -> tuple[bool, str | None]:
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
        {"name": name.strip(), "email": email.strip(), "division": division} if r["id"] == recipient_id else
        {"name": r["name"], "email": r["email"], "division": r["division"]}
        for r in current
    ]
    ok, error = push_recipients(MODULE_KEY, pending)
    if not ok:
        return False, error
    update_recipient(recipient_id, name, email, division)
    return True, None


def delete_recipient_synced(recipient_id: int) -> tuple[bool, str | None]:
    """Same push-first-then-apply contract as create_recipient_synced."""
    pending = [
        {"name": r["name"], "email": r["email"], "division": r["division"]}
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
    Never fails (a well-formed recipient dict from another machine's own
    push always has name/email/division); malformed entries are skipped
    rather than raising, since a partial merge is still strictly better
    than losing the rest of a legitimate batch."""
    added = 0
    for recipient in check_result.get("new_recipients", []):
        name, email, division = recipient.get("name"), recipient.get("email"), recipient.get("division")
        if not name or not email or not division:
            continue
        create_recipient(name, email, division)
        added += 1
    logger.info(f"Master email recipients: merged {added} new recipient(s) from sync")
    return True, None
