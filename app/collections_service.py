"""Collections Action Center engine.

Scope: the daily operational working set for the Accounts & Collections
team -- currently outstanding invoices needing follow-up. Deliberately
independent of app/payment_analytics_service.py (the six-month Historical
Payment Analytics engine): different table, different upload, different
question ("who's overdue right now?" vs "how has this customer paid over
the last six months?"). No code is shared between the two beyond copying
the same small, generic helpers (_clean_str, _parse_date) -- kept
duplicated on purpose, so a future change to one module's date-parsing
never silently changes the other's.

Storage: one table, outstanding_invoices, on the config/main database
(get_config_session()) -- Collections has no Developer Mode concept of
its own, same as the rest of Payment Analytics. Fully replaced (delete-
all + reinsert) every time a new Outstanding Report is uploaded -- see
process_outstanding_report()'s docstring for why a full replace is
correct here (Daily Refresh).

Dynamic date logic: Days Outstanding, Days Over Due, and Status are never
stored -- they're computed fresh, against date.today(), every time
get_outstanding_invoices() is called (see compute_days_and_status()).
This is the one property that makes this module different from Historical
Payment Analytics' Payment Days (which is frozen the moment Clear Date is
known): an outstanding invoice's urgency changes every day even if nobody
uploads anything, so nothing here is allowed to freeze that at upload
time.

Filtering/sorting performance: get_outstanding_invoices() does one DB
round trip and returns every invoice (with status/days already computed).
The UI loads this once per upload/page-visit and does every filter,
search, and sort over that in-memory list (see sort_invoices(),
matches_days_over_due_bucket(), matches_due_date_filter()) -- the same
convention ui/payment_customer_analytics_page.py already uses for
get_all_customer_profiles(). Re-querying the database on every filter
change would be wasted work; a plain Python sort/filter over a few
thousand dicts is effectively instant.

Future-proofing: every invoice already has a stable identity (`id`) and a
persisted `followed_up` flag, and every read goes through
get_outstanding_invoices() rather than being scattered across the UI.
Automatic reminder emails, daily ageing reports, and escalation workflows
can all be built as new read-only consumers of that same function (or a
scheduled job calling process_outstanding_report() on a timer) without
touching this module's schema or the upload pipeline.
"""

import re
from collections import Counter
from datetime import date, datetime, timedelta

import pandas as pd
from loguru import logger

from app.payment_parameters_service import get_collections_ageing_thresholds
from database.connection import get_config_session
from database.models import OutstandingInvoice

STATUS_NOT_YET_DUE = "Not Yet Due"
STATUS_DUE_SOON = "Due Soon"
STATUS_OVERDUE = "Overdue"
STATUS_CRITICAL = "Critical"
# Sort priority, most urgent first -- see sort_invoices().
STATUS_PRIORITY = [STATUS_CRITICAL, STATUS_OVERDUE, STATUS_DUE_SOON, STATUS_NOT_YET_DUE]

# Fixed list (not derived from the upload, unlike H.Q.) -- these are the
# three real organizational units already used elsewhere in Saffron
# Automation's hierarchy system. "Division" is the report's own column
# name for this field.
TEAM_OPTIONS = ["All Divisions", "Onyx", "Xandra", "Guardians"]
STATUS_FILTER_OPTIONS = ["All", STATUS_NOT_YET_DUE, STATUS_DUE_SOON, STATUS_OVERDUE, STATUS_CRITICAL, "Followed Up"]
DAYS_OVER_DUE_BUCKET_OPTIONS = ["All", "Not Yet Due", "1–5 Days", "6–10 Days", "11–20 Days", "More than 20 Days"]
DUE_DATE_FILTER_OPTIONS = ["All", "Due Today", "Due This Week", "Already Overdue", "Custom Date Range"]
FOLLOW_UP_FILTER_OPTIONS = ["All", "Pending Follow-up", "Already Followed Up"]


def _clean_str(value) -> str | None:
    if value is None:
        return None
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _parse_date(value) -> date | None:
    """Best-effort parse of a date-like cell into a plain `date`, or None
    if missing/unparseable. Never raises. Deliberately duplicated from
    app/payment_analytics_service.py's identical helper -- see module
    docstring."""
    if value is None:
        return None
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None

    text = str(value).strip()
    if not text or text.lower() in ("nan", "nat", "none"):
        return None

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date()


def _parse_amount(value) -> float | None:
    """Best-effort parse of a Bill Amount cell into a float, or None if
    missing/unparseable. Never raises. Tolerates a currency symbol and
    thousands separators some exports include (e.g. "₹1,23,456.00")."""
    if value is None:
        return None
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", text)
    if not cleaned or cleaned in ("-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def compute_days_and_status(
    lr_date: date, due_date: date, today: date | None = None,
    thresholds: tuple[int, int] | None = None,
) -> tuple[int, int, str]:
    """Steps 2-3: Days Outstanding, Days Over Due, Status -- always
    computed against the current system date (or an injected `today`,
    for tests), never a stored/frozen value.

    Green is a condition, not a threshold, and is never configurable: if
    today is on or before Due Date, Days Over Due = 0 and Status = Not Yet
    Due, full stop. Only once Due Date has passed do the (user-editable,
    see app/payment_parameters_service.get_collections_ageing_thresholds())
    Yellow/Orange maximum Days Over Due apply -- Due Soon up to
    yellow_max, Overdue up to orange_max, Critical beyond that.
    `thresholds`, if given, avoids a parameter-store read per call --
    get_outstanding_invoices() fetches once and passes it through."""
    today = today or date.today()
    days_outstanding = (today - lr_date).days

    if today <= due_date:
        return days_outstanding, 0, STATUS_NOT_YET_DUE

    yellow_max, orange_max = thresholds or get_collections_ageing_thresholds()
    days_over_due = (today - due_date).days
    if days_over_due <= yellow_max:
        status = STATUS_DUE_SOON
    elif days_over_due <= orange_max:
        status = STATUS_OVERDUE
    else:
        status = STATUS_CRITICAL
    return days_outstanding, days_over_due, status


def process_outstanding_report(df: pd.DataFrame) -> dict:
    """Step 1 (columns already validated upstream) + Step 7 (Daily
    Refresh): parse every row and replace the entire stored invoice list.
    Replacing every row is also what resets every `followed_up` flag --
    only one Outstanding Report is ever active at a time, so a new upload
    is a full daily refresh, not a merge.

    Never raises. A row with a missing Party Name or a missing/invalid
    L.R. Date or Due Date is skipped and logged (loguru's rotating file
    sink, the same error-log mechanism every other engine in this app
    uses), not fatal to the rest of the batch. Bill Amount and Month are
    stored best-effort (None if missing/unparseable) -- neither is a
    reason to skip a row.

    Returns: {rows_read, rows_valid, rows_skipped, errors, total_invoices}
    """
    rows_read = len(df)
    invoices: list[dict] = []
    errors: list[dict] = []

    for idx, row in df.iterrows():
        display_row = idx + 2  # 1-based Excel row, +1 for the header row
        party_name = _clean_str(row.get("Party Name"))
        invoice_no = _clean_str(row.get("Bill No."))

        if party_name is None:
            errors.append({"row": display_row, "party_name": None, "invoice_no": invoice_no, "reason": "Missing Party Name"})
            continue

        lr_date = _parse_date(row.get("L.R. Date"))
        if lr_date is None:
            errors.append({"row": display_row, "party_name": party_name, "invoice_no": invoice_no, "reason": "Missing or invalid L.R. Date"})
            continue

        due_date = _parse_date(row.get("Due Date"))
        if due_date is None:
            errors.append({"row": display_row, "party_name": party_name, "invoice_no": invoice_no, "reason": "Missing or invalid Due Date"})
            continue

        invoices.append(
            {
                "party_name": party_name,
                "team": _clean_str(row.get("Division")),
                "hq": _clean_str(row.get("H.Q.")),
                "invoice_no": invoice_no,
                "lr_date": lr_date,
                "due_date": due_date,
                "bill_amount": _parse_amount(row.get("Bill Amount")),
                "month": _clean_str(row.get("Month")),
            }
        )

    for err in errors:
        logger.warning(
            f"Outstanding Report: skipped row {err['row']} "
            f"(Party Name={err['party_name']!r}, Bill No.={err['invoice_no']!r}) -- {err['reason']}"
        )

    now = datetime.now()
    session = get_config_session()
    try:
        session.query(OutstandingInvoice).delete()
        for invoice in invoices:
            session.add(OutstandingInvoice(**invoice, followed_up=0, uploaded_at=now, updated_at=now))
        session.commit()
    finally:
        session.close()

    logger.info(
        f"Processed Outstanding Report: {rows_read} row(s) read, {len(invoices)} valid, {len(errors)} skipped"
    )

    return {
        "rows_read": rows_read,
        "rows_valid": len(invoices),
        "rows_skipped": len(errors),
        "errors": errors,
        "total_invoices": len(invoices),
    }


# --- Read-side ---------------------------------------------------------


def _invoice_row_to_dict(row: OutstandingInvoice, thresholds: tuple[int, int]) -> dict:
    days_outstanding, days_over_due, status = compute_days_and_status(row.lr_date, row.due_date, thresholds=thresholds)
    return {
        "id": row.id,
        "party_name": row.party_name,
        "team": row.team,
        "hq": row.hq,
        "invoice_no": row.invoice_no,
        "lr_date": row.lr_date,
        "due_date": row.due_date,
        "bill_amount": row.bill_amount,
        "month": row.month,
        "days_outstanding": days_outstanding,
        "days_over_due": days_over_due,
        "status": status,
        "followed_up": bool(row.followed_up),
    }


def get_outstanding_invoices() -> list[dict]:
    """Every outstanding invoice, with Days Outstanding/Days Over Due/
    Status computed fresh against today's date and the current
    Yellow/Orange ageing thresholds (fetched once here, not once per
    invoice). See module docstring -- the UI loads this once (on_show /
    right after an upload) and filters/sorts the returned list in memory,
    not per filter change."""
    thresholds = get_collections_ageing_thresholds()
    session = get_config_session()
    try:
        rows = session.query(OutstandingInvoice).all()
        return [_invoice_row_to_dict(r, thresholds) for r in rows]
    finally:
        session.close()


def get_hq_options() -> list[str]:
    """Distinct H.Q. values currently present, for the HQ filter dropdown
    -- populated dynamically from the uploaded report (unlike Division,
    which is a fixed list -- see TEAM_OPTIONS)."""
    session = get_config_session()
    try:
        values = {r.hq for r in session.query(OutstandingInvoice.hq).all() if r.hq}
    finally:
        session.close()
    return ["All HQs"] + sorted(values)


def set_follow_up(invoice_id: int, followed_up: bool) -> None:
    """Step 6: persist the Follow-up checkbox so it survives a page
    revisit or an app restart -- cleared only by the next Outstanding
    Report upload (process_outstanding_report() replaces every row).
    Bumps updated_at -- the reliable local "last modified" timestamp the
    app-wide Last-Modified-Wins sync rule compares against the cloud (see
    app/payment_sync_service.py) -- so this toggle is recognized as the
    newer copy on the next reconcile."""
    session = get_config_session()
    try:
        invoice = session.get(OutstandingInvoice, invoice_id)
        if invoice is not None:
            invoice.followed_up = 1 if followed_up else 0
            invoice.updated_at = datetime.now()
            session.commit()
    finally:
        session.close()


def summarize_invoices(invoices: list[dict]) -> dict:
    """Step 5 (also reused for Filter Behaviour's "update KPI cards
    automatically"): {total, not_yet_due, due_soon, overdue, critical}
    over whatever list is passed in -- the full unfiltered set, or the
    currently filtered one. A pure function over already-fetched data, not
    a DB query, so it's cheap enough to call on every filter change."""
    counts = Counter(inv["status"] for inv in invoices)
    return {
        "total": len(invoices),
        "not_yet_due": counts.get(STATUS_NOT_YET_DUE, 0),
        "due_soon": counts.get(STATUS_DUE_SOON, 0),
        "overdue": counts.get(STATUS_OVERDUE, 0),
        "critical": counts.get(STATUS_CRITICAL, 0),
    }


def sort_invoices(invoices: list[dict]) -> list[dict]:
    """Step 4: Critical > Overdue > Due Soon > Not Yet Due, highest Days
    Over Due first within each category. Followed-up invoices always sort
    after every not-yet-followed-up invoice, regardless of status (Step
    6: move to the bottom, never delete, never mixed back in)."""
    def key(inv):
        priority = STATUS_PRIORITY.index(inv["status"]) if inv["status"] in STATUS_PRIORITY else len(STATUS_PRIORITY)
        return (inv["followed_up"], priority, -inv["days_over_due"])
    return sorted(invoices, key=key)


# --- Filter predicates (pure functions -- easy to unit test, reused by
# the UI's client-side filtering; see module docstring) -------------------


def matches_days_over_due_bucket(invoice: dict, bucket: str) -> bool:
    if bucket == "All":
        return True
    if bucket == "Not Yet Due":
        return invoice["status"] == STATUS_NOT_YET_DUE
    days = invoice["days_over_due"]
    if bucket == "1–5 Days":
        return 1 <= days <= 5
    if bucket == "6–10 Days":
        return 6 <= days <= 10
    if bucket == "11–20 Days":
        return 11 <= days <= 20
    if bucket == "More than 20 Days":
        return days > 20
    return True


def matches_due_date_filter(
    invoice: dict, filter_value: str,
    custom_start: date | None = None, custom_end: date | None = None,
    today: date | None = None,
) -> bool:
    if filter_value == "All":
        return True
    today = today or date.today()
    due = invoice["due_date"]
    if filter_value == "Due Today":
        return due == today
    if filter_value == "Due This Week":
        return today <= due <= today + timedelta(days=6)
    if filter_value == "Already Overdue":
        return due < today
    if filter_value == "Custom Date Range":
        if custom_start is not None and due < custom_start:
            return False
        if custom_end is not None and due > custom_end:
            return False
        return True
    return True
