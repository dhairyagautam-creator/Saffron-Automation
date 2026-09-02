"""Work Distribution's automated management notification email system --
Phase 5. Reuses the existing shared infrastructure throughout, per explicit
instruction, rather than building a new email framework:
- app/smtp_service.py for the actual SMTP send (Path Validator's own
  sending module, already module-agnostic -- extended, not duplicated).
- app/email_settings_service.py for the Gmail sender credentials -- the
  same single account used by the rest of the application's email
  functionality, not a separate Work Distribution account (see
  send_notification_batch). app/work_distribution_email_settings_service.py
  still owns only this module's automatic-sending trigger flag.
- app/hierarchy_parser.py / app/hierarchy_service.py for hierarchy lookups
  (find_by_employee_name/find_by_employee_code/find_by_designation,
  is_valid_recipient) -- there is exactly one hierarchy dataset in the
  application.
- app/work_distribution_email_template.py for HTML/text rendering (this
  module's own presentation layer, mirroring app/email_template.py's brand
  colors/skeleton).
- app/table_export_service.py for the actual Excel-writing (Milestone 57,
  2026-08-27): each flagged employee's own Doctor List (RGD Coverage) or
  BM Monthly Trend (Manager Work Allocation) is attached to the recipient's
  email as a real file -- see _doctor_list_attachment/
  _monthly_trend_attachment. One attachment per flagged employee per
  reason, never one combined file per recipient; the HTML/text body itself
  is completely unchanged by this.

Only FLAGGED employees are ever included -- a Healthy/Pass finding never
reaches this module's output. Recipients are resolved automatically from
the uploaded hierarchy; nothing here lets a user manually configure a
recipient (matching the Email Center page's own "no manually managed
Recipient Table" design).

ROUTING RULES (explicit, per-module -- the same designation routes
differently depending on which engine flagged it, so these are two
separate tables, not one shared by designation alone):

  RGD_COVERAGE_CHAINS:
    BM  -> notify ABM AND RBM (both, always -- not first-available).
    ABM -> notify RBM AND SM AND AGM AND GM (all four, if each exists).

  MANAGER_WORK_ALLOCATION_CHAINS:
    ABM -> notify RBM ONLY. Never SM/AGM/GM for this module -- this is
           deliberately narrower than RGD Coverage's own ABM routing.
    RBM -> notify SM AND AGM AND GM (all three, if each exists).

Every rung in a chain that resolves to a valid (named + emailed) recipient
gets notified -- this is a "notify everyone in the list" model, not Path
Validator's own "walk until the first available rung, then stop" fallback
(app.hierarchy_service.FALLBACK_CHAINS) -- the two designs are unrelated by
design, per this module's own business rule (plural "notify their ABM and
RBM", not "escalate to whichever is reachable").

GROUPING: one consolidated email per RECIPIENT, never one per flagged
employee -- and, as of the employee-first redesign, never one CARD per
flagged-employee-per-module either. A recipient who is due more than one
flagged employee (e.g. an RBM with four flagged BMs) receives one email
with one card per distinct employee. If that SAME employee is flagged
under more than one module (e.g. an ABM whose book fails both RGD
Coverage and Manager Work Allocation), they still get exactly one card --
each failed module becomes one entry in that card's own `reasons` list,
not a second, duplicate card. Employee identity for this merge is
`employee_code` when the hierarchy resolved one, else the flagged
employee's own name (case/whitespace-insensitive) -- the same person can't
carry two different codes. If `division`/`hq` ever differ between two
modules' builds of the same employee (shouldn't happen in practice), the
first non-empty value wins silently -- never surfaced as a conflict.
This is enforced by grouping every resolved (recipient_email -> flagged
employee) pairing into one bundle per email address, then merging by
employee identity within that bundle, before any rendering happens.

KNOWN DATA GAP: neither WorkDistributionFinding nor employee_hierarchy
stores an HQ for a BM/ABM. This module derives a best-effort HQ for RGD
Coverage findings from the most common "hq" value across that employee's
own uploaded doctor book (see `_rgd_hq_for`) -- a reasonable proxy, not a
fabricated value; Manager Work Allocation findings use the real `rep_hq`
field already captured on ManagerWorkAllocationRecord, which has no such
gap.

Does NOT modify analysis logic, threshold calculations, rolling six-month
calculations, hierarchy parsing, or Employee Details calculations -- every
function here only READS already-computed findings/records and RENDERS
them; no finding's status/reason is ever recomputed here.
"""

import os
import re
import tempfile
from collections import Counter
from datetime import datetime

from loguru import logger

from app.doj_eligibility_service import NOT_YET_JOINED_LABEL
from app.email_settings_service import get_settings
from app.hierarchy_parser import find_by_designation, find_by_employee_code, find_by_employee_name
from app.hierarchy_service import is_valid_recipient
from app.manager_work_allocation_parameters_service import get_all as get_mwa_parameters, get_rbm_flag_tiers
from app.manager_work_allocation_rbm_service import (
    get_all_findings as get_all_rbm_findings,
    get_current_cycle_label as get_rbm_cycle_label,
    get_employee_bm_details as get_rbm_employee_bm_details,
    get_employee_bm_monthly_history as get_rbm_employee_bm_monthly_history,
)
from app.manager_work_allocation_service import (
    get_all_findings as get_all_abm_findings,
    get_current_cycle_label as get_abm_cycle_label,
    get_employee_bm_details as get_abm_employee_bm_details,
    get_employee_bm_monthly_history as get_abm_employee_bm_monthly_history,
)
from app.smtp_service import open_smtp_connection, send_via_connection
from app.table_export_service import write_rows_to_excel
from app.work_distribution_email_template import render_html, render_text
from app.work_distribution_parameters_service import get_all as get_rgd_parameters
from app.work_distribution_service import (
    get_all_findings as get_all_rgd_findings,
    get_current_period_label,
    get_employee_doctors,
)
from database.connection import get_config_session
from database.models import ManagerWorkAllocationRecord, WorkDistributionDoctor, WorkDistributionEmailNotification

STATUS_DRAFT = "Draft"
STATUS_SENT = "Sent"
STATUS_FAILED = "Failed"

# Same rationale as app.inventory_notification_service.COMMIT_EVERY_N_EMAILS
# -- batched commits so a disk fsync isn't paid per email, but the send log
# still fills in during the run rather than only appearing once the whole
# batch is over.
COMMIT_EVERY_N_EMAILS = 3

RGD_COVERAGE_CHAINS = {
    "BM": ["ABM", "RBM"],
    "ABM": ["RBM", "SM", "AGM", "GM"],
}
MANAGER_WORK_ALLOCATION_CHAINS = {
    "ABM": ["RBM"],
    "RBM": ["SM", "AGM", "GM"],
}

# ABM and RBM are the only two rungs carried as a direct field on an
# employee's own hierarchy row (abm_code/abm_name, rbm_code/rbm_name) --
# same convention as app.hierarchy_service.DIRECTLY_ASSIGNED_LEVELS.
_DIRECT_LEVELS = {"ABM", "RBM"}

# Same shape as ui.work_distribution_employee_details_page's own
# DOCTOR_LIST_COLUMNS/HEADINGS -- duplicated (not imported) since that
# module is UI-layer (imports customtkinter) and this one must stay
# importable headlessly; both feed the exact same
# app.work_distribution_service.get_employee_doctors() data into
# app.table_export_service.write_rows_to_excel(), so the attachment
# matches that page's own Doctor List/Export output exactly.
_DOCTOR_LIST_COLUMNS = ("doctor_code", "doctor_name", "division", "city", "visit_count", "status")
_DOCTOR_LIST_HEADINGS = {
    "doctor_code": "Doctor Code",
    "doctor_name": "Doctor Name",
    "division": "Division",
    "city": "City",
    "visit_count": "Visit Count",
    "status": "Status",
}

_FILENAME_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')


def _attachment_filename(employee_name: str, reason_label: str) -> str:
    """"<Employee Name> - <Reason>.xlsx" -- unlike
    app.table_export_service.default_export_filename (which strips every
    non-alphanumeric character for a UI Save-As default), this only
    strips the characters Windows itself forbids in a filename, so the
    name and reason stay readable in an inbox's attachment list, per this
    feature's own explicit naming requirement."""
    raw = f"{employee_name} - {reason_label}"
    safe = _FILENAME_UNSAFE_CHARS.sub("", raw).strip()
    return f"{safe or 'Employee'}.xlsx"


def _rows_to_xlsx_bytes(rows: list[dict], columns: tuple, headings: dict, sheet_title: str) -> bytes:
    """Writes `rows` through the SAME app.table_export_service.
    write_rows_to_excel() every other export in this app uses -- that
    writer is disk-path-based, not bytes-based, so this only adds a
    temp-file round-trip around it (never a second Excel-writing
    routine) to get the bytes an email attachment needs."""
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        write_rows_to_excel(rows, columns, headings, tmp_path, sheet_title=sheet_title)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(tmp_path)


def _doctor_list_attachment(employee_code: str, employee_name: str, designation: str) -> tuple[str, bytes]:
    """(filename, bytes) for one flagged BM's/ABM's own Doctor List --
    reuses app.work_distribution_service.get_employee_doctors() (the
    exact data source the Employee Details page's own Doctor List table
    uses; no second calculation pipeline)."""
    doctors = get_employee_doctors(employee_code, designation)
    file_bytes = _rows_to_xlsx_bytes(doctors, _DOCTOR_LIST_COLUMNS, _DOCTOR_LIST_HEADINGS, "Doctor List")
    return _attachment_filename(employee_name, "RGD Coverage"), file_bytes


def _monthly_trend_attachment(history: dict, employee_name: str, status_heading: str) -> tuple[str, bytes] | None:
    """(filename, bytes) for one flagged ABM's/RBM's own BM Monthly Trend
    -- reuses the already-computed `history` dict (from
    get_abm_employee_bm_monthly_history()/get_rbm_employee_bm_monthly_history(),
    the same data ui.work_distribution_employee_details_page's own
    _build_monthly_trend_card renders) and mirrors that page's exact row
    shape -- dynamic month columns, NOT_YET_JOINED_LABEL substitution
    included -- so the attachment matches what the manager would see
    on-screen. None if this employee currently has no BMs at all (nothing
    to attach)."""
    months = history.get("months") or []
    bms = history.get("bms") or []
    if not bms:
        return None

    columns = ("subordinate_name", *months, "average", "status")
    headings = {"subordinate_name": "BM Name", "average": "Average", "status": status_heading}
    headings.update({m: m for m in months})

    rows = []
    for bm in bms:
        not_yet_joined_months = bm.get("not_yet_joined_months", ())
        row = {"subordinate_name": bm["subordinate_name"], "average": bm["average"], "status": bm["status"]}
        for m in months:
            row[m] = NOT_YET_JOINED_LABEL if m in not_yet_joined_months else bm["monthly"].get(m, "")
        rows.append(row)

    file_bytes = _rows_to_xlsx_bytes(rows, columns, headings, "BM Monthly Trend")
    return _attachment_filename(employee_name, "Manager Work Allocation"), file_bytes


# --- Hierarchy resolution ------------------------------------------------

def _hierarchy_row_for_employee(employee_name: str) -> dict | None:
    matches = find_by_employee_name(employee_name)
    return matches[0] if matches else None


def _resolve_chain(hierarchy_row: dict, chain: list) -> list:
    """Every VALID recipient across `chain`'s rungs, in order -- (level,
    hierarchy_row) pairs. Plural by design (see module docstring): every
    rung that resolves to a named + emailed recipient is included, not just
    the first one found."""
    division = hierarchy_row.get("division")
    source_sheet = hierarchy_row.get("source_sheet")

    recipients = []
    for level in chain:
        if level in _DIRECT_LEVELS:
            code = hierarchy_row.get(f"{level.lower()}_code")
            name = hierarchy_row.get(f"{level.lower()}_name")
            candidate = find_by_employee_code(code) if code else None
            if candidate is None and name:
                by_name = find_by_employee_name(name)
                candidate = by_name[0] if by_name else None
        else:
            candidate = find_by_designation(division, source_sheet, level)
        if is_valid_recipient(candidate):
            recipients.append((level, candidate))
    return recipients


# --- HQ derivation (see module docstring's "Known data gap") -------------

def _rgd_hq_for(designation: str, employee_code: str) -> str:
    """Pre-existing bug fix (found while implementing Milestone 57, not
    caused by it): this filtered by `bm`/`abm` (name columns dropped by
    the 2026-08 BM/ABM Code migration -- see WorkDistributionDoctor's own
    docstring) and by NAME, when the doctor rows have been keyed by CODE
    ever since. Every real RGD Coverage notification has been raising
    AttributeError here since that migration landed -- restoring the
    already-migrated bm_code/abm_code columns and the code-based lookup
    is what makes RGD notifications (attachments or not) work at all
    again; no identity/aggregation logic changed."""
    column = WorkDistributionDoctor.bm_code if designation == "BM" else WorkDistributionDoctor.abm_code
    session = get_config_session()
    try:
        rows = session.query(WorkDistributionDoctor.hq).filter(
            column == employee_code, WorkDistributionDoctor.hq.isnot(None)
        ).all()
    finally:
        session.close()
    values = [r[0] for r in rows if r[0]]
    return Counter(values).most_common(1)[0][0] if values else ""


def _mwa_hq_for(source_engine: str, employee_name: str) -> str:
    session = get_config_session()
    try:
        rows = session.query(ManagerWorkAllocationRecord.rep_hq).filter(
            ManagerWorkAllocationRecord.source_engine == source_engine,
            ManagerWorkAllocationRecord.emp_name == employee_name,
            ManagerWorkAllocationRecord.rep_hq.isnot(None),
        ).all()
    finally:
        session.close()
    values = [r[0] for r in rows if r[0]]
    return Counter(values).most_common(1)[0][0] if values else ""


# --- Monthly trend summarization (Manager Work Allocation only) ---------

def _average_monthly_trend(history: dict) -> dict | None:
    """A single representative month-by-month line for the flagged
    MANAGER themselves -- the average across all of their own BMs' monthly
    figures, for the email's compact "Monthly Trend" row. Purely a display
    summarization of already-computed per-BM data (see
    app.manager_work_allocation_service/_rbm_service's own
    get_employee_bm_monthly_history) -- never a recalculation of any
    threshold or status. None if there's no monthly history at all."""
    months = history.get("months") or []
    bms = history.get("bms") or []
    if not months or not bms:
        return None
    values = {}
    for month in months:
        month_values = [bm["monthly"][month] for bm in bms if month in bm["monthly"]]
        if month_values:
            avg = sum(month_values) / len(month_values)
            values[month] = str(int(avg)) if avg == int(avg) else f"{avg:.1f}"
    return {"months": months, "values": values}


def _rbm_tier_threshold_text(total_bms: int) -> str:
    """Human-readable threshold text for an RBM's own tiered missed-BM
    rule, read fresh from Settings -- mirrors
    app.manager_work_allocation_rbm_service's own private `_tier_for`
    lookup (duplicated rather than imported, since that's a private helper
    of that module -- see app/manager_work_allocation_parser.py's own
    docstring for why each engine/consumer stays independently readable)."""
    for tier in get_rbm_flag_tiers():
        low, high = tier["min"], tier["max"]
        if high is None:
            if total_bms >= low:
                return f"For {low}+ BMs: flagged if {tier['missed']}+ BM(s) not worked with during the rolling window."
        elif low <= total_bms <= high:
            return f"For {low}-{high} BMs: flagged if {tier['missed']}+ BM(s) not worked with during the rolling window."
    return "Below the configured auto-flag floor for this BM count."


# --- Per-finding-type employee content builders --------------------------

def _build_rgd_bm_employee(finding: dict, hierarchy_row: dict | None, params: dict, period_label: str) -> dict:
    return {
        "employee_name": finding["employee_name"],
        "employee_code": hierarchy_row.get("employee_code", "") if hierarchy_row else "",
        "designation": "BM",
        "division": finding["division"] or "",
        "hq": _rgd_hq_for("BM", finding["employee_code"]),
        "analysis_period": period_label,
        "module": "RGD Coverage",
        "reason": finding["reason"],
        "actual_values": [
            ("Total Calls", str(finding["total_calls"])),
            ("Missed Doctors", str(finding["missed_doctors"])),
            ("Doctors with <2 Visits", str(finding["poor_coverage_doctors"])),
        ],
        "required_threshold": (
            f"Minimum {params['bm_minimum_calls']:.0f} calls; "
            f"missed-doctor % below {params['bm_missed_doctor_percent']:.0f}%; "
            f"poor-coverage % below {params['bm_coverage_percent']:.0f}%"
        ),
        "recommended_action": (
            "Review this BM's territory coverage plan and schedule additional field visits "
            "to underperforming doctors."
        ),
        # RGD Coverage's own BM Code (WorkDistributionFinding.employee_code,
        # the same identity get_employee_doctors() groups doctor rows by) --
        # NOT hierarchy_row's own code above, which is a separate,
        # name-matched hierarchy lookup that may not agree. This is what the
        # Doctor List attachment below is actually fetched by.
        "attachment": _doctor_list_attachment(finding["employee_code"], finding["employee_name"], "BM"),
    }


def _build_rgd_abm_employee(finding: dict, hierarchy_row: dict | None, params: dict, period_label: str) -> dict:
    return {
        "employee_name": finding["employee_name"],
        "employee_code": hierarchy_row.get("employee_code", "") if hierarchy_row else "",
        "designation": "ABM",
        "division": finding["division"] or "",
        "hq": _rgd_hq_for("ABM", finding["employee_code"]),
        "analysis_period": period_label,
        "module": "RGD Coverage",
        "reason": finding["reason"],
        "actual_values": [
            ("Missed Doctors", str(finding["missed_doctors"])),
            ("Doctors with <2 Visits", str(finding["poor_coverage_doctors"])),
        ],
        "required_threshold": (
            f"Maximum {params['abm_missed_doctors']:.0f} missed doctors; "
            f"maximum {params['abm_coverage_doctors']:.0f} doctors with <2 visits"
        ),
        "recommended_action": (
            "Review this ABM's territory coverage and doctor engagement plan; escalate persistent gaps."
        ),
        "attachment": _doctor_list_attachment(finding["employee_code"], finding["employee_name"], "ABM"),
    }


def _build_mwa_abm_employee(finding: dict, hierarchy_row: dict | None, params: dict, period_label: str) -> dict:
    bm_details = get_abm_employee_bm_details(finding["employee_name"])
    failed = [d for d in bm_details if d["status"] == "Fail"]
    reason = (
        f"{len(failed)} of {len(bm_details)} BM(s) averaged below the required "
        f"{params['minimum_joint_working_days']:.0f} joint working days per month."
        if bm_details else "One or more BMs did not meet the required joint working days."
    )
    # One shared fetch -- feeds both the email's own compact trend line
    # (_average_monthly_trend) and the full BM Monthly Trend attachment
    # below, rather than calling get_abm_employee_bm_monthly_history()
    # twice for the same employee.
    monthly_history = get_abm_employee_bm_monthly_history(finding["employee_name"])
    return {
        "employee_name": finding["employee_name"],
        "employee_code": hierarchy_row.get("employee_code", "") if hierarchy_row else "",
        "designation": "ABM",
        "division": finding["division"] or "",
        "hq": _mwa_hq_for("ABM", finding["employee_name"]),
        "analysis_period": period_label,
        "module": "Manager Work Allocation",
        "reason": reason,
        "actual_values": [
            ("Total BMs", str(finding["total_bms"])),
            ("Passed BMs", str(finding["passed_bms"])),
            ("Failed BMs", str(finding["failed_bms"])),
        ],
        "required_threshold": f"Minimum {params['minimum_joint_working_days']:.0f} joint working days/month average per BM",
        "monthly_trend": _average_monthly_trend(monthly_history),
        "recommended_action": (
            "Review joint working schedules with underperforming BMs and realign field coverage plans."
        ),
        "attachment": _monthly_trend_attachment(monthly_history, finding["employee_name"], "Status"),
    }


def _build_mwa_rbm_employee(finding: dict, hierarchy_row: dict | None, period_label: str) -> dict:
    # One shared fetch -- see _build_mwa_abm_employee's own comment.
    monthly_history = get_rbm_employee_bm_monthly_history(finding["employee_name"])
    return {
        "employee_name": finding["employee_name"],
        "employee_code": hierarchy_row.get("employee_code", "") if hierarchy_row else "",
        "designation": "RBM",
        "division": finding["division"] or "",
        "hq": _mwa_hq_for("RBM", finding["employee_name"]),
        "analysis_period": period_label,
        "module": "Manager Work Allocation",
        "reason": finding["reason"],
        "actual_values": [
            ("Total Unique BMs", str(finding["total_bms"])),
            ("Covered BMs", str(finding["passed_bms"])),
            ("Missed BMs", str(finding["failed_bms"])),
            ("Coverage %", str(finding["coverage_percent"])),
        ],
        "required_threshold": _rbm_tier_threshold_text(finding["total_bms"]),
        "monthly_trend": _average_monthly_trend(monthly_history),
        "attachment": _monthly_trend_attachment(monthly_history, finding["employee_name"], "Covered"),
        "recommended_action": (
            "Review missed BM coverage with this RBM and confirm a joint-working plan for the next cycle."
        ),
    }


# --- Batch build (routing + grouping) ------------------------------------

def build_notification_batch() -> list:
    """Builds (but does not send) one consolidated draft per recipient,
    covering every currently FLAGGED employee across RGD Coverage and
    Manager Work Allocation who routes to them, per this module's own fixed
    routing rules. Healthy/Pass findings never reach this function's
    output. One card per distinct employee -- an employee flagged under
    more than one module gets one card with multiple `reasons` entries, not
    one card per module (see module docstring's GROUPING section). Returns
    a list of drafts: {recipient_name, recipient_email, employees,
    employee_count, subject, body, text_body, attachments, status}.
    `attachments` is a flat list of (filename, bytes) pairs -- one per
    flagged employee per reason (see _doctor_list_attachment/
    _monthly_trend_attachment), never merged into a single combined file."""
    rgd_params = get_rgd_parameters()
    mwa_params = get_mwa_parameters()
    rgd_period = get_current_period_label()
    abm_cycle = get_abm_cycle_label() or ""
    rbm_cycle = get_rbm_cycle_label() or ""

    groups: dict = {}
    unresolved_count = 0

    def _add_to_group(recipient_row: dict, employee: dict) -> None:
        """Merges `employee` into this recipient's bundle, keyed by employee
        identity (employee_code, else name) -- so the same real person
        flagged under two modules collapses into one card with two
        `reasons` entries instead of two separate cards. See module
        docstring's GROUPING section for the identity-key and
        first-non-empty-wins division/hq rules."""
        email = recipient_row["email"]
        group = groups.setdefault(email, {"name": recipient_row["employee_name"], "employees": {}})

        reason_entry = {
            "module": employee["module"],
            "analysis_period": employee.get("analysis_period", ""),
            "reason": employee["reason"],
            "actual_values": employee.get("actual_values") or [],
            "required_threshold": employee.get("required_threshold", ""),
            "monthly_trend": employee.get("monthly_trend"),
            "recommended_action": employee.get("recommended_action", ""),
            # (filename, bytes) for this employee's own Doctor List (RGD
            # Coverage) or BM Monthly Trend (Manager Work Allocation) -- see
            # _doctor_list_attachment/_monthly_trend_attachment. Computed
            # once per employee, reused as-is for every recipient this same
            # employee routes to (e.g. an RGD BM's finding is attached
            # identically to both their ABM's and RBM's email).
            "attachment": employee.get("attachment"),
        }

        key = employee.get("employee_code") or employee["employee_name"].strip().lower()
        existing = group["employees"].get(key)
        if existing is None:
            group["employees"][key] = {
                "employee_name": employee["employee_name"],
                "employee_code": employee.get("employee_code", ""),
                "designation": employee.get("designation", ""),
                "division": employee.get("division", ""),
                "hq": employee.get("hq", ""),
                "reasons": [reason_entry],
            }
        else:
            if not existing["division"]:
                existing["division"] = employee.get("division", "")
            if not existing["hq"]:
                existing["hq"] = employee.get("hq", "")
            existing["reasons"].append(reason_entry)

    def _log_unresolved(employee_name: str, module: str, designation: str, reason: str) -> None:
        nonlocal unresolved_count
        unresolved_count += 1
        logger.warning(
            f"Work Distribution notification: {employee_name!r} ({module} {designation}) -- {reason} "
            "No email sent for this employee."
        )

    # --- RGD Coverage: BM + ABM ---
    for finding in get_all_rgd_findings():
        if finding["status"] != "Flagged":
            continue
        hierarchy_row = _hierarchy_row_for_employee(finding["employee_name"])
        if hierarchy_row is None:
            _log_unresolved(finding["employee_name"], "RGD Coverage", finding["designation"], "not found in hierarchy.")
            continue
        chain = RGD_COVERAGE_CHAINS.get(finding["designation"], [])
        recipients = _resolve_chain(hierarchy_row, chain)
        if not recipients:
            _log_unresolved(
                finding["employee_name"], "RGD Coverage", finding["designation"],
                "every rung in the routing chain is vacant, missing, or has no email on file.",
            )
            continue
        if finding["designation"] == "BM":
            employee = _build_rgd_bm_employee(finding, hierarchy_row, rgd_params, rgd_period)
        else:
            employee = _build_rgd_abm_employee(finding, hierarchy_row, rgd_params, rgd_period)
        for _level, recipient_row in recipients:
            _add_to_group(recipient_row, employee)

    # --- Manager Work Allocation: ABM ---
    for finding in get_all_abm_findings():
        if finding["status"] != "Flagged":
            continue
        hierarchy_row = _hierarchy_row_for_employee(finding["employee_name"])
        if hierarchy_row is None:
            _log_unresolved(finding["employee_name"], "Manager Work Allocation", "ABM", "not found in hierarchy.")
            continue
        recipients = _resolve_chain(hierarchy_row, MANAGER_WORK_ALLOCATION_CHAINS["ABM"])
        if not recipients:
            _log_unresolved(
                finding["employee_name"], "Manager Work Allocation", "ABM",
                "their RBM is vacant, missing, or has no email on file.",
            )
            continue
        employee = _build_mwa_abm_employee(finding, hierarchy_row, mwa_params, abm_cycle)
        for _level, recipient_row in recipients:
            _add_to_group(recipient_row, employee)

    # --- Manager Work Allocation: RBM ---
    for finding in get_all_rbm_findings():
        if finding["status"] != "Flagged":
            continue
        hierarchy_row = _hierarchy_row_for_employee(finding["employee_name"])
        if hierarchy_row is None:
            _log_unresolved(finding["employee_name"], "Manager Work Allocation", "RBM", "not found in hierarchy.")
            continue
        recipients = _resolve_chain(hierarchy_row, MANAGER_WORK_ALLOCATION_CHAINS["RBM"])
        if not recipients:
            _log_unresolved(
                finding["employee_name"], "Manager Work Allocation", "RBM",
                "every rung (SM/AGM/GM) is vacant, missing, or has no email on file.",
            )
            continue
        employee = _build_mwa_rbm_employee(finding, hierarchy_row, rbm_cycle)
        for _level, recipient_row in recipients:
            _add_to_group(recipient_row, employee)

    if unresolved_count:
        logger.warning(
            f"Work Distribution notification batch: {unresolved_count} flagged employee routing(s) "
            "could not be resolved -- see individual warnings above."
        )

    now_text = datetime.now().strftime("%d %b %Y, %I:%M %p")
    drafts = []
    for email, group in groups.items():
        employees = sorted(group["employees"].values(), key=lambda e: e["employee_name"])
        employee_count = len(employees)
        # Distinct modules represented in this email, for the Send Log's own
        # "Sections" column -- purely descriptive text, not a grouping key
        # anymore (see WorkDistributionEmailNotification's own docstring).
        modules_summary = ", ".join(sorted({r["module"] for e in employees for r in e["reasons"]}))
        # One attachment per flagged employee per reason -- never merged
        # into a single combined file, and never deduplicated across
        # employees (each is that specific person's own Doctor List/BM
        # Monthly Trend). A reason with no attachment (e.g. a monthly-trend
        # fetch that came back empty) is simply skipped, not a blank entry.
        attachments = [
            r["attachment"] for e in employees for r in e["reasons"] if r.get("attachment")
        ]
        drafts.append({
            "recipient_name": group["name"],
            "recipient_email": email,
            "employees": employees,
            "employee_count": employee_count,
            "modules_summary": modules_summary,
            "subject": (
                f"Saffron Automation - Work Distribution Review Required "
                f"({employee_count} Employee{'s' if employee_count != 1 else ''})"
            ),
            "body": render_html(group["name"], now_text, employees),
            "text_body": render_text(group["name"], now_text, employees),
            "attachments": attachments,
            "status": STATUS_DRAFT,
        })
    return drafts


# --- Send ------------------------------------------------------------

def send_notification_batch(drafts: list, progress_callback=None) -> dict:
    """Sends every draft from build_notification_batch() over ONE shared,
    reused SMTP connection (app.smtp_service.open_smtp_connection/
    send_via_connection), exactly the same skeleton as
    app.inventory_notification_service.send_report_batch: each draft sent
    inside its own try/except so one recipient's failure never stops the
    rest of the batch, one send-log row per attempt.

    Returns {'sent_count', 'failed_count', 'drafts'}."""
    progress_callback = progress_callback or (lambda stage, **kwargs: None)
    total = len(drafts)
    sent_count = 0
    failed_count = 0

    progress_callback("sending", label="Sending Work Distribution notifications...", completed=0, total=total)

    connection = None
    sender_email = None
    connection_error = None
    if drafts:
        settings = get_settings()
        try:
            connection, sender_email = open_smtp_connection(settings["sender_email"], settings["app_password"])
        except Exception as exc:
            connection_error = exc

    session = get_config_session()
    try:
        for index, draft in enumerate(drafts, start=1):
            try:
                if connection_error is not None:
                    raise connection_error
                send_via_connection(
                    connection, sender_email, draft["recipient_email"],
                    draft["subject"], draft["body"], draft.get("text_body"),
                    draft.get("attachments"),
                )
            except Exception as exc:
                logger.error(f"Failed to send Work Distribution notification to {draft['recipient_email']}: {exc}")
                draft["status"] = STATUS_FAILED
                session.add(WorkDistributionEmailNotification(
                    recipient_name=draft["recipient_name"],
                    recipient_email=draft["recipient_email"],
                    sections=draft["modules_summary"],
                    employee_count=draft["employee_count"],
                    subject=draft["subject"],
                    body=draft["body"],
                    status=STATUS_FAILED,
                    error_message=str(exc),
                    created_at=datetime.now(),
                ))
                failed_count += 1
            else:
                draft["status"] = STATUS_SENT
                session.add(WorkDistributionEmailNotification(
                    recipient_name=draft["recipient_name"],
                    recipient_email=draft["recipient_email"],
                    sections=draft["modules_summary"],
                    employee_count=draft["employee_count"],
                    subject=draft["subject"],
                    body=draft["body"],
                    status=STATUS_SENT,
                    created_at=datetime.now(),
                    sent_at=datetime.now(),
                ))
                sent_count += 1

            progress_callback("sending", label=draft["recipient_name"], completed=index, total=total)
            if index % COMMIT_EVERY_N_EMAILS == 0:
                session.commit()

        session.commit()
    finally:
        session.close()
        if connection is not None:
            try:
                connection.quit()
            except Exception:
                pass

    logger.info(f"Work Distribution notification send complete: {sent_count} sent, {failed_count} failed")
    return {"sent_count": sent_count, "failed_count": failed_count, "drafts": drafts}


def get_recent_notifications(limit: int = 50) -> list:
    """Recent send-log rows for the Email Center page's own log view, most
    recent first."""
    session = get_config_session()
    try:
        rows = session.query(WorkDistributionEmailNotification).order_by(
            WorkDistributionEmailNotification.created_at.desc()
        ).limit(limit).all()
        return [
            {
                "recipient_name": r.recipient_name or "",
                "recipient_email": r.recipient_email or "",
                "sections": r.sections or "",
                "employee_count": r.employee_count,
                "subject": r.subject,
                "status": r.status,
                "created_at": r.created_at.strftime("%d %b %Y, %I:%M %p") if r.created_at else "",
            }
            for r in rows
        ]
    finally:
        session.close()
