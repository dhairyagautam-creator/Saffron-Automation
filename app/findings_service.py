"""Read/update access to investigation_findings, scoped to the active
session file — a previously imported file's findings stay in the database
but are not shown unless a future feature explicitly reloads them.
"""

import re
from datetime import datetime

from loguru import logger

from database.connection import get_session
from database.models import InvestigationFinding

STATUSES = ("Open", "Reviewed", "Ignored")

# Notification statuses that mean the canonical suppression pass withheld this
# finding. That pass lives in ONE place -- app/notification_service.build_email_batch,
# which reuses app/region_suppression (Kerala/Punjab etc.) and
# app/hospital_service -- and persists its decision on notification_status. A
# finding in one of these states is NOT actionable: it is excluded from the
# HR-Based findings table AND the Master page AND the email, all by consulting
# is_finding_suppressed() below, so there is exactly one suppression rule, not
# a second copy per surface.
SUPPRESSED_NOTIFICATION_STATUSES = ("Suppressed - Region Rule", "Hospital Suppressed")


def is_finding_suppressed(finding) -> bool:
    """True if the canonical suppression pass marked this finding region- or
    hospital-suppressed. The persisted notification_status is the single
    source of truth -- Master, the HR-Based table, and the email pipeline all
    read it, so a suppressed employee is withheld everywhere identically."""
    return finding.notification_status in SUPPRESSED_NOTIFICATION_STATUSES

# The backend HOURS_WORKED detector (rules/hours_worked.py) writes its
# per-finding numbers into the finding message, e.g.
#   "Worked 5.2h (10:15–15:27), below the 6h minimum. Review Required."
# The detector is frozen, so the message is the one place first/last call,
# hours worked, and the threshold live. This parser is the single reader of
# that format -- both the Findings page (ui/findings_page.py) and the email
# pipeline (app/notification_service.py) go through it, so there is exactly
# one regex to update if the message text ever changes.
_HOURS_WORKED_RE = re.compile(
    r"Worked\s+([\d.]+)h\s+\((\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})\),\s+below the\s+([\d.]+)h"
)


def parse_hours_worked_message(message: str) -> dict | None:
    """Structured values behind a HOURS_WORKED finding's message, or None if
    the message doesn't match. hours_worked/minimum/hours_short are floats
    (hours); first_call/last_call are "HH:MM" strings. Callers format as they
    need (decimal on the Findings page, h/m in the email)."""
    match = _HOURS_WORKED_RE.search(message or "")
    if not match:
        return None
    worked = float(match.group(1))
    minimum = float(match.group(4))
    return {
        "first_call": match.group(2),
        "last_call": match.group(3),
        "hours_worked": worked,
        "minimum": minimum,
        "hours_short": max(0.0, minimum - worked),
    }


def get_all_findings(import_id: int | None) -> list[InvestigationFinding]:
    """Return findings for the given import_id, most recent visit date
    first. Returns an empty list if `import_id` is None (no active session)."""
    if import_id is None:
        return []

    session = get_session()
    try:
        return (
            session.query(InvestigationFinding)
            .filter_by(import_id=import_id)
            .order_by(InvestigationFinding.visit_date.desc())
            .all()
        )
    finally:
        session.close()


def get_summary_counts(import_id: int | None) -> dict:
    """Return {'Total': n, 'Open': n, 'Reviewed': n, 'Ignored': n} for the
    given import_id. All zero if `import_id` is None."""
    counts = {"Total": 0, "Open": 0, "Reviewed": 0, "Ignored": 0}
    if import_id is None:
        return counts

    session = get_session()
    try:
        rows = session.query(InvestigationFinding).filter_by(import_id=import_id).all()
    finally:
        session.close()

    counts["Total"] = len(rows)
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return counts


def set_status(finding_id: int, status: str) -> None:
    """Update a single finding's review status."""
    if status not in STATUSES:
        raise ValueError(f"Unknown status '{status}', expected one of {STATUSES}")

    session = get_session()
    try:
        finding = session.query(InvestigationFinding).filter_by(finding_id=finding_id).first()
        if finding is None:
            raise ValueError(f"No finding with id {finding_id}")
        finding.status = status
        finding.updated_at = datetime.now()
        finding.dirty = 1  # local change -- protect from a stale cloud pull, push with CAS
        session.commit()
    finally:
        session.close()

    logger.info(f"Finding {finding_id} marked as {status}")


def set_notification_status(finding_id: int, notification_status: str, suppression_reason: str | None = None) -> None:
    """Record what happened to a finding's automatic notification —
    "Sent", "Hospital Suppressed", or "Failed" (see app/notification_service.py
    and app/hospital_service.py). Separate from `status` (Open/Reviewed/
    Ignored), which tracks manual review, not notification outcome.

    `suppression_reason=None` (the default) leaves whatever's already there
    untouched rather than clearing it -- this is what lets
    note_suppression_check_issue() below flag a finding whose hospital
    check couldn't be completed, and have that note survive the later Sent
    call that marks the same finding's email outcome."""
    session = get_session()
    try:
        finding = session.query(InvestigationFinding).filter_by(finding_id=finding_id).first()
        if finding is None:
            raise ValueError(f"No finding with id {finding_id}")
        finding.notification_status = notification_status
        if suppression_reason is not None:
            finding.suppression_reason = suppression_reason
        finding.updated_at = datetime.now()
        finding.dirty = 1  # local change -- protect from a stale cloud pull, push with CAS
        session.commit()
    finally:
        session.close()

    logger.info(f"Finding {finding_id} notification_status set to {notification_status}")


def note_suppression_check_issue(finding_id: int, note: str) -> None:
    """Flag that this finding's hospital-suppression check could not be
    completed (lookup failure, or the batch's circuit breaker skipped it) —
    written to `suppression_reason` without touching `notification_status`,
    so a finding whose check was inconclusive is never silently
    indistinguishable from one that was genuinely checked and cleared. The
    email for it still goes out as designed (fail-open), but this note
    survives on the finding for manual review afterward."""
    session = get_session()
    try:
        finding = session.query(InvestigationFinding).filter_by(finding_id=finding_id).first()
        if finding is None:
            raise ValueError(f"No finding with id {finding_id}")
        finding.suppression_reason = note
        finding.updated_at = datetime.now()
        finding.dirty = 1  # local change -- protect from a stale cloud pull, push with CAS
        session.commit()
    finally:
        session.close()

    logger.warning(f"Finding {finding_id} suppression check inconclusive: {note}")


def set_hospital_suppression(
    finding_id: int,
    reason: str,
    hospital_name: str,
    hospital_lat: float,
    hospital_lon: float,
    distance_meters: int,
) -> None:
    """Mark a finding Hospital Suppressed with the full structured detail
    the Findings page's detail panel shows (facility name, its own
    coordinates, and the distance from the flagged cluster) alongside the
    plain-language `suppression_reason` used in logs."""
    session = get_session()
    try:
        finding = session.query(InvestigationFinding).filter_by(finding_id=finding_id).first()
        if finding is None:
            raise ValueError(f"No finding with id {finding_id}")
        finding.notification_status = "Hospital Suppressed"
        finding.suppression_reason = reason
        finding.hospital_name = hospital_name
        finding.hospital_lat = hospital_lat
        finding.hospital_lon = hospital_lon
        finding.hospital_distance_meters = distance_meters
        finding.updated_at = datetime.now()
        finding.dirty = 1  # local change -- protect from a stale cloud pull, push with CAS
        session.commit()
    finally:
        session.close()

    logger.info(f"Finding {finding_id} Hospital Suppressed: {reason}")


def get_notification_status_counts(import_id: int | None) -> dict:
    """Return {'Pending': n, 'Sent': n, 'Failed': n, 'Hospital Suppressed': n,
    'Suppressed - Region Rule': n} for the given import_id, based on each
    finding's own `notification_status` — not the review `status` used by
    get_summary_counts. A finding with no notification_status yet (the
    pipeline hasn't reached it, or automatic sending is off) counts as
    Pending. All zero if `import_id` is None."""
    counts = {"Pending": 0, "Sent": 0, "Failed": 0, "Hospital Suppressed": 0, "Suppressed - Region Rule": 0}
    if import_id is None:
        return counts

    session = get_session()
    try:
        rows = session.query(InvestigationFinding).filter_by(import_id=import_id).all()
    finally:
        session.close()

    for row in rows:
        key = row.notification_status if row.notification_status in counts else "Pending"
        counts[key] += 1
    return counts
