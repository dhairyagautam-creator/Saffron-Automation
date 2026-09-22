"""Tests for the stale-finding email eligibility gate -- re-fixed 2026-09-23
for the current architecture (originally 2026-08-25, on the now-orphaned
release/2.3.2 branch, keyed off `created_at`; ported here onto
`first_flagged_at` since `created_at` is now stamped fresh on every rule
re-run -- see app/notification_service.py's own module-level comment on
STALE_FINDING_AGE_DAYS for the full history).

An unresolved finding (notification_status never reaches "Sent" -- e.g. a
vacant RBM, matching the original production bug exactly) older than
STALE_FINDING_AGE_DAYS must be excluded from build_email_batch's eligible
pool entirely -- both the per-manager batch and the master report email,
which otherwise includes every unresolved finding regardless of age. The
finding itself is never modified -- this only affects batch ELIGIBILITY.
"""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.master_email_recipients_service import ALL_DIVISIONS, create_recipient
from app.notification_service import STALE_FINDING_AGE_DAYS, _is_stale_for_email, build_email_batch
from database.connection import get_session, utcnow
from database.models import InvestigationFinding
from tests.db_isolation import isolate_database


# --- Pure unit tests for _is_stale_for_email (adapted from the original
# --- 2026-08-25 fix's own test_stale_finding_email_gate.py) --------------

def _finding(first_flagged_at):
    return SimpleNamespace(first_flagged_at=first_flagged_at)


def test_freshly_flagged_finding_is_not_stale():
    assert _is_stale_for_email(_finding(datetime.now())) is False


def test_finding_just_under_the_threshold_is_not_stale():
    assert _is_stale_for_email(_finding(datetime.now() - timedelta(days=STALE_FINDING_AGE_DAYS - 1))) is False


def test_finding_just_over_the_threshold_is_stale():
    assert _is_stale_for_email(_finding(datetime.now() - timedelta(days=STALE_FINDING_AGE_DAYS + 1))) is True


def test_finding_weeks_old_is_stale():
    """Mirrors the real case found in the original 2026-08-25 investigation:
    a finding still Open/unresolved and being emailed 15+ days after it was
    first flagged -- comfortably past the threshold."""
    assert _is_stale_for_email(_finding(datetime.now() - timedelta(days=15))) is True


def test_finding_with_no_first_flagged_at_is_never_stale():
    """Fail open, not fail closed -- a legacy row predating the backfill
    migration must never be silently excluded by a check it was never
    designed to evaluate."""
    assert _is_stale_for_email(_finding(first_flagged_at=None)) is False


# --- Integration: build_email_batch actually excludes a stale, vacant-RBM
# --- finding from BOTH the unresolved draft and the master report --------

@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    isolate_database(monkeypatch)


def _seed_finding(employee_code: str, first_flagged_at: datetime) -> int:
    """A vacant-RBM-shaped finding -- no hierarchy table/connection
    configured at all, so build_email_batch resolves no Senior for it and
    it lands in the "unresolved" bucket, exactly like the original bug's
    own trigger scenario. No cluster_lat/lon (skips Hospital Suppression's
    network call) and no matching raw_visits rows (skips Reverse
    Geocoding's network call) -- this test never touches the network."""
    session = get_session()
    try:
        row = InvestigationFinding(
            import_id=1,
            employee_name=f"Employee {employee_code}",
            employee_code=employee_code,
            visit_date=date(2026, 7, 15),
            rule_name="SAME_LOCATION",
            message="3 of 3 GPS visits (100%) occurred within approximately 50 meters of the same location.",
            division=None,
            notification_status=None,
            created_at=utcnow(),
            first_flagged_at=first_flagged_at,
        )
        session.add(row)
        session.commit()
        return row.finding_id
    finally:
        session.close()


def test_stale_unresolved_finding_excluded_from_both_batch_and_master_report():
    """Reproduces the exact real-world scenario, not just a value one day
    past the threshold: the original production incident was a finding
    first flagged 2026-07-15, still being freshly emailed 2026-08-25 --
    41 days later."""
    fresh_id = _seed_finding("E-FRESH", first_flagged_at=utcnow())
    stale_id = _seed_finding("E-STALE", first_flagged_at=utcnow() - timedelta(days=41))

    create_recipient("Master Recipient", "master@example.com", ALL_DIVISIONS)

    drafts = build_email_batch(import_id=1)

    unresolved_drafts = [d for d in drafts if d["kind"] == "unresolved"]
    master_drafts = [d for d in drafts if d["kind"] == "master"]
    assert len(unresolved_drafts) == 1
    assert len(master_drafts) == 1

    unresolved_ids = {int(fid) for fid in unresolved_drafts[0]["finding_ids"].split(",")}
    master_ids = {int(fid) for fid in master_drafts[0]["finding_ids"].split(",") if fid}

    assert fresh_id in unresolved_ids
    assert stale_id not in unresolved_ids
    assert fresh_id in master_ids
    assert stale_id not in master_ids

    # The stale finding itself is untouched: still there, still unresolved,
    # fully visible on the Findings page -- only its EMAIL ELIGIBILITY
    # changed, nothing about the finding's own persisted state.
    session = get_session()
    try:
        stale_row = session.query(InvestigationFinding).filter_by(finding_id=stale_id).one()
        assert stale_row.notification_status is None
    finally:
        session.close()
