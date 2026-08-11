"""Core business logic for the Manager Work Allocation engine's RBM phase
-- built on the EXACT SAME rolling six-month history architecture as the
ABM engine (app/manager_work_allocation_service.py), reusing
app/manager_work_allocation_shared.py's month-parsing, intra-month-merge,
and rolling-window sync/trim helpers verbatim rather than a second copy.
Only the parts of the business rule that genuinely differ between ABM and
RBM live here.

RBM business rule (UNCHANGED by the 2026-08-05 rolling-history redesign --
see below for exactly what DID change):
- Only rows where Emp Designation="RBM" AND Team Emp Designation="BM" are
  analyzed; every other designation combination is ignored entirely (the
  ABM engine handles its own rows completely separately).
- Unlike ABM (a rolling AVERAGE per BM), an RBM is NOT measured by how
  MANY days they worked with a BM -- only WHETHER they worked with that BM
  at least once. Total Joint Days > 0 means that BM is "covered"; = 0
  means "not covered". This rule itself has not changed.
- Flagging is tiered by how many unique BMs report to the RBM -- more BMs
  means more tolerance for a missed one before flagging, read fresh on
  every Run Analysis from
  app.manager_work_allocation_parameters_service.get_rbm_flag_tiers()
  (Settings-configurable; see that module's own docstring). This rule
  itself has not changed either.

What DID change (2026-08-05, a business-process correction, not a bug
fix): what looked like duplicate manager/subordinate rows within one
upload were never duplicates -- each uploaded report already contains
several months of history, and an RBM/BM pair legitimately appears once
per month it has a record for. "Total Joint Days" is now correctly SUMMED
across only the CURRENT ROLLING SIX-MONTH WINDOW (see
app.manager_work_allocation_shared.sync_rolling_window, the one place
either engine ever writes to ManagerWorkAllocationRecord) -- not summed
across every row ever uploaded regardless of month, which is what the
pre-redesign architecture actually did (a "duplicate merge" that silently
summed different months together). The threshold logic itself
(`_tier_for`/`_evaluate_rbm` below) is untouched.

Both engines write to the SAME three tables (ManagerWorkAllocationRecord/
Finding/BMDetail) -- every query and the sync/trim step here are scoped to
designation="RBM" / source_engine="RBM", so this engine's own Run Analysis
can never see or wipe the ABM engine's rows, and vice versa.

Scope: this module ONLY calculates and generates findings for the RBM
engine. It does not send emails, sync to the cloud, or export.
"""

from datetime import datetime

from loguru import logger

from app.manager_work_allocation_parameters_service import get_rbm_flag_tiers
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
STATUS_BM_COVERED = "Yes"
STATUS_BM_NOT_COVERED = "No"

# This engine ONLY analyzes RBM (manager) / BM (subordinate) rows -- every
# other Emp Designation/Team Emp Designation combination is ignored.
MANAGER_DESIGNATION = "RBM"
SUBORDINATE_DESIGNATION = "BM"

# Scopes every query/write below to rows THIS engine itself wrote, so
# ABM's own rows (sharing the same tables) are never read or touched here.
SOURCE_ENGINE = "RBM"

FINDINGS_COLUMNS = (
    "employee_name", "division", "total_bms", "passed_bms", "failed_bms",
    "coverage_percent", "status", "reason",
)
FINDINGS_HEADINGS = {
    "employee_name": "RBM Name",
    "division": "Division",
    "total_bms": "Total Unique BMs",
    "passed_bms": "Covered BMs",
    "failed_bms": "Missed BMs",
    "coverage_percent": "Coverage %",
    "status": "Status",
    "reason": "Reason",
}

BM_DETAIL_COLUMNS = ("subordinate_name", "joint_days", "status", "reason")
BM_DETAIL_HEADINGS = {
    "subordinate_name": "BM Name",
    "joint_days": "Total Joint Days",
    "status": "Covered",
    "reason": "Reason",
}


def validate_rbm_flag_tiers(tiers: list) -> list:
    """Returns a list of human-readable error messages -- empty means
    valid. Called by the Settings page BEFORE ever saving a change (never
    by the calculation engine itself, which trusts whatever's already been
    validated and stored). Enforces exactly the four rules the module's
    own spec requires:
    - Values must be non-negative integers (min, max, missed).
    - Every minimum must be <= its own maximum (bounded tiers only).
    - "Unlimited" (max=None) may only appear on the LAST tier.
    - Ranges must not overlap with one another."""
    errors = []
    if not tiers:
        return ["At least one tier is required."]

    def _is_nonneg_int(value) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    for i, tier in enumerate(tiers, start=1):
        min_value = tier.get("min")
        max_value = tier.get("max")
        missed_value = tier.get("missed")

        if not _is_nonneg_int(min_value):
            errors.append(f"Row {i}: Minimum BM Count must be a non-negative whole number.")
        if not _is_nonneg_int(missed_value):
            errors.append(f"Row {i}: Allowed Missed BMs must be a non-negative whole number.")
        if max_value is not None:
            if not _is_nonneg_int(max_value):
                errors.append(f"Row {i}: Maximum BM Count must be a non-negative whole number, or Unlimited.")
            elif _is_nonneg_int(min_value) and min_value > max_value:
                errors.append(f"Row {i}: Minimum BM Count ({min_value}) must be <= Maximum BM Count ({max_value}).")
        if max_value is None and i != len(tiers):
            errors.append(f"Row {i}: 'Unlimited' is only allowed on the final row.")

    if errors:
        return errors  # rows are malformed -- skip overlap checking against invalid data

    intervals = [(t["min"], t["max"] if t["max"] is not None else float("inf")) for t in tiers]
    for i in range(len(intervals)):
        for j in range(i + 1, len(intervals)):
            a_min, a_max = intervals[i]
            b_min, b_max = intervals[j]
            if a_min <= b_max and b_min <= a_max:
                a_label = f"{tiers[i]['min']}-{tiers[i]['max'] if tiers[i]['max'] is not None else 'Unlimited'}"
                b_label = f"{tiers[j]['min']}-{tiers[j]['max'] if tiers[j]['max'] is not None else 'Unlimited'}"
                errors.append(f"Row {i + 1} ({a_label}) and Row {j + 1} ({b_label}) overlap.")

    return errors


def _format_average(value: float) -> str:
    """'4' for a whole number, '4.17' otherwise -- same formatting as
    app.manager_work_allocation_service's own _format_average, duplicated
    rather than imported (this engine intentionally doesn't import from
    the ABM engine's own module -- see app/manager_work_allocation_parser.py's
    module docstring for why each engine stays independently readable)."""
    rounded = round(value, 2)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:g}"


def _tier_label(tier: dict) -> str:
    return f"{tier['min']}-{tier['max']}" if tier["max"] is not None else f"{tier['min']}+"


def _tier_for(total_bms: int, tiers: list):
    """Returns (missed_threshold, display_label) for the tier `total_bms`
    falls into, given the CURRENT Settings-configured `tiers` -- or
    (None, None) if `total_bms` is below every configured tier's own
    minimum -- never auto-flagged, but still fully calculated and shown.
    UNCHANGED by the 2026-08-05 rolling-history redesign."""
    for tier in tiers:
        low, high = tier["min"], tier["max"]
        if high is None:
            if total_bms >= low:
                return tier["missed"], _tier_label(tier)
        elif low <= total_bms <= high:
            return tier["missed"], _tier_label(tier)
    return None, None


def _evaluate_rbm(name: str, division: str, pair_rows_list: list, tiers: list) -> tuple:
    """Evaluates one RBM's BM pairs against the CURRENT Settings-configured
    `tiers`. `pair_rows_list` is a list of (one list of retained
    ManagerWorkAllocationRecord rows per BM pair) -- each BM's own Total
    Joint Days is the SUM across every month retained in the rolling
    window for that pair (UNCHANGED rule; only which months get summed
    into it changed -- see module docstring). Returns (finding dict,
    evaluated_bms list, tier_label, missed_threshold) -- the last two are
    only needed by the debug summary logger, not persisted."""
    evaluated_bms = []
    for pair_rows in pair_rows_list:
        ordered = sorted(pair_rows, key=lambda r: r.month_sort_key)
        bm_name = first_nonblank(r.team_emp_name for r in ordered)
        monthly = [(r.month, r.joint_days) for r in ordered]
        total_joint_days = sum(days for _month, days in monthly)
        covered = total_joint_days > 0
        evaluated_bms.append({
            "bm_name": bm_name,
            "monthly": monthly,
            "joint_days": total_joint_days,
            "status": STATUS_BM_COVERED if covered else STATUS_BM_NOT_COVERED,
            "reason": (
                "Worked with at least once during the rolling window" if covered
                else "Not worked with during the rolling window"
            ),
        })

    total = len(evaluated_bms)
    covered_count = sum(1 for b in evaluated_bms if b["status"] == STATUS_BM_COVERED)
    missed_count = total - covered_count
    coverage_percent = (covered_count / total * 100) if total else 0.0

    missed_threshold, tier_label = _tier_for(total, tiers)
    plural = "s" if missed_count != 1 else ""
    if missed_threshold is not None and missed_count >= missed_threshold:
        status = STATUS_FLAGGED
        reason = f"{missed_count} BM{plural} not worked with during the rolling window."
    else:
        status = STATUS_PASS
        if missed_threshold is None:
            floor = min((t["min"] for t in tiers), default=None)
            floor_text = f"{floor}-BM" if floor is not None else "configured"
            reason = f"Below the {floor_text} auto-flag floor -- not automatically flagged."
        elif missed_count == 0:
            reason = "All BMs worked with at least once during the rolling window."
        else:
            reason = (
                f"{missed_count} BM{plural} not worked with, but below this tier's "
                f"{missed_threshold}+ flag threshold."
            )

    finding = {
        "employee_name": name,
        "designation": "RBM",
        "division": division,
        "total_bms": total,
        "passed_bms": covered_count,
        "failed_bms": missed_count,
        "coverage_percent": coverage_percent,
        "status": status,
        "reason": reason,
    }
    return finding, evaluated_bms, tier_label, missed_threshold


def _log_rbm_debug_summary(finding: dict, evaluated_bms: list, tier_label) -> None:
    """Logs the post-rolling-window BM coverage breakdown for ONE sample
    RBM (the first processed each run) -- matches the module's own
    requested debug format, with each covered BM's own month-by-month
    history shown too."""
    covered = [b for b in evaluated_bms if b["status"] == STATUS_BM_COVERED]
    missed = [b for b in evaluated_bms if b["status"] == STATUS_BM_NOT_COVERED]

    lines = [
        f"RBM: {finding['employee_name']}",
        "",
        f"Unique BMs: {finding['total_bms']}",
        "",
        "Covered:",
    ]
    for b in covered:
        monthly_text = ", ".join(f"{month}={days}" for month, days in b["monthly"])
        lines.append(f"  {b['bm_name']} ({monthly_text})")
    lines.append("")
    lines.append("Missed:")
    lines.extend(f"  {b['bm_name']}" for b in missed)
    lines.append("")
    lines.append(f"Missed Count: {finding['failed_bms']}")
    lines.append("")
    lines.append(f"Threshold: {tier_label or 'N/A (below auto-flag floor)'}")
    lines.append("")
    lines.append(f"Result: {finding['status'].upper()}")
    lines.append("")
    lines.append(f"Reason: {finding['reason']}")
    logger.info("Manager Work Allocation (RBM) debug summary (sample RBM):\n" + "\n".join(lines))


def process_rbm_report(records: list) -> dict:
    """Full pipeline: filter to RBM/BM rows, merge genuine intra-month
    duplicates, sync the new monthly records into the rolling six-month
    window (upsert + trim -- SAME shared helper the ABM engine uses),
    evaluate every retained pair's coverage against the current
    Settings-configured tiers (UNCHANGED threshold logic), and store the
    result -- ManagerWorkAllocationRecord accumulates/rolls per above;
    ManagerWorkAllocationFinding + ManagerWorkAllocationBMDetail are
    full-replaced (derived views, recomputed fresh from the retained
    window every run), SCOPED to this engine's own rows only.

    Returns: {total_records, rbm_bm_relationships, rbm_count,
    flagged_count, passed_count}.
    """
    tiers = get_rbm_flag_tiers()

    rbm_bm_rows = [
        r for r in records
        if is_designation(r["emp_designation"], MANAGER_DESIGNATION)
        and is_designation(r["team_emp_designation"], SUBORDINATE_DESIGNATION)
    ]
    log_designation_filter_diagnostics("RBM", records, MANAGER_DESIGNATION, SUBORDINATE_DESIGNATION, rbm_bm_rows)
    new_monthly_records = merge_same_month_duplicates(rbm_bm_rows)
    logger.info(
        f"Manager Work Allocation (RBM) diagnostics: {len(rbm_bm_rows)} RBM/BM row(s) -> "
        f"{len(new_monthly_records)} record(s) after same-month-duplicate merge"
    )

    now = datetime.now()
    session = get_config_session()
    try:
        retained_rows = sync_rolling_window(
            session, ManagerWorkAllocationRecord, SOURCE_ENGINE, new_monthly_records, now,
        )
        logger.info(
            f"Manager Work Allocation (RBM) diagnostics: {len(retained_rows)} record(s) "
            "retained after rolling-window sync/trim"
        )

        pair_groups = group_by(retained_rows, lambda r: (r.emp_code, r.team_emp_code))
        rbm_groups: dict = {}
        rbm_order: list = []
        for (_emp_code, _team_emp_code), pair_rows in pair_groups.items():
            rbm_key = pair_rows[0].emp_code or pair_rows[0].emp_name
            if rbm_key not in rbm_groups:
                rbm_groups[rbm_key] = []
                rbm_order.append(rbm_key)
            rbm_groups[rbm_key].append(pair_rows)

        findings = []
        bm_details = []
        for i, rbm_key in enumerate(rbm_order):
            pair_rows_list = rbm_groups[rbm_key]
            rbm_name = first_nonblank(r.emp_name for pair_rows in pair_rows_list for r in pair_rows)
            division = first_nonblank(r.division for pair_rows in pair_rows_list for r in pair_rows)

            finding, evaluated_bms, tier_label, _missed_threshold = _evaluate_rbm(
                rbm_name, division, pair_rows_list, tiers,
            )
            findings.append(finding)
            for bm in evaluated_bms:
                bm_details.append({"manager_name": rbm_name, "division": division, **bm})

            if i == 0:
                _log_rbm_debug_summary(finding, evaluated_bms, tier_label)

        deleted_findings = session.query(ManagerWorkAllocationFinding).filter(
            ManagerWorkAllocationFinding.designation == MANAGER_DESIGNATION
        ).delete()
        deleted_details = session.query(ManagerWorkAllocationBMDetail).filter(
            ManagerWorkAllocationBMDetail.manager_designation == MANAGER_DESIGNATION
        ).delete()
        if deleted_findings or deleted_details:
            logger.info(f"Cleared {deleted_findings} RBM finding(s), {deleted_details} BM detail row(s) before rebuilding")

        for finding in findings:
            session.add(ManagerWorkAllocationFinding(
                employee_name=finding["employee_name"],
                designation=finding["designation"],
                division=finding["division"],
                total_bms=finding["total_bms"],
                passed_bms=finding["passed_bms"],
                failed_bms=finding["failed_bms"],
                coverage_percent=finding["coverage_percent"],
                reason=finding["reason"],
                status=finding["status"],
                last_updated=now,
            ))

        for detail in bm_details:
            session.add(ManagerWorkAllocationBMDetail(
                manager_name=detail["manager_name"],
                manager_designation=MANAGER_DESIGNATION,
                division=detail["division"],
                subordinate_name=detail["bm_name"],
                joint_days=detail["joint_days"],
                required_days=0,
                status=detail["status"],
                reason=detail["reason"],
                last_updated=now,
            ))
        session.commit()
    finally:
        session.close()

    flagged_count = sum(1 for f in findings if f["status"] == STATUS_FLAGGED)
    logger.info(
        f"Manager Work Allocation (RBM) processed: {len(records)} record(s), "
        f"{len(pair_groups)} RBM/BM relationship(s), {len(findings)} RBM(s), {flagged_count} flagged"
    )

    return {
        "total_records": len(records),
        "rbm_bm_relationships": len(pair_groups),
        "rbm_count": len(findings),
        "flagged_count": flagged_count,
        "passed_count": len(findings) - flagged_count,
    }


# --- Queries -----------------------------------------------------------

def get_all_findings() -> list:
    """Every RBM's finding, shaped for the Findings page's table -- see
    FINDINGS_COLUMNS/FINDINGS_HEADINGS above. Filtered to designation="RBM"
    -- ABM's own findings (sharing this same table) never appear here."""
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
                "coverage_percent": f"{r.coverage_percent:.1f}%" if r.coverage_percent is not None else "0.0%",
                "status": r.status,
                "reason": r.reason or "",
            }
            for r in rows
        ]
    finally:
        session.close()


def get_employee_bm_details(manager_name: str) -> list:
    """The BM breakdown for one RBM's own Employee Details view -- BM
    Name/Total Joint Days/Covered/Reason, shaped for BM_DETAIL_COLUMNS/
    BM_DETAIL_HEADINGS above. Filtered to manager_designation="RBM" -- an
    ABM sharing the same name is never mixed in."""
    session = get_config_session()
    try:
        rows = session.query(ManagerWorkAllocationBMDetail).filter(
            ManagerWorkAllocationBMDetail.manager_name == manager_name,
            ManagerWorkAllocationBMDetail.manager_designation == MANAGER_DESIGNATION,
        ).order_by(ManagerWorkAllocationBMDetail.subordinate_name).all()
        return [
            {
                "subordinate_name": r.subordinate_name,
                "joint_days": r.joint_days,
                "status": r.status,
                "reason": r.reason or "",
            }
            for r in rows
        ]
    finally:
        session.close()


def get_employee_bm_monthly_history(manager_name: str) -> dict:
    """The full month-by-month trend for one RBM's own Employee Details
    view -- read straight from the rolling ManagerWorkAllocationRecord
    store, mirroring app.manager_work_allocation_service's own
    get_employee_bm_monthly_history exactly (see that function's own
    docstring for the return shape).

    DISPLAY ONLY: the "average" key here is a genuine average joint days
    per retained month (sum of this pair's own monthly joint_days / number
    of months this pair has a record for), computed fresh from
    ManagerWorkAllocationRecord -- NOT read from
    ManagerWorkAllocationBMDetail.joint_days, which continues to store the
    SUM and continues to drive this engine's own covered-if-any-joint-work
    rule (`_evaluate_rbm`'s `covered = total_joint_days > 0`) and the
    BM-count-tiered missed-BM flagging threshold, both entirely unchanged
    by this. Changing what this one column SHOWS does not change what
    counts as "covered" or what gets flagged -- see this module's own
    docstring for why RBM's coverage rule is deliberately sum-based, not
    average-based."""
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
        true_average = sum(r.joint_days for r in bm_records) / len(bm_records) if bm_records else 0.0
        bms.append({
            "subordinate_name": bm_name,
            "monthly": monthly,
            "average": _format_average(true_average),
            "status": detail.status if detail else "",
        })
    bms.sort(key=lambda b: b["subordinate_name"])

    return {"months": months, "bms": bms}


def has_data() -> bool:
    """True once at least one RBM report has been processed -- used by the
    Findings page to distinguish "never uploaded" from "uploaded, everyone
    passed". Filtered to designation="RBM"."""
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
    None if no RBM report has ever been processed."""
    session = get_config_session()
    try:
        return get_retained_month_range_label(session, ManagerWorkAllocationRecord, SOURCE_ENGINE)
    finally:
        session.close()
