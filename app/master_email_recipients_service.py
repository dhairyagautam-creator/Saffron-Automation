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

from database.connection import get_config_session
from database.models import MasterEmailRecipient

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
    now = datetime.now()
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
        row.updated_at = datetime.now()
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
