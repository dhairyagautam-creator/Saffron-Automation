"""Tests for the stale-finding email eligibility gate (2026-08-25 fix):
an Open InvestigationFinding older than STALE_FINDING_AGE_DAYS must be
excluded from build_email_batch's own eligible-findings pool, so a finding
nobody ever resolved doesn't keep resurfacing indefinitely in later runs
as if it belonged to the current one. The finding itself is never
modified -- this only affects batch ELIGIBILITY.

Pure unit test of _is_stale_for_email -- no DB, no SMTP, no network,
mirroring tests/test_low_working_hours_email.py's own approach of using
SimpleNamespace for the finding rather than a real ORM row.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

from app.notification_service import STALE_FINDING_AGE_DAYS, _is_stale_for_email


def _finding(created_at):
    return SimpleNamespace(created_at=created_at)


def test_freshly_created_finding_is_not_stale():
    assert _is_stale_for_email(_finding(datetime.now())) is False


def test_finding_just_under_the_threshold_is_not_stale():
    assert _is_stale_for_email(_finding(datetime.now() - timedelta(days=STALE_FINDING_AGE_DAYS - 1))) is False


def test_finding_just_over_the_threshold_is_stale():
    assert _is_stale_for_email(_finding(datetime.now() - timedelta(days=STALE_FINDING_AGE_DAYS + 1))) is True


def test_finding_weeks_old_is_stale():
    """Mirrors the real case found in the 2026-08-25 investigation: a
    finding created 2026-08-10, still Open and being emailed 2026-08-25 --
    a 15-day gap, comfortably past the threshold."""
    assert _is_stale_for_email(_finding(datetime.now() - timedelta(days=15))) is True


def test_finding_with_no_created_at_is_never_stale():
    """Fail open, not fail closed -- a finding predating this column must
    never be silently excluded by a check it was never designed for."""
    assert _is_stale_for_email(_finding(created_at=None)) is False
