"""Core business logic for the Manager Work Allocation engine's ABM phase:
turns parsed joint-working rows (app/manager_work_allocation_parser.py)
into per-ABM findings, using the configurable threshold in
app/manager_work_allocation_parameters_service.py -- never hardcoded.

Redesigned 2026-08-05 into a ROLLING SIX-MONTH HISTORY architecture -- a
genuine business-process correction, not a bug fix: what looked like
duplicate manager/subordinate rows within one upload were never
duplicates. Each uploaded report already contains several months of
history (Feb, Mar, Apr, ...), and an ABM/BM pair legitimately appears once
per month it has a record for. Summing those rows together (the previous
architecture, through 2026-08-04) silently collapsed an entire month-by-
month history into one meaningless total.

ABM business rule (current):
- Only rows where Emp Designation="ABM" AND Team Emp Designation="BM" are
  analyzed; every other designation combination is ignored entirely (the
  RBM engine, app/manager_work_allocation_rbm_service.py, handles its own
  rows completely separately, sharing this exact same architecture).
- A genuine intra-month duplicate (same Emp Code + Team Emp Code + Month,
  split across >1 raw row) is merged by summing -- see
  app.manager_work_allocation_shared.merge_same_month_duplicates. Rows for
  the SAME pair in DIFFERENT months are NEVER merged this way; each stays
  its own distinct monthly record.
- Every upload UPSERTS its own monthly records into the persistent
  ManagerWorkAllocationRecord store (an already-known month for a pair
  gets corrected, not duplicated), then that store is trimmed to the
  newest SIX distinct months by month VALUE -- never by upload order,
  filename, or manual replacement (see
  app.manager_work_allocation_shared.sync_rolling_window, the one place
  either engine ever writes to that table).
- For every ABM/BM pair, the AVERAGE joint days per month across every
  month currently retained in the rolling window is what's compared
  against Minimum Joint Working Days (Settings-configurable, default 4)
  -- NOT a sum. If a pair has no record for a given retained month, that
  month is simply absent from its own average (never padded with a zero
  -- "collect every monthly record... calculate average" operates on
  whatever records actually exist for that pair, per the module's own
  spec). If a BM's average falls below the threshold, that BM has
  "failed"; if ANY of an ABM's BMs failed, that ABM is "Flagged".

Both engines share app/manager_work_allocation_shared.py's month-parsing,
intra-month-merge, and rolling-window sync/trim helpers, and the SAME
three database tables (ManagerWorkAllocationRecord/Finding/BMDetail) --
every query and the sync/trim step are scoped to this engine's own
designation ("ABM" / source_engine="ABM") so Run Analysis for one engine
can never see or wipe the other's rows.

Scope: this module ONLY parses (via the parser), calculates, and generates
findings for the ABM engine. It does not send emails, sync to the cloud,
or export.
"""

from datetime import datetime

from loguru import logger

from app.manager_work_allocation_parameters_service import get_all as get_parameters
from app.manager_work_allocation_shared import (
    first_nonblank,
    get_retained_month_range_label,
    group_by,
    is_designation,
    log_designation_filter_diagnostics,
    merge_same_month_duplicates,
    sync_rolling_window,
)
from database.connection import get_config_session
from database.models import (
    ManagerWorkAllocationBMDetail,
    ManagerWorkAllocationFinding,
    ManagerWorkAllocationRecord,
)

STATUS_PASS = "Pass"
STATUS_FLAGGED = "Flagged"
STATUS_BM_FAIL = "Fail"

# This engine ONLY analyzes ABM (manager) / BM (subordinate) rows -- every
# other Emp Designation/Team Emp Designation combination is ignored (RBM
# rows are handled entirely by app.manager_work_allocation_rbm_service).
MANAGER_DESIGNATION = "ABM"
SUBORDINATE_DESIGNATION = "BM"

# Scopes every query/write below to rows THIS engine itself wrote, so
# RBM's own rows (sharing the same tables) are never read or touched here.
SOURCE_ENGINE = "ABM"

FINDINGS_COLUMNS = ("employee_name", "division", "total_bms", "passed_bms", "failed_bms", "status")
FINDINGS_HEADINGS = {
    "employee_name": "ABM Name",
    "division": "Division",
    "total_bms": "Total BMs",
    "passed_bms": "Passed BMs",
    "failed_bms": "Failed BMs",
    "status": "Status",
}

BM_DETAIL_COLUMNS = ("subordinate_name", "joint_days", "required_days", "status")
BM_DETAIL_HEADINGS = {
    "subordinate_name": "BM Name",
    "joint_days": "Average Joint Days",
    "required_days": "Required Days",
    "status": "Status",
}


def _format_average(value: float) -> str:
    """'4' for a whole number, '4.17' otherwise -- avoids noisy floating
    point tails (e.g. 4.166666...) in the UI/export while still showing
    real precision when it isn't a clean whole number."""
    rounded = round(value, 2)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:g}"


def _log_abm_debug_summary(abm_name: str, evaluated_bms: list, total: int, failed: int, min_days: float) -> None:
    """Logs the post-rolling-window breakdown for ONE sample ABM (the
    first processed each run) -- each BM's own month-by-month history and
    the resulting average, so Run Analysis can be verified by reading the
    log rather than guessing."""
    lines = [f"ABM: {abm_name}"]
    for bm in evaluated_bms:
        monthly_text = ", ".join(f"{month}={days}" for month, days in bm["monthly"])
        lines.append(f"  BM {bm['bm_name']}: {monthly_text} -> avg {_format_average(bm['joint_days'])}")
    lines.append(f"  Total unique BMs: {total}")
    lines.append(f"  Missed BMs (average < {_format_average(min_days)} days): {failed}")
    logger.info("Manager Work Allocation (ABM) debug summary (sample ABM):\n" + "\n".join(lines))


def process_manager_work_allocation_report(records: list) -> dict:
    """Full pipeline: filter to ABM/BM rows, merge genuine intra-month
    duplicates, sync the new monthly records into the rolling six-month
    window (upsert + trim -- see
    app.manager_work_allocation_shared.sync_rolling_window), evaluate
    every retained pair's AVERAGE joint days against the current Settings
    threshold, and store the result -- ManagerWorkAllocationRecord
    accumulates/rolls per above; ManagerWorkAllocationFinding +
    ManagerWorkAllocationBMDetail are full-replaced (derived views,
    recomputed fresh from the retained window every run).

    Returns: {total_records, abm_bm_relationships, abm_count,
    flagged_count, passed_count}.
    """
    params = get_parameters()
    min_days = params["minimum_joint_working_days"]

    abm_bm_rows = [
        r for r in records
        if is_designation(r["emp_designation"], MANAGER_DESIGNATION)
        and is_designation(r["team_emp_designation"], SUBORDINATE_DESIGNATION)
    ]
    log_designation_filter_diagnostics("ABM", records, MANAGER_DESIGNATION, SUBORDINATE_DESIGNATION, abm_bm_rows)
    new_monthly_records = merge_same_month_duplicates(abm_bm_rows)
    logger.info(
        f"Manager Work Allocation (ABM) diagnostics: {len(abm_bm_rows)} ABM/BM row(s) -> "
        f"{len(new_monthly_records)} record(s) after same-month-duplicate merge"
    )

    now = datetime.now()
    session = get_config_session()
    try:
        retained_rows = sync_rolling_window(
            session, ManagerWorkAllocationRecord, SOURCE_ENGINE, new_monthly_records, now,
        )
        logger.info(
            f"Manager Work Allocation (ABM) diagnostics: {len(retained_rows)} record(s) "
            "retained after rolling-window sync/trim"
        )

        pair_groups = group_by(retained_rows, lambda r: (r.emp_code, r.team_emp_code))
        abm_groups: dict = {}
        abm_order: list = []
        for (_emp_code, _team_emp_code), pair_rows in pair_groups.items():
            abm_key = pair_rows[0].emp_code or pair_rows[0].emp_name
            if abm_key not in abm_groups:
                abm_groups[abm_key] = []
                abm_order.append(abm_key)
            abm_groups[abm_key].append(pair_rows)

        findings = []
        bm_details = []
        for i, abm_key in enumerate(abm_order):
            bm_pair_rows_list = abm_groups[abm_key]
            abm_name = first_nonblank(r.emp_name for pair_rows in bm_pair_rows_list for r in pair_rows)
            division = first_nonblank(r.division for pair_rows in bm_pair_rows_list for r in pair_rows)

            evaluated_bms = []
            for pair_rows in bm_pair_rows_list:
                ordered = sorted(pair_rows, key=lambda r: r.month_sort_key)
                bm_name = first_nonblank(r.team_emp_name for r in ordered)
                monthly = [(r.month, r.joint_days) for r in ordered]
                average = sum(days for _month, days in monthly) / len(monthly) if monthly else 0.0
                bm_status = STATUS_PASS if average >= min_days else STATUS_BM_FAIL
                evaluated_bms.append({
                    "bm_name": bm_name,
                    "monthly": monthly,
                    "joint_days": average,
                    "required_days": min_days,
                    "status": bm_status,
                })

            total = len(evaluated_bms)
            passed = sum(1 for b in evaluated_bms if b["status"] == STATUS_PASS)
            failed = total - passed

            if i == 0:
                _log_abm_debug_summary(abm_name, evaluated_bms, total, failed, min_days)

            findings.append({
                "employee_name": abm_name,
                "designation": "ABM",
                "division": division,
                "total_bms": total,
                "passed_bms": passed,
                "failed_bms": failed,
                "status": STATUS_FLAGGED if failed > 0 else STATUS_PASS,
            })
            for bm in evaluated_bms:
                bm_details.append({"manager_name": abm_name, "division": division, **bm})

        deleted_findings = session.query(ManagerWorkAllocationFinding).filter(
            ManagerWorkAllocationFinding.designation == MANAGER_DESIGNATION
        ).delete()
        deleted_details = session.query(ManagerWorkAllocationBMDetail).filter(
            ManagerWorkAllocationBMDetail.manager_designation == MANAGER_DESIGNATION
        ).delete()
        if deleted_findings or deleted_details:
            logger.info(f"Cleared {deleted_findings} ABM finding(s), {deleted_details} BM detail row(s) before rebuilding")

        for finding in findings:
            session.add(ManagerWorkAllocationFinding(
                employee_name=finding["employee_name"],
                designation=finding["designation"],
                division=finding["division"],
                total_bms=finding["total_bms"],
                passed_bms=finding["passed_bms"],
                failed_bms=finding["failed_bms"],
                status=finding["status"],
                last_updated=now,
            ))

        for detail in bm_details:
            session.add(ManagerWorkAllocationBMDetail(
                manager_name=detail["manager_name"],
                manager_designation="ABM",
                division=detail["division"],
                subordinate_name=detail["bm_name"],
                joint_days=detail["joint_days"],
                required_days=detail["required_days"],
                status=detail["status"],
                last_updated=now,
            ))
        session.commit()
    finally:
        session.close()

    flagged_count = sum(1 for f in findings if f["status"] == STATUS_FLAGGED)
    logger.info(
        f"Manager Work Allocation (ABM) processed: {len(records)} record(s), "
        f"{len(pair_groups)} ABM/BM relationship(s), {len(findings)} ABM(s), {flagged_count} flagged"
    )

    return {
        "total_records": len(records),
        "abm_bm_relationships": len(pair_groups),
        "abm_count": len(findings),
        "flagged_count": flagged_count,
        "passed_count": len(findings) - flagged_count,
    }


# --- Queries -----------------------------------------------------------

def get_all_findings() -> list:
    """Every ABM's finding, shaped for the Findings page's table -- see
    FINDINGS_COLUMNS/FINDINGS_HEADINGS above. Filtered to designation="ABM"
    -- RBM's own findings (sharing this same table) never appear here."""
    session = get_config_session()
    try:
        rows = session.query(ManagerWorkAllocationFinding).filter(
            ManagerWorkAllocationFinding.designation == MANAGER_DESIGNATION
        ).order_by(ManagerWorkAllocationFinding.employee_name).all()
        return [
            {
                "employee_name": r.employee_name,
                "designation": r.designation,
                "division": r.division or "",
                "total_bms": r.total_bms,
                "passed_bms": r.passed_bms,
                "failed_bms": r.failed_bms,
                "status": r.status,
            }
            for r in rows
        ]
    finally:
        session.close()


def get_employee_bm_details(manager_name: str) -> list:
    """The BM breakdown for one ABM's own Employee Details view -- BM
    Name/Average Joint Days/Required Days/Status, shaped for
    BM_DETAIL_COLUMNS/BM_DETAIL_HEADINGS above. joint_days is the AVERAGE
    across the rolling window (a formatted string, e.g. "4.17"), and
    required_days/status are the SNAPSHOT values stored at upload time
    (see ManagerWorkAllocationBMDetail's own docstring), so this always
    agrees with the manager's own ManagerWorkAllocationFinding row even if
    Settings changes afterward. Filtered to manager_designation="ABM" --
    an RBM sharing the same name (unlikely, but this table now serves
    both engines) is never mixed in."""
    session = get_config_session()
    try:
        rows = session.query(ManagerWorkAllocationBMDetail).filter(
            ManagerWorkAllocationBMDetail.manager_name == manager_name,
            ManagerWorkAllocationBMDetail.manager_designation == MANAGER_DESIGNATION,
        ).order_by(ManagerWorkAllocationBMDetail.subordinate_name).all()
        return [
            {
                "subordinate_name": r.subordinate_name,
                "joint_days": _format_average(r.joint_days),
                "required_days": _format_average(r.required_days),
                "status": r.status,
            }
            for r in rows
        ]
    finally:
        session.close()


def get_employee_bm_monthly_history(manager_name: str) -> dict:
    """The full month-by-month trend for one ABM's own Employee Details
    view -- read straight from the rolling ManagerWorkAllocationRecord
    store (NOT ManagerWorkAllocationBMDetail, which only holds the
    aggregate), so the table always reflects the exact months currently
    retained in the rolling window, in order, however many there are (up
    to six).

    Returns {"months": [<label>, ...] (oldest to newest, whatever is
    currently retained), "bms": [{"subordinate_name", "monthly": {label:
    days, ...}, "average", "required_days", "status"}, ...]} --
    `"monthly"` only has an entry for a month this pair actually has a
    record for (never zero-padded, per the module's own spec); the UI
    renders a blank cell for a month a BM has no record for."""
    session = get_config_session()
    try:
        records = session.query(ManagerWorkAllocationRecord).filter(
            ManagerWorkAllocationRecord.source_engine == SOURCE_ENGINE,
            ManagerWorkAllocationRecord.emp_name == manager_name,
        ).order_by(ManagerWorkAllocationRecord.month_sort_key.asc()).all()
        details = {
            d.subordinate_name: d
            for d in session.query(ManagerWorkAllocationBMDetail).filter(
                ManagerWorkAllocationBMDetail.manager_name == manager_name,
                ManagerWorkAllocationBMDetail.manager_designation == MANAGER_DESIGNATION,
            ).all()
        }
    finally:
        session.close()

    months_seen: dict = {}
    for r in records:
        months_seen.setdefault(r.month_sort_key, r.month)
    months = [months_seen[key] for key in sorted(months_seen)]

    by_bm = group_by(records, lambda r: r.team_emp_name)
    bms = []
    for bm_name, bm_records in by_bm.items():
        monthly = {r.month: r.joint_days for r in bm_records}
        detail = details.get(bm_name)
        bms.append({
            "subordinate_name": bm_name,
            "monthly": monthly,
            "average": _format_average(detail.joint_days) if detail else "",
            "required_days": _format_average(detail.required_days) if detail else "",
            "status": detail.status if detail else "",
        })
    bms.sort(key=lambda b: b["subordinate_name"])

    return {"months": months, "bms": bms}


def has_data() -> bool:
    """True once at least one ABM report has been processed -- used by the
    Findings page to distinguish "never uploaded" from "uploaded, everyone
    passed". Filtered to designation="ABM"."""
    session = get_config_session()
    try:
        return session.query(ManagerWorkAllocationFinding).filter(
            ManagerWorkAllocationFinding.designation == MANAGER_DESIGNATION
        ).first() is not None
    finally:
        session.close()


def get_current_cycle_label() -> str | None:
    """Human-readable retained rolling-window range for THIS engine (e.g.
    "Feb 2026 – Jul 2026"), for the Findings page's own "Current cycle"
    display -- see app.manager_work_allocation_shared.get_retained_month_range_label.
    None if no ABM report has ever been processed."""
    session = get_config_session()
    try:
        return get_retained_month_range_label(session, ManagerWorkAllocationRecord, SOURCE_ENGINE)
    finally:
        session.close()
