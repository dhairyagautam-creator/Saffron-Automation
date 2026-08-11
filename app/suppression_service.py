"""Canonical "is this finding actionable, or suppressed?" decision, shared by
the Master page and the HR-Based findings table.

There is exactly ONE suppression rule, reused here -- nothing is
reimplemented:

  * Region suppression -- app.region_suppression.is_region_suppressed
    (Kerala/Punjab, per that module's SUPPRESSED_REGIONS). Deterministic and
    cheap, so it is evaluated LIVE from each finding's Region (raw_visits), and
    therefore applies immediately -- a suppressed-region employee never shows
    as actionable, even before any email/preview has run.

  * Hospital suppression -- app.hospital_service, applied by the email pass in
    app.notification_service.build_email_batch and PERSISTED on the finding's
    notification_status ("Hospital Suppressed"). It needs a network lookup, so
    it is inherently a post-analysis decision; the view layer reads the
    persisted result rather than re-doing the lookup.

The email pipeline persists both decisions on notification_status; this module
mirrors the SAME rules for the read-only views so a suppressed employee is
withheld from the email, the HR-Based table, and the Master page identically.
"""

from sqlalchemy import inspect, text

from app.findings_service import SUPPRESSED_NOTIFICATION_STATUSES
from app.region_suppression import is_region_suppressed
from database.connection import get_data_engine
from database.import_service import IMPORT_ID_COLUMN, RAW_VISITS_TABLE

EMPLOYEE_CODE_COLUMN = "Employee Code"
DATE_COLUMN = "Date"
REGION_COLUMN = "Region"


def region_by_employee_date(import_id: int) -> dict:
    """{(employee_code, 'dd-mm-YYYY'): region} for one import's raw_visits --
    the same key an InvestigationFinding maps to (employee_code + visit_date).
    Empty if the table or any needed column is absent (older files without a
    Region column simply can't be region-suppressed)."""
    if import_id is None:
        return {}
    engine = get_data_engine()
    if not inspect(engine).has_table(RAW_VISITS_TABLE):
        return {}
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({RAW_VISITS_TABLE})"))}
        needed = {IMPORT_ID_COLUMN, EMPLOYEE_CODE_COLUMN, DATE_COLUMN, REGION_COLUMN}
        if not needed.issubset(cols):
            return {}
        rows = conn.execute(
            text(
                f'SELECT "{EMPLOYEE_CODE_COLUMN}", "{DATE_COLUMN}", "{REGION_COLUMN}" '
                f"FROM {RAW_VISITS_TABLE} WHERE {IMPORT_ID_COLUMN} = :iid"
            ),
            {"iid": import_id},
        ).fetchall()
    result: dict = {}
    for code, date_str, region in rows:
        key = (code, date_str)
        if region and key not in result:
            result[key] = region
    return result


def suppressed_finding_ids(findings: list, region_by_key: dict) -> set:
    """Finding ids that are suppressed (region- or hospital-). Pure -- the
    region map is injected, so this is testable without a database."""
    out = set()
    for f in findings:
        if f.notification_status in SUPPRESSED_NOTIFICATION_STATUSES:
            out.add(f.finding_id)
            continue
        key = (f.employee_code, f.visit_date.strftime("%d-%m-%Y")) if f.visit_date else None
        if key is not None and is_region_suppressed(region_by_key.get(key)):
            out.add(f.finding_id)
    return out


def suppressed_finding_ids_for_import(findings: list, import_id: int) -> set:
    """suppressed_finding_ids for one import, sourcing the region map itself."""
    return suppressed_finding_ids(findings, region_by_employee_date(import_id))


def region_suppressed_ids(findings: list, region_by_key: dict) -> set:
    """Finding ids whose employee/date region is region-suppressed by the ONE
    canonical live rule (app.region_suppression.is_region_suppressed) --
    independent of any persisted notification_status. Pure (region map
    injected), so it's testable without a database."""
    out = set()
    for f in findings:
        key = (f.employee_code, f.visit_date.strftime("%d-%m-%Y")) if f.visit_date else None
        if key is not None and is_region_suppressed(region_by_key.get(key)):
            out.add(f.finding_id)
    return out


def region_suppressed_finding_ids(findings: list, import_id: int) -> set:
    """region_suppressed_ids for one import, sourcing the region map itself.
    The Location-Based tab uses this so a region-suppressed finding is
    labelled/tinted the moment it exists, matching what Master/HR/email
    already decide, rather than waiting for the persisted status."""
    return region_suppressed_ids(findings, region_by_employee_date(import_id))


def filter_actionable(findings: list, import_id: int) -> list:
    """Drop suppressed findings -- what Master aggregation consumes."""
    suppressed = suppressed_finding_ids_for_import(findings, import_id)
    return [f for f in findings if f.finding_id not in suppressed]
