"""Core business logic for Work Distribution: turns parsed doctor rows
(app/work_distribution_parser.py) into per-employee KPI findings (BM/ABM),
using the configurable thresholds in
app/work_distribution_parameters_service.py -- never hardcoded values.

Full-replacement architecture, same as Inventory's Threshold/Replenishment
tables (app/threshold_service.py): an uploaded Work Distribution report is
a complete monthly snapshot, so every existing WorkDistributionDoctor/
WorkDistributionFinding row is deleted and rebuilt from THIS upload's data
on every run. A finding is a snapshot of the thresholds in effect at
upload time -- changing a Settings value afterward never retroactively
re-flags an already-generated finding, only the next upload does.

KPI rules (see the module's own spec):
- BM: only doctors whose "Category" column normalizes to "B-RGD" (see
  `_is_b_rgd`) AND whose "BM Code" column names this BM -- a "General" or
  "A-RGD" Category doctor is excluded entirely from every BM calculation
  (Total Doctors, Total Calls, Missed Doctors, Coverage %), confirmed
  against a real uploaded report: without this filter a BM's book averaged
  ~125 doctors instead of the spec's own "approximately 50" (Category and
  ABM RGD are two independent columns on the same row -- a doctor can be
  Category="B-RGD" and ABM RGD="A-RGD" at once, so a doctor may legitimately
  count toward BOTH a BM's book and an ABM's book). Flag if Total Calls
  (sum of BM Visit counts) < Minimum Calls, OR Missed Doctor % (0-visit
  doctors / total, as a percentage) >= its threshold, OR Poor Coverage %
  (<2-visit doctors / total, as a percentage) >= its threshold.
- ABM: only doctors whose "ABM RGD" column normalizes to "A-RGD" (see
  `_is_a_rgd`) AND whose "ABM Code" column names this ABM -- a blank or
  non-"A-RGD" row is excluded entirely, not counted as 0 visits. Flag if
  Missed Doctors (raw count, not a percentage) >= its threshold, OR Doctors
  with <2 Visits (raw count) >= its threshold.

Employee identity (2026-08 fix): a BM/ABM is grouped, aggregated, and
deduplicated by their "BM Code"/"ABM Code" value -- the hierarchy's own
Employee Code -- NEVER by name. Two different real employees who happen to
share a name (e.g. two "Rahul Sharma"s with different codes) previously
collapsed into one incorrectly-combined book; they no longer do. The
display name shown on findings/doctor lists is resolved from the shared
hierarchy dataset (app.hierarchy_parser.find_by_employee_code) purely for
readability -- falls back to showing the code itself if that code has no
hierarchy match, never blank, and is never used as a key for anything.

Scope: this module ONLY parses, calculates KPIs, and generates findings.
It does not send emails, sync to the cloud, or export -- see the module's
Phase 2 instructions.
"""

from datetime import datetime

from loguru import logger

from app.doj_eligibility_service import (
    NOT_YET_JOINED,
    NOT_YET_JOINED_LABEL,
    load_doj_by_code,
    load_doj_by_name,
    monthly_status,
    resolve_doj,
)
from app.hierarchy_parser import find_by_employee_code
from app.manager_work_allocation_shared import parse_month
from app.work_distribution_parameters_service import get_all as get_parameters
from database.connection import get_config_session
from database.models import WorkDistributionDoctor, WorkDistributionFinding

STATUS_HEALTHY = "Healthy"
STATUS_FLAGGED = "Flagged"
# A BM/ABM whose DOJ (see app.doj_eligibility_service) falls after this
# period's own month-end -- shown, but excluded from every aggregate
# (get_dashboard_summary's average_coverage, and never eligible to be
# "Flagged" since that status is never assigned to them at all).
STATUS_NOT_YET_JOINED = NOT_YET_JOINED_LABEL

FINDINGS_COLUMNS = (
    "employee_name", "designation", "division", "total_doctors", "total_calls",
    "missed_doctors", "poor_coverage_doctors", "status", "reason",
)
FINDINGS_HEADINGS = {
    "employee_name": "Employee Name",
    "designation": "Designation",
    "division": "Division",
    "total_doctors": "Total Doctors",
    "total_calls": "Total Calls",
    "missed_doctors": "Missed Doctors",
    "poor_coverage_doctors": "Doctors with <2 Visits",
    "status": "Status",
    "reason": "Reason",
}


def _normalize_dash(value) -> str:
    return str(value).strip().upper().replace("–", "-").replace("—", "-")


def _is_a_rgd(value) -> bool:
    """True if an "ABM RGD" cell marks this doctor as ABM-eligible. Only
    an exact "A-RGD" (case-insensitive, whitespace-trimmed, either dash
    style) counts -- a blank or any other value means this doctor is
    excluded from ABM calculations entirely, per the module's own spec
    ("Ignore blank ABM RGD rows")."""
    if not value:
        return False
    return _normalize_dash(value) == "A-RGD"


def _is_b_rgd(value) -> bool:
    """True if a "Category" cell marks this doctor as BM-eligible. Only an
    exact "B-RGD" (case-insensitive, whitespace-trimmed, either dash style)
    counts -- "General" and "A-RGD" Category doctors are excluded entirely
    from every BM calculation. Category is independent of the "ABM RGD"
    column (see `_is_a_rgd`) -- a doctor can be Category="B-RGD" and ABM
    RGD="A-RGD" simultaneously, counting toward both a BM's and an ABM's
    book at once; that overlap is expected, not a bug."""
    if not value:
        return False
    return _normalize_dash(value) == "B-RGD"


def _is_vacant(code, name: str = "") -> bool:
    """True if a BM/ABM Code or Name cell marks the position as vacant --
    mirrors app.hierarchy_parser's own `_is_vacant` convention exactly
    (case-insensitive "vacant" substring). A vacant position has no real
    employee behind it, so it must never be evaluated as one -- confirmed
    against the real uploaded report: an un-filtered vacant BM/ABM cell
    produced a finding with 100% missed doctors (nobody there to visit
    them), which is a data artifact, not a real KPI violation to flag or
    eventually email.

    Checks BOTH `code` and `name` (2026-08 vacancy-detection fix): an
    older real file embedded vacancy directly in the identity/Code value
    itself (e.g. "Vacant_Lokesh Kumar Singh" as the BM Code), which the
    `code` check alone still catches; the current real file instead gives
    a vacant position an ordinary-looking Code (e.g. "V02677") and puts
    "Vacant_<who>" in a separate BM Name/ABM Name column (see
    app.work_distribution_parser's OPTIONAL_COLUMN_SYNONYMS) -- the `name`
    check catches that. `name` is optional (blank on an older file with no
    Name column) so this is purely additive, never a regression for a
    file using the original convention."""
    return (bool(code) and "vacant" in str(code).lower()) or (bool(name) and "vacant" in str(name).lower())


def _combine_reasons(reasons: list) -> str:
    """A single failed KPI gets its own specific sentence; more than one
    is combined into one readable summary, per the module's own spec --
    never a bare "Flagged" with no detail."""
    if not reasons:
        return "Meets all KPI targets"
    if len(reasons) == 1:
        return reasons[0]
    return "Multiple KPI violations: " + ", ".join(reasons)


def _group_by(doctors: list, key_fn) -> dict:
    groups: dict = {}
    for doctor in doctors:
        key = key_fn(doctor)
        if not key:
            continue
        groups.setdefault(key, []).append(doctor)
    return groups


def _first_nonblank(values):
    for value in values:
        if value:
            return value
    return None


def _resolve_employee_name(code: str) -> str:
    """Display name for a BM/ABM Employee Code, resolved from the shared
    hierarchy dataset (the same one every other module in this app already
    uses -- see app.hierarchy_parser.find_by_employee_code). Falls back to
    showing the code itself when it has no hierarchy match, never blank --
    this is display only; the code, never this name, is what
    matching/grouping/aggregation/deduplication use."""
    row = find_by_employee_code(code)
    return row["employee_name"] if row and row.get("employee_name") else code


def _evaluate_bm(code: str, group: list, params: dict) -> dict:
    name = _resolve_employee_name(code)
    total_doctors = len(group)
    total_calls = sum(d["bm_visit_count"] for d in group)
    missed = sum(1 for d in group if d["bm_visit_count"] == 0)
    poor_coverage = sum(1 for d in group if d["bm_visit_count"] < 2)
    missed_pct = (missed / total_doctors * 100) if total_doctors else 0.0
    coverage_pct = (poor_coverage / total_doctors * 100) if total_doctors else 0.0

    reasons = []
    if total_calls < params["bm_minimum_calls"]:
        reasons.append("Calls below target")
    if missed_pct >= params["bm_missed_doctor_percent"]:
        reasons.append("Missed doctor percentage exceeded")
    if coverage_pct >= params["bm_coverage_percent"]:
        reasons.append("Coverage below minimum")

    return {
        "employee_code": code,
        "employee_name": name,
        "designation": "BM",
        "division": _first_nonblank(d["division"] for d in group),
        "total_doctors": total_doctors,
        "total_calls": total_calls,
        "missed_doctors": missed,
        "poor_coverage_doctors": poor_coverage,
        "status": STATUS_FLAGGED if reasons else STATUS_HEALTHY,
        "reason": _combine_reasons(reasons),
    }


def _evaluate_abm(code: str, group: list, params: dict) -> dict:
    name = _resolve_employee_name(code)
    total_doctors = len(group)
    total_calls = sum(d["abm_visit_count"] for d in group)
    missed = sum(1 for d in group if d["abm_visit_count"] == 0)
    poor_coverage = sum(1 for d in group if d["abm_visit_count"] < 2)

    reasons = []
    if missed >= params["abm_missed_doctors"]:
        reasons.append("Missed doctor count exceeded")
    if poor_coverage >= params["abm_coverage_doctors"]:
        reasons.append("Coverage below minimum")

    return {
        "employee_code": code,
        "employee_name": name,
        "designation": "ABM",
        "division": _first_nonblank(d["division"] for d in group),
        "total_doctors": total_doctors,
        "total_calls": total_calls,
        "missed_doctors": missed,
        "poor_coverage_doctors": poor_coverage,
        "status": STATUS_FLAGGED if reasons else STATUS_HEALTHY,
        "reason": _combine_reasons(reasons),
    }


def _not_yet_joined_finding(code: str, designation: str, group: list) -> dict:
    """A finding for a BM/ABM whose own DOJ is after this period's
    month-end (see app.doj_eligibility_service) -- shown, exactly like any
    other employee, but never evaluated against the KPI thresholds (there
    is no real activity to judge yet) and excluded from every aggregate
    that reads WorkDistributionFinding.status (see get_dashboard_summary's
    average_coverage; the notification batch's own "status != Flagged"
    filter already skips this status for free)."""
    return {
        "employee_code": code,
        "employee_name": _resolve_employee_name(code),
        "designation": designation,
        "division": _first_nonblank(d["division"] for d in group),
        "total_doctors": len(group),
        "total_calls": 0,
        "missed_doctors": 0,
        "poor_coverage_doctors": 0,
        "status": STATUS_NOT_YET_JOINED,
        "reason": "Not yet joined as of this period.",
    }


def process_work_distribution_report(doctors: list) -> dict:
    """Full pipeline: group parsed doctors into BM/ABM books, evaluate each
    against the current Settings thresholds, and store the result --
    full-replace WorkDistributionDoctor + WorkDistributionFinding.

    Returns: {total_doctors, total_employees, bm_count, abm_count,
    flagged_count, healthy_count}.
    """
    params = get_parameters()

    bm_eligible = [
        d for d in doctors
        if d["bm_code"] and not _is_vacant(d["bm_code"], d.get("bm_name")) and _is_b_rgd(d["category"])
    ]
    bm_groups = _group_by(bm_eligible, lambda d: d["bm_code"])
    abm_eligible = [
        d for d in doctors
        if d["abm_code"] and not _is_vacant(d["abm_code"], d.get("abm_name")) and _is_a_rgd(d["abm_rgd"])
    ]
    abm_groups = _group_by(abm_eligible, lambda d: d["abm_code"])

    # This period's own (year, month), from the same "BM Visit <Month>"
    # header text app.work_distribution_parser already reduces to
    # period_label -- reusing app.manager_work_allocation_shared's own
    # month parser rather than a second copy (it already tolerates every
    # real-world spelling this report uses). None if it can't be
    # determined, in which case monthly_status() below always returns
    # ACTIVE -- DOJ eligibility is a no-op rather than a guess.
    period = parse_month(doctors[0]["period_label"]) if doctors else None
    period_year, period_month = (period[0], period[1]) if period else (None, None)
    # Code-first, name-fallback -- same resolve_doj() Manager Work
    # Allocation's own ABM/RBM engines already use, now that BM/ABM Code
    # is available here too (2026-08 fix; previously this could only ever
    # look up by name, the same collision risk being fixed everywhere else
    # in this module).
    doj_by_code = load_doj_by_code()
    doj_by_name = load_doj_by_name()

    def _doj_status(code: str) -> str:
        doj = resolve_doj(code, _resolve_employee_name(code), doj_by_code, doj_by_name)
        return monthly_status(doj, period_year, period_month)

    findings = [
        _not_yet_joined_finding(code, "BM", group) if _doj_status(code) == NOT_YET_JOINED
        else _evaluate_bm(code, group, params)
        for code, group in bm_groups.items()
    ]
    findings += [
        _not_yet_joined_finding(code, "ABM", group) if _doj_status(code) == NOT_YET_JOINED
        else _evaluate_abm(code, group, params)
        for code, group in abm_groups.items()
    ]

    now = datetime.now()
    session = get_config_session()
    try:
        deleted_doctors = session.query(WorkDistributionDoctor).delete()
        deleted_findings = session.query(WorkDistributionFinding).delete()
        if deleted_doctors or deleted_findings:
            logger.info(
                f"Cleared {deleted_doctors} doctor row(s) and {deleted_findings} finding(s) "
                "before rebuilding from this upload"
            )

        for doctor in doctors:
            session.add(WorkDistributionDoctor(
                division=doctor["division"] or None,
                doctor_code=doctor["doctor_code"] or None,
                doctor_name=doctor["doctor_name"],
                speciality=doctor["speciality"] or None,
                category=doctor["category"] or None,
                abm_rgd=doctor["abm_rgd"] or None,
                city=doctor["city"] or None,
                hq=doctor["hq"] or None,
                region=doctor["region"] or None,
                bm_code=doctor["bm_code"] or None,
                abm_code=doctor["abm_code"] or None,
                bm_visit_count=doctor["bm_visit_count"],
                abm_visit_count=doctor["abm_visit_count"],
                period_label=doctor.get("period_label") or None,
                last_updated=now,
            ))

        for finding in findings:
            session.add(WorkDistributionFinding(
                employee_code=finding["employee_code"] or None,
                employee_name=finding["employee_name"],
                designation=finding["designation"],
                division=finding["division"],
                total_doctors=finding["total_doctors"],
                total_calls=finding["total_calls"],
                missed_doctors=finding["missed_doctors"],
                poor_coverage_doctors=finding["poor_coverage_doctors"],
                status=finding["status"],
                reason=finding["reason"],
                last_updated=now,
            ))
        session.commit()
    finally:
        session.close()

    flagged_count = sum(1 for f in findings if f["status"] == STATUS_FLAGGED)
    logger.info(
        f"Work Distribution processed: {len(doctors)} doctor row(s), {len(bm_groups)} BM(s), "
        f"{len(abm_groups)} ABM(s), {flagged_count} flagged"
    )

    return {
        "total_doctors": len(doctors),
        "total_employees": len(findings),
        "bm_count": len(bm_groups),
        "abm_count": len(abm_groups),
        "flagged_count": flagged_count,
        "healthy_count": len(findings) - flagged_count,
    }


# --- Queries ---------------------------------------------------------------

def get_all_findings() -> list:
    """Every employee's finding, shaped for the Findings page's table --
    see FINDINGS_COLUMNS/FINDINGS_HEADINGS above. Also carries
    `employee_code` (not one of FINDINGS_COLUMNS, so it's never rendered
    as a table column) for callers like the Employee Details page that
    need the real identity, not just the display name -- see
    get_employee_doctors below."""
    session = get_config_session()
    try:
        rows = session.query(WorkDistributionFinding).order_by(
            WorkDistributionFinding.designation, WorkDistributionFinding.employee_name
        ).all()
        return [
            {
                "employee_code": r.employee_code or "",
                "employee_name": r.employee_name,
                "designation": r.designation,
                "division": r.division or "",
                "total_doctors": r.total_doctors,
                "total_calls": r.total_calls,
                "missed_doctors": r.missed_doctors,
                "poor_coverage_doctors": r.poor_coverage_doctors,
                "status": r.status,
                "reason": r.reason or "",
            }
            for r in rows
        ]
    finally:
        session.close()


def get_current_period_label() -> str:
    """The uploaded report's own period label (e.g. "Jul-2026"), read from
    whichever WorkDistributionDoctor rows are currently stored -- this is a
    full-replace, single-month-snapshot table (see module docstring), so
    every row shares the same period_label after a given upload. Used by
    app.work_distribution_notification_service for each flagged employee's
    own "Analysis Period" field. Empty string if nothing has been uploaded
    yet, or the upload didn't carry a recognizable period label."""
    session = get_config_session()
    try:
        row = session.query(WorkDistributionDoctor.period_label).filter(
            WorkDistributionDoctor.period_label.isnot(None)
        ).first()
        return row[0] if row else ""
    finally:
        session.close()


def get_dashboard_summary() -> dict:
    """Dashboard KPI numbers: total_employees, total_doctors,
    flagged_employees, average_coverage (the average, across every
    employee's own book, of the percentage of that book's doctors visited
    2+ times -- each employee weighted equally, not by book size)."""
    session = get_config_session()
    try:
        findings = session.query(WorkDistributionFinding).all()
        doctor_count = session.query(WorkDistributionDoctor).count()
    finally:
        session.close()

    total_employees = len(findings)
    flagged = sum(1 for f in findings if f.status == STATUS_FLAGGED)

    # A "NOT YET JOINED" employee (see app.doj_eligibility_service) has no
    # real coverage to average in -- excluded here entirely rather than
    # counted as a 0%, per that module's own rule. Still counted in
    # total_employees above (a headcount, not a performance aggregate).
    coverage_values = [
        ((f.total_doctors - f.poor_coverage_doctors) / f.total_doctors * 100) if f.total_doctors else 0.0
        for f in findings
        if f.status != STATUS_NOT_YET_JOINED
    ]
    average_coverage = sum(coverage_values) / len(coverage_values) if coverage_values else 0.0

    return {
        "total_employees": total_employees,
        "total_doctors": doctor_count,
        "flagged_employees": flagged,
        "average_coverage": average_coverage,
    }


def get_employee_doctors(employee_code: str, designation: str) -> list:
    """The doctor list for one employee's own book, for the Employee
    Details page -- BM sees only the B-RGD-eligible doctors whose "BM Code"
    column names them; ABM sees only the A-RGD-eligible doctors whose
    "ABM Code" column names them. Matched by Employee Code (2026-08 fix,
    not name -- two different real employees sharing a name must never
    show the same book). Mirrors process_work_distribution_report's own
    grouping rule exactly (same `_is_b_rgd`/`_is_a_rgd` eligibility checks),
    so this always matches the finding already generated for this employee
    -- its own total_doctors count. visit_count/status use that
    designation's own visit column (bm_visit_count for BM, abm_visit_count
    for ABM)."""
    session = get_config_session()
    try:
        if designation == "ABM":
            rows = [
                r for r in session.query(WorkDistributionDoctor).filter(
                    WorkDistributionDoctor.abm_code == employee_code
                ).all()
                if _is_a_rgd(r.abm_rgd)
            ]
            visit_attr = "abm_visit_count"
        else:
            rows = [
                r for r in session.query(WorkDistributionDoctor).filter(
                    WorkDistributionDoctor.bm_code == employee_code
                ).all()
                if _is_b_rgd(r.category)
            ]
            visit_attr = "bm_visit_count"
    finally:
        session.close()

    doctors = []
    for r in rows:
        visit_count = getattr(r, visit_attr)
        doctors.append({
            "doctor_code": r.doctor_code or "",
            "doctor_name": r.doctor_name,
            "division": r.division or "",
            "city": r.city or "",
            "visit_count": visit_count,
            "status": _visit_status(visit_count),
        })
    doctors.sort(key=lambda d: d["doctor_name"])
    return doctors


def _visit_status(visit_count: int) -> str:
    if visit_count == 0:
        return "Not Visited"
    if visit_count == 1:
        return "Visited Once"
    return "Visited Two or More Times"


def has_data() -> bool:
    """True once at least one Work Distribution report has been processed
    -- used by the Findings/Dashboard pages to distinguish "never
    uploaded" from "uploaded, everyone healthy"."""
    session = get_config_session()
    try:
        return session.query(WorkDistributionFinding).first() is not None
    finally:
        session.close()
