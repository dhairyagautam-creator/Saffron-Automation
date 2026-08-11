"""Resolves flagged findings to their reporting Senior, extracts ONLY the
coordinates for those flagged employees' relevant visits, reverse-geocodes
just those (cache-first and concurrent for genuine misses, via
app.geocoding_service), and sends:

- One consolidated notification per Senior, covering every employee flagged
  under them.
- One additional master consolidated email PER CONFIGURED MASTER EMAIL
  RECIPIENT (see app/master_email_recipients_service.py and the Master
  Email Recipients section at the bottom of Email Center,
  ui/master_email_recipients_section.py) — replaces the old single
  "Master Report Email" Settings-page field. Each
  recipient has its own Division filter: "All Divisions" gets every
  flagged employee from the session (regardless of whether their Senior
  could be resolved), same as the old single-recipient behavior; a
  specific division (Onyx/Guardians/Xandra) gets only that division's own
  flagged employees. A recipient with zero matching findings this run is
  logged (status "Skipped - No Data") but not sent an email. Multiple
  recipients are fully independent — the same finding can appear in more
  than one recipient's report (e.g. an "All Divisions" recipient and an
  "Onyx" recipient both receive an Onyx finding).

Routing rule: every flagged employee notifies their "Senior" -- the same
person, resolved the same way, shown in the Organization Data table's
Senior column. This is a straight read of the employee's own
`senior_name`/`senior_email` (see app.hierarchy_service.compute_seniors(),
which populates them once per Organization Data refresh) -- this module does
NOT re-walk the reporting chain itself, so the table and the email can never
disagree. As of 2026-07-28, per explicit instruction, this is simplified to
exactly one rung: every flagged employee is BM or ABM (rules/same_location.py's
ANALYZABLE_DESIGNATIONS), and both route ONLY to their own directly-assigned
RBM -- no Senior RBM/SM/AGM/GM fallback. If that RBM is vacant, missing,
null, or has no email on file, the finding is Unresolved -- logged and
reported as "RBM is Vacant. Email not sent." -- never escalated further.

This is the only place reverse geocoding happens anywhere in the
application — never during import, never during rule evaluation, and never
for visits belonging to employees who weren't flagged. Every flagged
coordinate across the whole session is geocoded exactly once, up front, and
the resolved addresses are reused by both the per-RBM emails and the master
email — never fetched twice for the same spot.

Notification pipeline order (v1.3): Region Suppression -> Hospital
Suppression -> Reverse Geocoding -> email fallback routing -> send. The two
suppression stages run first and in that order, so a finding suppressed by
region never incurs a hospital lookup, and only findings that survive BOTH
are ever reverse-geocoded.

Region Suppression (see app/region_suppression.py) withholds the email for
a flagged employee whose Region is on the suppressed list. Hospital
Suppression (see app/hospital_service.py) then checks, for each surviving
finding's cluster location (rules/same_location.py's cluster_lat/
cluster_lon), whether a real hospital/medical facility sits within ~100m (a
large hospital campus can legitimately put several doctors inside the same
cluster); if so the finding is excluded from every email (RBM and master
alike) and recorded as "Hospital Suppressed". Both are notification-only
filters: a suppressed finding still stays in the Findings table and in
Dashboard statistics exactly as flagged — only its automatic email is
withheld. Neither changes what gets flagged. Hospital Suppression is a
standard, always-on production stage as of v1.3 (its feature flag is forced
on in User Mode — see app/feature_flags_service.py — and retained only so
Developer Mode can toggle it off for A/B testing).

All email content (HTML + plain text) is fully generated in memory before
anything is sent, and sending reuses a single SMTP connection for the whole
batch instead of reconnecting per message. Each phase's measured duration
feeds the shared app.timing.PipelineTimingReport for that run.
`progress_callback(stage, label=None, completed=None, total=None)` fires at
each stage transition and, for item-based stages, once per item as it
completes, so the UI can show a live "N / M" count instead of a single
before/after status line.
"""

import re
from datetime import datetime

from loguru import logger
from sqlalchemy import inspect, text

from app.email_template import concentration_sentence, occurrence_bullets, render_manager_email_html, render_manager_email_text
from app.feature_flags_service import is_feature_enabled
from app.findings_service import (
    get_all_findings,
    note_suppression_check_issue,
    parse_hours_worked_message,
    set_hospital_suppression,
    set_notification_status,
    set_status,
)
from app.geocoding_service import geocode_many, round_coordinate
from app.hierarchy_parser import find_by_employee_code, find_by_employee_name
from app.hierarchy_service import is_valid_recipient
from app.hospital_service import DEFAULT_RADIUS_METERS, find_hospitals_many, format_suppression_reason
from app.hospital_service import round_coordinate as round_hospital_coordinate
from app.master_email_recipients_service import division_matches, get_all_recipients as get_all_master_recipients
from app.region_suppression import format_suppression_reason as format_region_suppression_reason
from app.region_suppression import is_region_suppressed
from app.rule_parameters import get_parameters
from app.send_state import update_progress
from app.smtp_service import open_smtp_connection, send_via_connection
from app.timing import PhaseTimer, get_current_report
from database.connection import get_data_engine, get_session
from database.import_service import RAW_VISITS_TABLE
from database.models import EmailNotification, ImportHistory, InvestigationFinding

# Emails are committed to the DB in small batches rather than one at a time
# (fsync cost per commit isn't free) or all at the end (the Email Center's
# 1s poll would see nothing until the whole batch finished) -- the in-memory
# send_state progress update still happens immediately per email regardless
# of this batching, so the UI is never behind by more than one email.
COMMIT_EVERY_N_EMAILS = 3

DATE_COLUMN = "Date"
EMPLOYEE_CODE_COLUMN = "Employee Code"
REPORTING_HQ_COLUMN = "Reporting HQ"
REGION_COLUMN = "Region"

# A configured Master Email recipient whose Division filter matches zero
# findings this run -- logged so the attempt is auditable (matches
# app.inventory_notification_service's identical convention) but never
# actually sent, since an empty consolidated report helps no one.
STATUS_SKIPPED_NO_DATA = "Skipped - No Data"

# The only text a manager should ever see in place of a resolved address —
# never a raw "lat, lon" string. See _geocode_stats_by_employee logging and
# _validate_no_raw_coordinates below for how this is enforced.
ADDRESS_UNAVAILABLE_TEXT = "Address unavailable"

# Safety net, not the primary fix: matches a bare "lat, lon" pair like
# "23.0187084, 72.5067161" so a raw coordinate slipping into an address list
# from anywhere (a future regression, a different code path) is caught and
# logged loudly instead of silently reaching a manager's inbox.
_RAW_COORDINATE_PATTERN = re.compile(r"^-?\d{1,3}(\.\d+)?,\s*-?\d{1,3}(\.\d+)?$")


def _validate_no_raw_coordinates(employee_name: str, addresses: list[str]) -> None:
    for address in addresses:
        if _RAW_COORDINATE_PATTERN.match(address.strip()):
            logger.error(
                f"[Reverse Geocoding] Employee: {employee_name} | a raw coordinate-shaped value "
                f"reached the final address list: {address!r}. This should never happen post-fix -- "
                "treat as a bug, not expected behavior."
            )

# Friendly label for each rule_name, used in the HTML/text email instead of
# the raw internal rule name. Any rule not listed here just falls back to
# showing its raw name, so adding a new rule never breaks email rendering.
RULE_LABELS = {
    "SAME_LOCATION": "Same Location Rule",
    "HOURS_WORKED": "Low Working Hours",
}

# HR-based rules (Low Working Hours today; a future Low Call Count later).
# They feed the SAME employee-level email as the location rules, but they are
# never region/hospital suppressed -- those are location-only notification
# filters, and an HR finding has no cluster location to check. Kept as a set
# so the whole pipeline treats "is this an HR finding?" one way in one place.
HR_RULE_NAMES = {"HOURS_WORKED"}


def _fmt_hm_from_minutes(total_minutes: float) -> str:
    """'6h 10m' from a minute count (clamped at zero)."""
    total = max(0, round(total_minutes))
    return f"{total // 60}h {total % 60:02d}m"


def _working_hours_view(finding) -> dict | None:
    """Presentation-ready Working Hours block for one HOURS_WORKED finding,
    built from the finding's own recorded times (parse_hours_worked_message)
    -- no recomputation of the detector's result, just formatting the values
    it already produced. None if the message can't be parsed."""
    parsed = parse_hours_worked_message(finding.message)
    if parsed is None:
        return None
    fh, fm = (int(x) for x in parsed["first_call"].split(":"))
    lh, lm = (int(x) for x in parsed["last_call"].split(":"))
    worked_minutes = (lh * 60 + lm) - (fh * 60 + fm)
    required_minutes = parsed["minimum"] * 60
    return {
        "first_call": parsed["first_call"],
        "last_call": parsed["last_call"],
        "hours_worked": _fmt_hm_from_minutes(worked_minutes),
        "required": _fmt_hm_from_minutes(required_minutes),
        "short_by": _fmt_hm_from_minutes(required_minutes - worked_minutes),
    }


def _hours_summary_text(finding) -> str:
    """One-line summary for the findings-table row of an HR finding, e.g.
    'Worked 6h 10m; required 7h 30m; short 1h 20m.'"""
    view = _working_hours_view(finding)
    if view is None:
        return "Low working hours."
    return f"Worked {view['hours_worked']}; required {view['required']}; short {view['short_by']}."


def _combined_interpretation(location_findings: list, working_hours: list) -> list[str]:
    """Factual combined explanation shown ONLY when an employee has both a
    location finding and a Low Working Hours finding. Uses the values the two
    detectors already produced (concentration % / valid calls / radius from
    the location finding; span / shortfall from the hours finding) -- no new
    metric is computed or invented."""
    view = working_hours[0]
    loc = next(
        (
            f for f, _ in location_findings
            if f.concentration_percent is not None
            and f.valid_visit_count is not None
            and f.radius_meters is not None
        ),
        None,
    )
    if loc is not None:
        return [
            "Both conditions were detected for this employee.",
            (
                f"{loc.concentration_percent:g}% of {loc.valid_visit_count} valid GPS-tagged calls "
                f"clustered within approximately {loc.radius_meters} meters of one location, while the "
                f"working day spanned only {view['hours_worked']} (first call {view['first_call']}, last "
                f"call {view['last_call']}) — {view['short_by']} short of the {view['required']} minimum. "
                "A concentrated cluster of activity within an already-short working day warrants review."
            ),
        ]
    return [
        "Both a location concentration flag and a low-working-hours flag were detected for this employee.",
        (
            f"The working day spanned only {view['hours_worked']} — {view['short_by']} short of the "
            f"{view['required']} minimum."
        ),
    ]


def _resolve_employee_hierarchy_row(employee_code: str, employee_name: str) -> dict | None:
    row = find_by_employee_code(employee_code)
    if row:
        return row
    matches = find_by_employee_name(employee_name)
    return matches[0] if matches else None


# --- Company-specific routing override (Xandra HQ override) ------------
#
# ONE-OFF BUSINESS RULE, DELIBERATELY ISOLATED HERE so it can be removed
# in one place without touching the general fallback chain above: delete
# this block (constants + _xandra_override_recipient) and the single call
# site in build_email_batch below.
#
# For Division = Xandra and BM HQ in {Muzaffarnagar, Saharanpur,
# Dehradun}, bypass the normal routing (as of 2026-07-28: RBM only, no
# further fallback -- see app.hierarchy_service.FALLBACK_CHAINS) ENTIRELY
# and route straight to Abhishek Sharma (Senior RBM), using
# his email as currently configured in the Xandra Organization Data
# workbook (looked up fresh each time, never hardcoded, so a changed email
# there is picked up automatically). If his email can't be found, the
# finding becomes Unresolved -- per spec, this override does NOT continue
# through the fallback chain for these three HQs even if it can't resolve.

_XANDRA_OVERRIDE_DIVISION = "XANDRA"
_XANDRA_OVERRIDE_HQS = {"MUZAFFARNAGAR", "SAHARANPUR", "DEHRADUN"}
_XANDRA_OVERRIDE_RECIPIENT_NAME = "Abhishek Sharma"


def _xandra_override_applies(division: str | None, hq: str | None) -> bool:
    if not division or division.strip().upper() != _XANDRA_OVERRIDE_DIVISION:
        return False
    return bool(hq) and hq.strip().upper() in _XANDRA_OVERRIDE_HQS


def _xandra_override_recipient(hq: str | None) -> tuple[str | None, str | None]:
    """Only ever called after _xandra_override_applies() has already
    confirmed the override applies -- returns (name, email) for Abhishek
    Sharma if his email is configured, or (None, None) if not (which
    means Unresolved, deliberately NOT a fall-through to the general
    chain -- see block comment above)."""
    matches = find_by_employee_name(_XANDRA_OVERRIDE_RECIPIENT_NAME)
    for row in matches:
        if is_valid_recipient(row):
            return row["employee_name"], row["email"]
    logger.warning(
        f"Xandra HQ override matched (HQ={hq!r}) but '{_XANDRA_OVERRIDE_RECIPIENT_NAME}' has no "
        "configured email in the hierarchy -- finding will be Unresolved, not falling back further."
    )
    return None, None


def _get_finding_context(import_id: int, employee_code: str, visit_date) -> dict:
    """Return {'hq': str|None, 'region': str|None, 'coordinates': [(lat, lon), ...]}
    for the exact visits this finding covers — the same valid-coordinate
    rows the rule engine evaluated. This is the scope-narrowing step: only
    these coordinates are ever geocoded, and HQ/Region come straight from
    the imported workbook rather than depending on a hierarchy match.
    `region` feeds the Region Suppression Rule (see
    app/region_suppression.py) — a separate, later filter from Hospital
    Suppression."""
    if not inspect(get_data_engine()).has_table(RAW_VISITS_TABLE):
        return {"hq": None, "region": None, "coordinates": []}

    date_str = visit_date.strftime("%d-%m-%Y")
    query = text(
        f'SELECT "{REPORTING_HQ_COLUMN}", "{REGION_COLUMN}", latitude, longitude FROM {RAW_VISITS_TABLE} '
        f'WHERE import_id = :iid AND "{EMPLOYEE_CODE_COLUMN}" = :code AND "{DATE_COLUMN}" = :date_str'
    )
    with get_data_engine().connect() as conn:
        rows = conn.execute(
            query, {"iid": import_id, "code": employee_code, "date_str": date_str}
        ).fetchall()

    hq = None
    region = None
    coordinates = []
    for reporting_hq, row_region, lat, lon in rows:
        if hq is None and reporting_hq:
            hq = reporting_hq
        if region is None and row_region:
            region = row_region
        if lat is not None and lon is not None:
            coordinates.append((lat, lon))

    return {"hq": hq, "region": region, "coordinates": coordinates}


def _get_import_file_name(import_id: int) -> str:
    session = get_session()
    try:
        row = session.query(ImportHistory).filter_by(id=import_id).first()
        return row.file_name if row else "-"
    finally:
        session.close()


def _concentration_text(finding) -> str:
    """'90% of calls were made within a 200 meter radius.' — falls back to a
    plain label for any pre-existing finding created before these columns
    existed (concentration_percent/radius_meters are nullable)."""
    if finding.concentration_percent is None or finding.radius_meters is None:
        return "Concentration data not available."
    return concentration_sentence(finding.concentration_percent, finding.radius_meters)


def _build_consolidated_email(
    recipient_label: str,
    group_findings: list,
    contexts: dict,
    designations: dict,
    addresses_by_employee: dict,
    summary_label: str = "Manager",
    extra_summary_pairs: list[tuple[str, str]] | None = None,
    show_division: bool = False,
) -> tuple[str, str, list, int]:
    """Return (html_body, text_body, table_rows, distinct_employee_count)
    for one recipient's consolidated email (an RBM, or the gddesk@ master
    rollup), aggregating multiple findings for the same employee (e.g.
    flagged on more than one date) into a single section with one
    explanation block per occurrence.

    `show_division=True` (master email only, see build_email_batch below)
    adds a Division column to the findings table, sourced from each
    finding's own `division` field (see rules/same_location.py) -- display
    only, never affects grouping or any of the aggregation above."""
    table_rows = []
    employees: dict = {}  # employee_code -> aggregated section data, in first-seen order

    for finding in group_findings:
        context = contexts[finding.finding_id]
        rule_label = RULE_LABELS.get(finding.rule_name, finding.rule_name)
        hq = context["hq"] or "-"
        designation = designations.get(finding.finding_id, "-")
        date_text = finding.visit_date.strftime("%d %b %Y")

        # The summary column shows the concentration sentence for a location
        # finding, or the worked/required/short line for an HR finding.
        if finding.rule_name in HR_RULE_NAMES:
            summary_text = _hours_summary_text(finding)
        else:
            summary_text = _concentration_text(finding)

        table_rows.append(
            {
                "employee_name": finding.employee_name,
                "employee_code": finding.employee_code,
                "designation": designation,
                "hq": hq,
                "rule_label": rule_label,
                "visit_date_text": date_text,
                "concentration_text": summary_text,
                "division": finding.division or "",
            }
        )

        section = employees.setdefault(
            finding.employee_code,
            {
                "employee_name": finding.employee_name,
                "employee_code": finding.employee_code,
                "hq": hq,
                "findings": [],
                "addresses": addresses_by_employee.get(finding.employee_name, []),
            },
        )
        section["findings"].append((finding, date_text))

    employee_sections = []
    for section in employees.values():
        # One employee, all their findings -- split into the location family
        # (Same Location, fake calls, ...) and the HR family (Low Working
        # Hours) so each renders its own section under the single employee
        # heading, instead of the employee appearing twice.
        location_findings = [(f, d) for f, d in section["findings"] if f.rule_name not in HR_RULE_NAMES]
        hr_findings = [(f, d) for f, d in section["findings"] if f.rule_name in HR_RULE_NAMES]

        multi = len(location_findings) > 1
        occurrences = []
        for finding, date_text in location_findings:
            if finding.matched_visit_count is None or finding.valid_visit_count is None or \
               finding.radius_meters is None or finding.threshold_percent is None:
                occurrences.append([_concentration_text(finding)])
                continue
            occurrences.append(
                occurrence_bullets(
                    finding.matched_visit_count,
                    finding.valid_visit_count,
                    finding.radius_meters,
                    finding.threshold_percent,
                    date_text=date_text if multi else None,
                )
            )

        multi_hr = len(hr_findings) > 1
        working_hours = []
        for finding, date_text in hr_findings:
            view = _working_hours_view(finding)
            if view is None:
                continue
            working_hours.append({**view, "date_text": date_text if multi_hr else None})

        status_parts = []
        if occurrences:
            status_parts.append("Location")
        if working_hours:
            status_parts.append("Low Working Hours")

        combined_lines = (
            _combined_interpretation(location_findings, working_hours)
            if occurrences and working_hours else []
        )

        employee_sections.append(
            {
                "employee_name": section["employee_name"],
                "employee_code": section["employee_code"],
                "hq": section["hq"],
                "occurrences": occurrences,
                "addresses": section["addresses"],
                "working_hours": working_hours,
                "status_label": " + ".join(status_parts),
                "combined_lines": combined_lines,
            }
        )

    distinct_employee_count = len(employees)
    rules_triggered = ", ".join(
        sorted({RULE_LABELS.get(f.rule_name, f.rule_name) for f in group_findings})
    )
    now = datetime.now()
    report_date = now.strftime("%d %b %Y")
    generated_at = now.strftime("%d %b %Y, %I:%M %p")

    summary_pairs = [
        (summary_label, recipient_label),
        ("Date", report_date),
        ("Employees Flagged", str(distinct_employee_count)),
        ("Rules Triggered", rules_triggered),
    ]

    render_kwargs = dict(
        recipient_label=recipient_label,
        summary_pairs=summary_pairs,
        generated_at=generated_at,
        table_rows=table_rows,
        employee_sections=employee_sections,
        extra_summary_pairs=extra_summary_pairs,
        show_division=show_division,
    )
    html_body = render_manager_email_html(**render_kwargs)
    text_body = render_manager_email_text(**render_kwargs)
    return html_body, text_body, table_rows, distinct_employee_count


def build_email_batch(import_id: int, progress_callback=None) -> list:
    """Build (but do not send) one draft per RBM affected this session, plus
    one additional master draft — sent to the configurable Master Report
    Email from the Settings page — covering every flagged employee
    regardless of whether their RBM could be resolved. All email content
    (HTML + plain text) is fully generated here, in memory, before the
    caller sends anything.

    Returns a list of drafts: {manager_name, manager_email, subject, body,
    text_body, finding_ids, status, kind}. `kind` is "rbm", "master", or
    "unresolved" — only "rbm" drafts drive a finding's status to Reviewed on
    send; the master email is an informational copy and never does.
    status is "Draft" for anything routable, or "Unresolved" for findings
    whose RBM/email couldn't be found — those are still returned (as one
    combined draft) so nothing silently vanishes.

    `progress_callback(stage, label=None, completed=None, total=None)`, if
    given, is called at each stage transition and — for item-based stages
    (hospital suppression, reverse geocoding, email generation) — once per
    item as it completes, so the UI can show a live "N / M" count instead
    of a single before/after status line.
    """
    progress_callback = progress_callback or (lambda stage, **kwargs: None)
    report = get_current_report()
    geocode_stats_by_employee: dict = {}

    findings = [f for f in get_all_findings(import_id) if f.status == "Open"]

    progress_callback("hierarchy", label="Resolving hierarchy...")
    hierarchy_timer = PhaseTimer()
    email_lookup_timer = PhaseTimer()

    # Every flagged finding's context (HQ + its own visit coordinates) is
    # resolved up front, before hierarchy/routing -- this is also what lets
    # the hospital-lookup batch and the reverse-geocoding batch further
    # below run concurrently instead of back-to-back (both hit independent
    # Geoapify endpoints with no shared state). BM HQ from here also feeds
    # the Xandra HQ routing override immediately below.
    contexts = {
        finding.finding_id: _get_finding_context(import_id, finding.employee_code, finding.visit_date)
        for finding in findings
    }

    # Resolved for every Open finding up front, regardless of outcome — the
    # master email needs all of them, not just the ones that route cleanly.
    enriched = []
    for finding in findings:
        with hierarchy_timer:
            hierarchy_row = _resolve_employee_hierarchy_row(finding.employee_code, finding.employee_name)
            designation = hierarchy_row.get("designation") if hierarchy_row else None
            # senior_name/senior_email are precomputed once per Organization
            # Data refresh by app.hierarchy_service.compute_seniors() -- the
            # exact same values shown in the Organization Data table's
            # Senior column. Reading them here (instead of re-walking the
            # fallback chain) is what guarantees the table and the email
            # recipient can never disagree. "Top Level" (a GM with nothing
            # above them, by design) is never itself an emailable recipient.
            senior_name = hierarchy_row.get("senior_name") if hierarchy_row else None
            senior_email = hierarchy_row.get("senior_email") if hierarchy_row else None
            if senior_name == "Top Level" or not senior_email:
                senior_name, senior_email = None, None

        with email_lookup_timer:
            hq = contexts[finding.finding_id]["hq"]
            if _xandra_override_applies(finding.division, hq):
                recipient_name, recipient_email = _xandra_override_recipient(hq)
            else:
                recipient_name, recipient_email = senior_name, senior_email
                # 2026-07-28 simplification: BM/ABM route ONLY to their own
                # RBM now (see app.hierarchy_service.FALLBACK_CHAINS) -- no
                # further fallback. If that RBM is vacant, missing, null, or
                # has no email on file, recipient_email is empty here and
                # the finding is deliberately NOT escalated any further; log
                # exactly that, per instruction, rather than silently
                # leaving it to the aggregated Unresolved report below.
                if not recipient_email and hierarchy_row is not None:
                    logger.warning(
                        f"RBM is Vacant. Email not sent. (Employee: {finding.employee_name} "
                        f"[{finding.employee_code}], Designation: {designation})"
                    )

        enriched.append(
            {
                "finding": finding,
                "designation": designation,
                "rbm_name": recipient_name,
                "rbm_email": recipient_email,
                # Whether this employee had an Organization Data entry at
                # all -- used only for the Unresolved-findings diagnostic
                # message below, to distinguish "no hierarchy entry found"
                # from "hierarchy entry found, but every rung above them is
                # vacant/missing/has no email".
                "has_hierarchy_entry": hierarchy_row is not None,
            }
        )

    hierarchy_timer.log("Hierarchy lookup")
    email_lookup_timer.log("Email lookup")
    report.record("Hierarchy lookup", hierarchy_timer.total)
    report.record("Email lookup", email_lookup_timer.total)

    designations = {item["finding"].finding_id: item["designation"] or "-" for item in enriched}

    # ---- Pipeline stage 3: Region Suppression ----
    # Region Suppression runs FIRST -- before Hospital Suppression -- so a
    # region-suppressed finding never incurs a hospital lookup (per the v1.3
    # pipeline order Region -> Hospital -> Reverse Geocoding, and the "only
    # look up findings that survive Region Suppression" performance
    # requirement). Notification-only, always on: the employee was already
    # fully analyzed and their finding stays on the Findings page and in
    # Dashboard stats exactly as flagged -- this only decides whether the
    # automatic RBM/master email goes out. See app/region_suppression.py.
    region_active_items = []
    region_suppressed_count = 0
    for item in enriched:
        finding = item["finding"]
        region = contexts[finding.finding_id].get("region")

        # Region Suppression applies to EVERY finding type, HR included: a
        # Low Working Hours finding for an employee in a suppressed region
        # (Kerala/Punjab, per app/region_suppression.py) is withheld from the
        # email and, via the persisted "Suppressed - Region Rule" status,
        # excluded from the HR-Based table and the Master page too. (Hospital
        # Suppression below still skips HR -- an HR finding has no cluster
        # location to check.)
        if is_region_suppressed(region):
            reason = format_region_suppression_reason(region)
            logger.info(
                f"[Region Suppression] Employee: {finding.employee_name} ({finding.employee_code}) | "
                f"Region: {region!r} | Decision: SUPPRESSED — {reason}"
            )
            set_notification_status(finding.finding_id, "Suppressed - Region Rule", reason)
            region_suppressed_count += 1
            continue  # excluded from every email — RBM and master alike

        region_active_items.append(item)

    if region_suppressed_count:
        logger.info(f"Region Suppression: {region_suppressed_count} finding(s) suppressed this run")

    enriched = region_active_items  # only region-survivors continue to Hospital Suppression

    # ---- Pipeline stage 4: Hospital Suppression ----
    # Runs ONLY on findings that survived Region Suppression above -- never
    # the whole workbook, never region-suppressed findings -- and only those
    # with a recorded cluster location (older findings predating cluster
    # capture don't have one and are treated as not-suppressed rather than
    # blocking anything). Always on in production as of v1.3 (see
    # app/feature_flags_service.py -- the user-mode flag is forced on); the
    # feature flag is retained only so Developer Mode can toggle it off for
    # A/B testing, which never affects production. Notification-only, exactly
    # like Region Suppression: a hospital-suppressed finding stays on the
    # Findings page / Dashboard, recorded as "Hospital Suppressed" -- only
    # its automatic email is withheld.
    hospital_enabled = is_feature_enabled("hospital_suppression")

    cluster_coords = {
        (item["finding"].cluster_lat, item["finding"].cluster_lon)
        for item in enriched
        if item["finding"].cluster_lat is not None and item["finding"].cluster_lon is not None
    } if hospital_enabled else set()

    if hospital_enabled:
        progress_callback(
            "hospital_suppression", label="Checking hospital suppression...", completed=0, total=len(cluster_coords)
        )
        with report.timed("Hospital suppression"):
            if cluster_coords:
                radius_meters = int(
                    get_parameters("HOSPITAL_SUPPRESSION").get("radius_meters", DEFAULT_RADIUS_METERS)
                )
                hospital_by_coord = find_hospitals_many(
                    list(cluster_coords),
                    radius_meters=radius_meters,
                    on_progress=lambda done, total: progress_callback(
                        "hospital_suppression", completed=done, total=total
                    ),
                )
            else:
                hospital_by_coord = {}
    else:
        hospital_by_coord = {}

    hospital_active_items = []
    suppressed_count = 0
    for item in enriched:
        finding = item["finding"]

        if not hospital_enabled or finding.rule_name in HR_RULE_NAMES:
            # Hospital Suppression toggled off (Developer Mode A/B test only;
            # it is forced on in production) — or an HR finding, which has no
            # cluster location to check and is a location-only concept anyway.
            # Either way the finding stays active and routes to email as normal.
            hospital_active_items.append(item)
            continue

        hospital_result = None
        if finding.cluster_lat is not None and finding.cluster_lon is not None:
            hospital_result = hospital_by_coord.get(
                round_hospital_coordinate(finding.cluster_lat, finding.cluster_lon)
            )

        # Per-employee decision trace tying app.hospital_service's
        # coordinate-level candidate logging back to a specific
        # employee/finding. The cluster's own street address is deliberately
        # NOT reverse-geocoded here: Reverse Geocoding is a later pipeline
        # stage (below), and geocoding during the hospital stage would both
        # violate that order and add lookups the spec explicitly wants to
        # avoid. The hospital name/distance below is the decisive detail.
        logger.info(
            f"[Hospital Suppression] Employee: {finding.employee_name} ({finding.employee_code}) | "
            f"Cluster: ({finding.cluster_lat}, {finding.cluster_lon})"
        )

        if hospital_result is None:
            logger.info("[Hospital Suppression] Decision: NOT suppressed — no cluster location to check.")
        elif hospital_result.get("error"):
            logger.warning(
                f"[Hospital Suppression] Decision: NOT suppressed — hospital lookup failed "
                f"({hospital_result['error']}); treating as fail-open, not a confirmed absence of a hospital."
            )
            # A failed/skipped check must never look identical to a
            # genuine "no hospital nearby" clear pass -- this note
            # survives the later Sent/Failed update (see
            # findings_service.set_notification_status) so a reviewer can
            # tell which findings actually need a manual hospital check.
            note_suppression_check_issue(
                finding.finding_id,
                f"Hospital suppression check inconclusive — {hospital_result['error']}. Needs manual review.",
            )
        elif hospital_result["found"]:
            logger.info(
                f"[Hospital Suppression] Decision: SUPPRESSED — nearest facility "
                f"'{hospital_result['name']}' at ({hospital_result['lat']}, {hospital_result['lon']}), "
                f"{hospital_result['distance_meters']}m away."
            )
        else:
            logger.info("[Hospital Suppression] Decision: NOT suppressed — no medical facility found nearby.")

        if hospital_result and hospital_result["found"]:
            reason = format_suppression_reason(hospital_result["name"], hospital_result["distance_meters"])
            set_hospital_suppression(
                finding.finding_id,
                reason,
                hospital_result["name"],
                hospital_result["lat"],
                hospital_result["lon"],
                hospital_result["distance_meters"],
            )
            suppressed_count += 1
            continue  # excluded from every email — RBM and master alike

        hospital_active_items.append(item)

    if suppressed_count:
        logger.info(f"Hospital Suppression: {suppressed_count} finding(s) suppressed this run")

    enriched = hospital_active_items  # only findings surviving BOTH suppressions continue
    findings = [item["finding"] for item in enriched]

    # ---- Pipeline stage 5: Reverse Geocoding ----
    # Only the coordinates of findings that survived BOTH suppression stages
    # are geocoded -- never suppressed findings, never the whole workbook --
    # per the v1.3 pipeline order (Hospital Suppression precedes Reverse
    # Geocoding). A coordinate whose lookup failed maps to None here (see
    # app.geocoding_service.geocode_many) rather than a raw coordinate
    # string, so both "failed" and "genuinely missing" collapse to the same
    # unresolved signal handled below.
    coordinate_set = {
        coord for item in enriched for coord in contexts[item["finding"].finding_id]["coordinates"]
    }
    progress_callback("geocoding", label="Resolving addresses...", completed=0, total=len(coordinate_set))
    with report.timed("Reverse geocoding"):
        address_by_rounded = geocode_many(
            list(coordinate_set),
            on_progress=lambda done, total: progress_callback("geocoding", completed=done, total=total),
        )

    address_by_coord = {coord: address_by_rounded.get(round_coordinate(*coord)) for coord in coordinate_set}

    addresses_by_employee: dict = {}
    for item in enriched:
        finding = item["finding"]
        context = contexts[finding.finding_id]
        addresses = addresses_by_employee.setdefault(finding.employee_name, [])
        stats = geocode_stats_by_employee.setdefault(
            finding.employee_name, {"detected": set(), "resolved": 0, "failed": 0}
        )
        for coord in context["coordinates"]:
            if coord not in stats["detected"]:
                stats["detected"].add(coord)
                address = address_by_coord.get(coord)
                if address:
                    stats["resolved"] += 1
                else:
                    stats["failed"] += 1
            address = address_by_coord.get(coord) or ADDRESS_UNAVAILABLE_TEXT
            if address not in addresses:
                addresses.append(address)

    for employee_name, addresses in addresses_by_employee.items():
        stats = geocode_stats_by_employee.get(employee_name, {"detected": set(), "resolved": 0, "failed": 0})
        detected = len(stats["detected"])
        attempted = detected  # every detected location is resolved cache-first, so all are "attempted"
        logger.info(
            f"[Reverse Geocoding] Employee: {employee_name} | locations detected: {detected}, "
            f"attempted: {attempted}, resolved: {stats['resolved']}, failed: {stats['failed']}, "
            f"final addresses in email: {len(addresses)}"
        )
        if stats["resolved"] + stats["failed"] != detected:
            logger.error(
                f"[Reverse Geocoding] Employee: {employee_name} | count mismatch: "
                f"detected={detected} but resolved({stats['resolved']}) + failed({stats['failed']}) "
                f"= {stats['resolved'] + stats['failed']}"
            )
        _validate_no_raw_coordinates(employee_name, addresses)

    with report.timed("Email grouping"):
        rbm_groups: dict = {}
        unresolved_items = []
        for item in enriched:
            if not item["rbm_email"]:
                unresolved_items.append(item)
                continue
            group = rbm_groups.setdefault(
                item["rbm_email"], {"rbm_name": item["rbm_name"], "rbm_email": item["rbm_email"], "findings": []}
            )
            group["findings"].append(item["finding"])

    email_gen_timer = PhaseTimer()
    drafts = []

    master_recipients = get_all_master_recipients()

    # One master email built per configured recipient (see module
    # docstring), so "N / M" reflects every email this stage will
    # actually build -- not just "+1" for a single master recipient.
    total_to_generate = len(rbm_groups) + len(master_recipients)
    generated_count = 0
    progress_callback("email_generation", label="Generating emails...", completed=0, total=total_to_generate)

    for rbm_email, group in rbm_groups.items():
        with email_gen_timer:
            html_body, text_body, table_rows, distinct_employee_count = _build_consolidated_email(
                recipient_label=group["rbm_name"],
                group_findings=group["findings"],
                contexts=contexts,
                designations=designations,
                addresses_by_employee=addresses_by_employee,
            )
        generated_count += 1
        progress_callback("email_generation", completed=generated_count, total=total_to_generate)

        drafts.append(
            {
                "manager_name": group["rbm_name"],
                "manager_email": rbm_email,
                "subject": f"Saffron Automation Alert - Activity Review Required ({distinct_employee_count} Employees)",
                "body": html_body,
                "text_body": text_body,
                "finding_ids": ",".join(str(f.finding_id) for f in group["findings"]),
                "status": "Draft",
                "kind": "rbm",
            }
        )

    if master_recipients:
        all_findings = [item["finding"] for item in enriched]
        total_rbms_affected = len(rbm_groups)
        now = datetime.now()

        for recipient in master_recipients:
            # Division filter (see app.master_email_recipients_service.
            # division_matches): "All Divisions" gets every flagged
            # employee this session, exactly like the old single-recipient
            # behavior; any other division gets only ITS OWN flagged
            # employees. Recipients are fully independent -- the same
            # finding can land in more than one recipient's report.
            recipient_findings = [f for f in all_findings if division_matches(recipient["division"], f.division)]
            recipient_employees_flagged = len({f.employee_code for f in recipient_findings})

            with email_gen_timer:
                html_body, text_body, table_rows, _ = _build_consolidated_email(
                    recipient_label=recipient["name"],
                    group_findings=recipient_findings,
                    contexts=contexts,
                    designations=designations,
                    addresses_by_employee=addresses_by_employee,
                    summary_label="Recipient",
                    extra_summary_pairs=[
                        ("Division", recipient["division_label"]),
                        ("Total Employees Flagged", str(recipient_employees_flagged)),
                        ("Total RBMs Affected", str(total_rbms_affected)),
                        ("Analysis Timestamp", now.strftime("%d %b %Y, %I:%M %p")),
                        ("Workbook Name", _get_import_file_name(import_id)),
                    ],
                    show_division=True,
                )
            generated_count += 1
            progress_callback("email_generation", completed=generated_count, total=total_to_generate)

            # A recipient whose division filter matches nothing this run
            # still gets a logged attempt (see STATUS_SKIPPED_NO_DATA) --
            # auditable, same as every configured recipient, but never
            # actually sent (an empty consolidated report helps no one).
            status = "Draft" if recipient_findings else STATUS_SKIPPED_NO_DATA

            drafts.append(
                {
                    "manager_name": f"{recipient['name']} (Master Summary)",
                    "manager_email": recipient["email"],
                    "subject": (
                        f"Saffron Automation Master Alert - Activity Review Required "
                        f"({recipient_employees_flagged} Employees, {recipient['division_label']})"
                    ),
                    "body": html_body,
                    "text_body": text_body,
                    "finding_ids": ",".join(str(f.finding_id) for f in recipient_findings),
                    "status": status,
                    "kind": "master",
                }
            )

    email_gen_timer.log("Email generation")
    report.record("Email generation", email_gen_timer.total)

    if unresolved_items:
        lines = ["The following findings could not be routed to a Senior:", ""]
        for item in unresolved_items:
            finding = item["finding"]
            # "RBM is Vacant. Email not sent." -- the 2026-07-28 simplified
            # rule: BM/ABM route ONLY to their own RBM, no further
            # fallback (see app.hierarchy_service.FALLBACK_CHAINS), so a
            # hierarchy entry that exists but still ends up here always
            # means exactly this: its RBM is vacant, missing, null, or has
            # no email on file -- never "every rung of the chain," since
            # there is only one rung to check now.
            reason = (
                "RBM is Vacant. Email not sent."
                if item["has_hierarchy_entry"]
                else "no Organization Data entry found for this employee, so no Senior could be resolved"
            )
            lines.append(f"- {finding.employee_name} ({finding.employee_code}) on {finding.visit_date}: {reason}")

        drafts.append(
            {
                "manager_name": None,
                "manager_email": None,
                "subject": "Unresolved findings — manual review needed",
                "body": "\n".join(lines),
                "finding_ids": ",".join(str(item["finding"].finding_id) for item in unresolved_items),
                "status": "Unresolved",
                "kind": "unresolved",
            }
        )

    return drafts


def preview_email_batch(import_id: int, progress_callback=None) -> dict:
    """Testing-safe counterpart to send_all_emails, used when Automatic
    Email Sending is OFF (see app/email_settings_service.py). Runs the
    exact same pipeline as build_email_batch -- hierarchy resolution,
    Hospital Suppression, Region Suppression, reverse geocoding, HTML/text
    generation -- and persists every draft to EmailNotification (status
    "Draft", or "Unresolved" for findings that couldn't be routed) so they
    appear in the Email Center exactly like a real batch would, and can be
    opened for inspection via the existing double-click preview. The one
    thing that never happens here is the actual SMTP send: no connection
    is opened, no message is transmitted anywhere, and no finding's
    `status`/`notification_status` is advanced to Reviewed/Sent -- only
    Hospital/Region Suppression's own decisions (already applied inside
    build_email_batch) are reflected, since those aren't about sending.

    This lets the complete pipeline be validated against real production
    data with zero risk of actually emailing anyone, while still showing
    exactly what would have been sent.

    Returns a dict with `draft_count` (routable RBM/master drafts),
    `unresolved_count`, `skipped_count` (a configured Master Email
    recipient whose Division filter matched nothing this run -- see
    STATUS_SKIPPED_NO_DATA), and `drafts`.
    """
    drafts = build_email_batch(import_id, progress_callback=progress_callback)

    draft_count = 0
    unresolved_count = 0
    skipped_count = 0

    session = get_session()
    try:
        for draft in drafts:
            if draft["status"] == "Unresolved":
                unresolved_count += 1
            elif draft["status"] == STATUS_SKIPPED_NO_DATA:
                skipped_count += 1
            else:
                draft_count += 1
            session.add(
                EmailNotification(
                    import_id=import_id,
                    manager_name=draft["manager_name"],
                    manager_email=draft["manager_email"],
                    subject=draft["subject"],
                    body=draft["body"],
                    finding_ids=draft["finding_ids"],
                    status=draft["status"],
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
            )
        session.commit()
    finally:
        session.close()

    logger.info(
        f"Email batch PREVIEW ONLY for import_id={import_id} (Automatic Email Sending is OFF): "
        f"{draft_count} draft(s) generated, {unresolved_count} unresolved, {skipped_count} skipped "
        "(no data) -- nothing was sent to any recipient."
    )

    if progress_callback:
        progress_callback("finalizing", label="Completed (preview only — nothing sent).")

    from app.email_sync_service import sync_email_notifications_for_import

    sync_email_notifications_for_import(import_id)

    return {
        "draft_count": draft_count,
        "unresolved_count": unresolved_count,
        "skipped_count": skipped_count,
        "drafts": drafts,
    }


def send_all_emails(import_id: int, progress_callback=None) -> dict:
    """Build the email batch (fully, in memory) and actually send each
    routable draft — one per affected RBM, plus one per configured Master
    Email recipient (see app/master_email_recipients_service.py) — via
    Gmail SMTP over a single reused connection. Only an RBM draft's
    success marks its covered findings Reviewed; every master draft is an
    informational copy and never changes finding status on its own.
    Findings whose email failed to send, or that were Unresolved, are left
    Open so they're retried the next time this is called for this session.

    Each draft's outcome (Sent/Failed/Unresolved/Skipped - No Data) is
    written to the DB and reflected in the in-memory send_state progress
    *as soon as that draft is handled* — the Email Center's polling picks
    these up live rather than only after the whole batch finishes. The DB
    transaction itself is committed every COMMIT_EVERY_N_EMAILS drafts
    rather than per-email (a disk commit isn't free) or once at the very
    end (that's what used to make the whole run invisible until it was
    already over).

    Returns a dict with `sent_count`, `failed_count`, `unresolved_count`,
    `skipped_count` (a Master Email recipient whose Division filter
    matched nothing this run), `suppressed_count`, and `drafts`.
    """
    progress_callback = progress_callback or (lambda stage, **kwargs: None)
    report = get_current_report()

    drafts = build_email_batch(import_id, progress_callback=progress_callback)

    sent_count = 0
    failed_count = 0
    unresolved_count = 0
    skipped_count = 0

    routable_drafts = [d for d in drafts if d["status"] not in ("Unresolved", STATUS_SKIPPED_NO_DATA)]
    total_drafts = len(drafts)

    progress_callback("sending", label="Sending emails...", completed=0, total=total_drafts)
    update_progress("sending", completed=0, total=total_drafts)

    connection = None
    sender_email = None
    connection_error = None
    if routable_drafts:
        with report.timed("SMTP connection"):
            try:
                connection, sender_email = open_smtp_connection()
            except Exception as exc:
                connection_error = exc

    session = get_session()
    try:
        with report.timed("Email sending"):
            for index, draft in enumerate(drafts, start=1):
                if draft["status"] == "Unresolved":
                    session.add(
                        EmailNotification(
                            import_id=import_id,
                            manager_name=draft["manager_name"],
                            manager_email=draft["manager_email"],
                            subject=draft["subject"],
                            body=draft["body"],
                            finding_ids=draft["finding_ids"],
                            status="Unresolved",
                            created_at=datetime.now(),
                            updated_at=datetime.now(),
                        )
                    )
                    unresolved_count += 1
                elif draft["status"] == STATUS_SKIPPED_NO_DATA:
                    session.add(
                        EmailNotification(
                            import_id=import_id,
                            manager_name=draft["manager_name"],
                            manager_email=draft["manager_email"],
                            subject=draft["subject"],
                            body=draft["body"],
                            finding_ids=draft["finding_ids"],
                            status=STATUS_SKIPPED_NO_DATA,
                            created_at=datetime.now(),
                            updated_at=datetime.now(),
                        )
                    )
                    skipped_count += 1
                else:
                    try:
                        if connection_error is not None:
                            raise connection_error
                        send_via_connection(
                            connection,
                            sender_email,
                            draft["manager_email"],
                            draft["subject"],
                            draft["body"],
                            draft.get("text_body"),
                        )
                    except Exception as exc:
                        logger.error(f"Failed to send email to {draft['manager_email']}: {exc}")
                        draft["status"] = "Failed"
                        session.add(
                            EmailNotification(
                                import_id=import_id,
                                manager_name=draft["manager_name"],
                                manager_email=draft["manager_email"],
                                subject=draft["subject"],
                                body=draft["body"],
                                finding_ids=draft["finding_ids"],
                                status="Failed",
                                error_message=str(exc),
                                created_at=datetime.now(),
                                updated_at=datetime.now(),
                            )
                        )
                        failed_count += 1
                    else:
                        draft["status"] = "Sent"
                        session.add(
                            EmailNotification(
                                import_id=import_id,
                                manager_name=draft["manager_name"],
                                manager_email=draft["manager_email"],
                                subject=draft["subject"],
                                body=draft["body"],
                                finding_ids=draft["finding_ids"],
                                status="Sent",
                                created_at=datetime.now(),
                                updated_at=datetime.now(),
                                sent_at=datetime.now(),
                            )
                        )
                        sent_count += 1

                    # Only an RBM draft's own outcome drives its findings'
                    # notification status — the master email is an
                    # informational copy and never changes finding status.
                    if draft.get("kind") == "rbm":
                        if draft["status"] == "Sent":
                            for finding_id in draft["finding_ids"].split(","):
                                set_status(int(finding_id), "Reviewed")
                                set_notification_status(int(finding_id), "Sent")
                        elif draft["status"] == "Failed":
                            for finding_id in draft["finding_ids"].split(","):
                                set_notification_status(int(finding_id), "Failed")

                # UI feedback happens immediately per email regardless of
                # the DB commit cadence below.
                progress_callback("sending", label=draft["manager_name"], completed=index, total=total_drafts)
                update_progress("sending", label=draft["manager_name"], completed=index, total=total_drafts)

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

    suppressed_session = get_session()
    try:
        suppressed_count = (
            suppressed_session.query(InvestigationFinding)
            .filter_by(import_id=import_id, notification_status="Hospital Suppressed")
            .count()
        )
        region_suppressed_count = (
            suppressed_session.query(InvestigationFinding)
            .filter_by(import_id=import_id, notification_status="Suppressed - Region Rule")
            .count()
        )
    finally:
        suppressed_session.close()

    logger.info(
        f"Email batch for import_id={import_id}: {sent_count} sent, "
        f"{failed_count} failed, {unresolved_count} unresolved, {skipped_count} skipped (no data), "
        f"{suppressed_count} hospital-suppressed, {region_suppressed_count} region-suppressed"
    )

    progress_callback("finalizing", label="Completed.")

    from app.email_sync_service import sync_email_notifications_for_import
    from app.findings_sync_service import sync_findings_for_import

    sync_findings_for_import(import_id)
    sync_email_notifications_for_import(import_id)

    return {
        "sent_count": sent_count,
        "failed_count": failed_count,
        "unresolved_count": unresolved_count,
        "skipped_count": skipped_count,
        "suppressed_count": suppressed_count,
        "region_suppressed_count": region_suppressed_count,
        "drafts": drafts,
    }
