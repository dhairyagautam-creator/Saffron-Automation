"""Historical Payment Analytics engine, including the rolling six-month
window.

Scope: reads a validated Payment Report DataFrame (see
app/excel_validation.py's load_historical_payment_report /
load_monthly_payment_report), computes Payment Days per invoice, groups
invoices into per-customer profiles, and assigns each customer a risk
category. This module does NOT implement Live Payment Tracking.

Storage -- three tables, all on the config/main database
(get_config_session()) -- Payment Analytics has no Developer Mode concept
of its own, same as Inventory Monitoring's tables. Everything lives in
real SQLite tables, never in an in-memory structure, so the active window
survives an application restart with no rebuild step:

- payment_invoices: the permanent invoice ledger. An invoice is never
  deleted once stored, even after its month rolls out of the active
  window -- see "Archiving" below.
- payment_active_months: the current rolling window membership -- at most
  6 rows. This table, not any flag on payment_invoices, is the single
  source of truth for which invoices currently feed every calculation.
- payment_customer_profiles: one row per Party Name, fully recalculated
  from active-window invoices only, every time the window changes.

Month identity: every invoice's canonical ordering key is
`(year, month_number)`, taken from its own LR Date -- never parsed from
the free-text Month column, whose format is never assumed (could be
"Jan-24", "January", "2024-01", ...). The Month column's text is kept
purely as a *display* label (see _most_common()'s use for it below).
Calendar-month arithmetic on `(year, month_number)` tuples is
unambiguous, which is what makes "next chronological month" / "already
uploaded" / "skips a month" / "older than the window" all simple, correct
tuple comparisons instead of fragile string parsing.

Archiving: process_historical_report() and process_monthly_report() both
only ever add rows to payment_invoices, never delete them. "Removing the
oldest month" means removing its payment_active_months row -- the
invoices themselves stay in payment_invoices permanently (Data Integrity:
archived invoices must never influence a calculation, but they don't need
to disappear either -- useful raw material for the future Live Payment
Tracking module). Every read-side getter below filters to
payment_active_months' current membership before computing anything.

Robustness: a row with a missing Party Name, a missing/invalid LR Date or
Clear Date, or a negative computed Payment Days is skipped and logged via
loguru's rotating file sink (app/logging_config.py) -- the same
error-log mechanism every other engine in this app already uses. Neither
process_historical_report() nor process_monthly_report() ever raises; a
bad row degrades the batch, it never crashes it.
"""

from collections import Counter
from datetime import date, datetime

import pandas as pd
from loguru import logger

from app.payment_parameters_service import get_historical_risk_thresholds
from database.connection import get_config_session
from database.models import PaymentActiveMonth, PaymentCustomerProfile, PaymentInvoice

RISK_GREEN = "Green"
RISK_YELLOW = "Yellow"
RISK_ORANGE = "Orange"
RISK_RED = "Red"
RISK_CATEGORIES = [RISK_GREEN, RISK_YELLOW, RISK_ORANGE, RISK_RED]

ACTIVE_WINDOW_SIZE = 6


def classify_risk(average_payment_days: float, thresholds: tuple[float, float, float] | None = None) -> str:
    """Green (< green_max) / Yellow (< yellow_max) / Orange (< orange_max)
    / Red (>= orange_max). Thresholds are user-editable from the
    Parameters page (app/payment_parameters_service.py), defaulting to
    30/45/60 -- never hardcoded here. `thresholds`, if given, avoids a
    parameter-store read per call; callers processing many rows (see
    _build_customer_profiles()) fetch once and pass it through."""
    green_max, yellow_max, orange_max = thresholds or get_historical_risk_thresholds()
    if average_payment_days < green_max:
        return RISK_GREEN
    if average_payment_days < yellow_max:
        return RISK_YELLOW
    if average_payment_days < orange_max:
        return RISK_ORANGE
    return RISK_RED


def _clean_str(value) -> str | None:
    if value is None:
        return None
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _parse_date(value) -> date | None:
    """Best-effort parse of a date-like cell into a plain `date`, or None
    if missing/unparseable. Never raises -- an unparseable cell is treated
    as an invalid date, not a crash.

    Handles the three shapes pandas/openpyxl actually produce for an Excel
    date cell (pd.Timestamp, datetime.datetime, datetime.date) plus a
    plain string (parsed with pandas' flexible parser, retried with
    dayfirst=True for DD-MM-YYYY-style exports). A bare number is treated
    as invalid rather than guessed at as an Excel date serial, since a
    non-date-formatted cell holding a raw serial number is ambiguous."""
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


def _most_common(values: list):
    """The most frequent non-blank/non-None value in `values`, ties broken
    by whichever value appeared first. Generic over any hashable type --
    used both for a customer's Type values (str -> "Customer Type") and
    for a batch's detected (year, month_number) key (tuple)."""
    counts = Counter()
    first_seen = []
    for value in values:
        if not value:
            continue
        if value not in counts:
            first_seen.append(value)
        counts[value] += 1
    if not counts:
        return None
    best_count = max(counts.values())
    for value in first_seen:
        if counts[value] == best_count:
            return value
    return None


def _next_month_key(key: tuple[int, int]) -> tuple[int, int]:
    year, month = key
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _parse_invoice_rows(df: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    """Steps 1-2: read every row into an invoice dict -- Party Name, Type,
    Inv. No., LR/Due/Clear Date, Payment Days, plus the calendar
    `(year, month_number)` derived from LR Date (see module docstring).
    Rows with a missing Party Name, a missing/invalid LR or Clear Date, or
    a negative computed Payment Days are skipped and returned in `errors`
    instead, never raised.

    Shared by both process_historical_report() and
    process_monthly_report() -- the two entry points differ only in what
    they do with the resulting invoice list, not in how a single row is
    validated."""
    invoices: list[dict] = []
    errors: list[dict] = []

    for idx, row in df.iterrows():
        # +2: 1-based Excel row number, +1 for the header row -- an
        # approximation (real files may have banner rows before the
        # header), good enough for a human-readable log line.
        display_row = idx + 2
        party_name = _clean_str(row.get("Party Name"))
        invoice_no = _clean_str(row.get("Inv. No."))

        if party_name is None:
            errors.append({"row": display_row, "party_name": None, "invoice_no": invoice_no, "reason": "Missing Party Name"})
            continue

        lr_date = _parse_date(row.get("LR Date"))
        if lr_date is None:
            errors.append({"row": display_row, "party_name": party_name, "invoice_no": invoice_no, "reason": "Missing or invalid LR Date"})
            continue

        clear_date = _parse_date(row.get("Clear Date"))
        if clear_date is None:
            errors.append({"row": display_row, "party_name": party_name, "invoice_no": invoice_no, "reason": "Missing or invalid Clear Date"})
            continue

        payment_days = (clear_date - lr_date).days
        if payment_days < 0:
            errors.append(
                {
                    "row": display_row, "party_name": party_name, "invoice_no": invoice_no,
                    "reason": f"Clear Date is before LR Date (computed {payment_days} days)",
                }
            )
            continue

        invoices.append(
            {
                "month": _clean_str(row.get("Month")) or "",
                "year": lr_date.year,
                "month_number": lr_date.month,
                "party_name": party_name,
                "customer_type": _clean_str(row.get("Type")),
                "invoice_no": invoice_no,
                "lr_date": lr_date,
                "due_date": _parse_date(row.get("Due Date")),
                "clear_date": clear_date,
                "payment_days": payment_days,
            }
        )

    return invoices, errors


def _log_skipped_rows(report_label: str, errors: list[dict]) -> None:
    for err in errors:
        row_display = err["row"] if err["row"] is not None else "(month mismatch)"
        logger.warning(
            f"{report_label}: skipped row {row_display} "
            f"(Party Name={err['party_name']!r}, Inv. No.={err['invoice_no']!r}) -- {err['reason']}"
        )


def _build_customer_profiles(invoices: list[dict]) -> list[dict]:
    """Step 3 + Step 4: group invoices by Party Name and compute Total
    Invoices, Average Payment Days, Earliest/Latest Invoice (by LR Date --
    the invoice-raised date), and Risk Category. `invoices` must already
    be scoped to the active window -- this function has no opinion on
    which invoices it's given."""
    thresholds = get_historical_risk_thresholds()  # one read, not one per customer

    by_party: dict[str, list[dict]] = {}
    for invoice in invoices:
        by_party.setdefault(invoice["party_name"], []).append(invoice)

    profiles = []
    for party_name, group in by_party.items():
        payment_days_values = [inv["payment_days"] for inv in group]
        average_payment_days = sum(payment_days_values) / len(payment_days_values)
        lr_dates = [inv["lr_date"] for inv in group]
        profiles.append(
            {
                "party_name": party_name,
                "customer_type": _most_common([inv["customer_type"] for inv in group]),
                "total_invoices": len(group),
                "average_payment_days": average_payment_days,
                "earliest_invoice": min(lr_dates),
                "latest_invoice": max(lr_dates),
                "risk_category": classify_risk(average_payment_days, thresholds),
            }
        )
    return profiles


def _replace_customer_profiles(session, invoices: list[dict], now: datetime) -> list[dict]:
    """Delete-all + rebuild payment_customer_profiles from `invoices`
    (already active-window-scoped) -- shared by both entry points below."""
    profiles = _build_customer_profiles(invoices)
    session.query(PaymentCustomerProfile).delete()
    for profile in profiles:
        session.add(PaymentCustomerProfile(**profile, last_updated=now))
    return profiles


def _invoice_row_to_dict(row: PaymentInvoice) -> dict:
    """The subset of a stored PaymentInvoice row that _build_customer_profiles()
    actually reads -- used to recompute profiles from already-persisted
    rows (process_monthly_report()) without re-deriving anything."""
    return {
        "party_name": row.party_name,
        "customer_type": row.customer_type,
        "payment_days": row.payment_days,
        "lr_date": row.lr_date,
    }


def _active_month_keys(session) -> set[tuple[int, int]]:
    return {(r.year, r.month_number) for r in session.query(PaymentActiveMonth).all()}


# --- Write-side: Historical baseline + rolling monthly upload ---------------


def process_historical_report(df: pd.DataFrame) -> dict:
    """The one-time (or re-run) baseline load: replaces the entire stored
    dataset (all three tables) with what's in `df`. If the file spans more
    than 6 distinct months, only the 6 most recent become the active
    window -- older months are stored (archived) but excluded from every
    calculation, exactly like a month evicted by the rolling window below.

    Never raises. See module docstring for the row-level skip rules.

    Returns: {rows_read, rows_valid, rows_skipped, errors, customers_count,
    active_window, months_archived}.
    """
    rows_read = len(df)
    invoices, errors = _parse_invoice_rows(df)
    _log_skipped_rows("Historical Payment Report", errors)

    by_key: dict[tuple[int, int], list[dict]] = {}
    for invoice in invoices:
        by_key.setdefault((invoice["year"], invoice["month_number"]), []).append(invoice)

    distinct_keys = sorted(by_key.keys())
    active_keys = set(distinct_keys[-ACTIVE_WINDOW_SIZE:])
    archived_keys = set(distinct_keys) - active_keys

    labels_by_key = {
        key: _most_common([inv["month"] for inv in group]) or f"{key[0]}-{key[1]:02d}"
        for key, group in by_key.items()
    }
    active_invoices = [inv for key in sorted(active_keys) for inv in by_key[key]]

    now = datetime.now()
    session = get_config_session()
    try:
        session.query(PaymentInvoice).delete()
        session.query(PaymentActiveMonth).delete()

        for invoice in invoices:  # every valid invoice is stored, active or archived
            session.add(PaymentInvoice(**invoice, created_at=now))
        for key in sorted(active_keys):
            session.add(
                PaymentActiveMonth(year=key[0], month_number=key[1], month_label=labels_by_key[key], added_at=now)
            )

        profiles = _replace_customer_profiles(session, active_invoices, now)
        session.commit()
    finally:
        session.close()

    active_window = [labels_by_key[k] for k in sorted(active_keys)]
    logger.info(
        f"Processed Historical Payment Report: {rows_read} row(s) read, {len(invoices)} valid, "
        f"{len(errors)} skipped, {len(profiles)} customer profile(s) built, active window {active_window}, "
        f"{len(archived_keys)} month(s) archived"
    )

    return {
        "rows_read": rows_read,
        "rows_valid": len(invoices),
        "rows_skipped": len(errors),
        "errors": errors,
        "customers_count": len(profiles),
        "active_window": active_window,
        "months_archived": len(archived_keys),
    }


def process_monthly_report(df: pd.DataFrame) -> dict:
    """The rolling-window monthly upload. Detects which month `df`
    represents (the majority `(year, month_number)` among its valid rows
    -- see module docstring), then applies the upload rules:

    - No active window yet (first-ever month): always accepted.
    - Detected month already active: REJECTED, reason="duplicate_month".
    - Detected month is exactly the month after the current latest active
      month: ACCEPTED -- inserted, and if the window now exceeds 6 months
      the oldest active month is evicted (archived, not deleted).
    - Detected month is further ahead than that (a gap): REJECTED,
      reason="skips_months".
    - Detected month is anything else (at or before the window's current
      start but not already a member): REJECTED, reason="too_old".

    On acceptance, every customer profile is recalculated from scratch
    using only the (now up to 6) active months' invoices -- Dashboard and
    Customer Analytics read that table directly, so they reflect the new
    window on their very next render with no separate refresh step.

    Never raises. Rejections make no database changes at all -- detection
    is pure read, only the acceptance path writes.

    Returns on success: {success: True, uploaded_month, removed_month,
    active_window, total_customers_updated, total_invoices_in_window,
    rows_read, rows_valid, rows_skipped, errors}.
    Returns on rejection/no-data: {success: False, reason, message,
    detected_month, rows_read, rows_valid, rows_skipped, errors}.
    """
    rows_read = len(df)
    invoices, errors = _parse_invoice_rows(df)

    if not invoices:
        _log_skipped_rows("Monthly Payment Report", errors)
        logger.warning("Monthly Payment Report: no valid invoice rows found -- nothing to process")
        return {
            "success": False, "reason": "no_valid_data",
            "message": "No valid invoices were found in this file — see the application log for details.",
            "detected_month": None, "rows_read": rows_read, "rows_valid": 0,
            "rows_skipped": len(errors), "errors": errors,
        }

    detected_key = _most_common([(inv["year"], inv["month_number"]) for inv in invoices])
    matched = [inv for inv in invoices if (inv["year"], inv["month_number"]) == detected_key]
    mismatched = [inv for inv in invoices if (inv["year"], inv["month_number"]) != detected_key]
    detected_label = _most_common([inv["month"] for inv in matched]) or f"{detected_key[0]}-{detected_key[1]:02d}"

    for inv in mismatched:
        errors.append(
            {
                "row": None, "party_name": inv["party_name"], "invoice_no": inv["invoice_no"],
                "reason": f"Row's month does not match the detected upload month ({detected_label})",
            }
        )
    _log_skipped_rows("Monthly Payment Report", errors)

    session = get_config_session()
    try:
        active_rows = session.query(PaymentActiveMonth).order_by(
            PaymentActiveMonth.year, PaymentActiveMonth.month_number
        ).all()
        active_keys = {(r.year, r.month_number) for r in active_rows}

        if active_keys:
            max_key = max(active_keys)
            expected_next = _next_month_key(max_key)

            if detected_key in active_keys:
                return {
                    "success": False, "reason": "duplicate_month",
                    "message": "This month's data already exists.",
                    "detected_month": detected_label, "rows_read": rows_read, "rows_valid": len(matched),
                    "rows_skipped": len(errors), "errors": errors,
                }
            if detected_key > expected_next:
                return {
                    "success": False, "reason": "skips_months",
                    "message": "The uploaded month is not the next chronological month.",
                    "detected_month": detected_label, "rows_read": rows_read, "rows_valid": len(matched),
                    "rows_skipped": len(errors), "errors": errors,
                }
            if detected_key < expected_next:
                return {
                    "success": False, "reason": "too_old",
                    "message": "The uploaded month is older than the current active window.",
                    "detected_month": detected_label, "rows_read": rows_read, "rows_valid": len(matched),
                    "rows_skipped": len(errors), "errors": errors,
                }
            # else: detected_key == expected_next -> accepted below

        # --- Accept: insert the new month, evict the oldest if the window
        # is now over capacity, then recalculate everything downstream. ---
        now = datetime.now()
        for invoice in matched:
            session.add(PaymentInvoice(**invoice, created_at=now))
        session.add(
            PaymentActiveMonth(
                year=detected_key[0], month_number=detected_key[1], month_label=detected_label, added_at=now
            )
        )
        session.flush()

        active_rows = session.query(PaymentActiveMonth).order_by(
            PaymentActiveMonth.year, PaymentActiveMonth.month_number
        ).all()
        removed_month = None
        if len(active_rows) > ACTIVE_WINDOW_SIZE:
            oldest = active_rows[0]
            removed_month = oldest.month_label
            session.delete(oldest)
            session.flush()
            active_rows = active_rows[1:]

        active_keys = {(r.year, r.month_number) for r in active_rows}
        # Captured as plain strings now, while the session is still open --
        # active_rows' ORM attributes become unreadable once session.close()
        # below expires/detaches them.
        active_window_labels = [r.month_label for r in active_rows]
        active_invoices = [
            _invoice_row_to_dict(r) for r in session.query(PaymentInvoice).all()
            if (r.year, r.month_number) in active_keys
        ]
        profiles = _replace_customer_profiles(session, active_invoices, now)
        session.commit()
    finally:
        session.close()

    logger.info(
        f"Processed Monthly Payment Report: uploaded {detected_label}, removed "
        f"{removed_month or '(window not yet full)'}, active window {active_window_labels}, "
        f"{len(profiles)} customer profile(s) recalculated"
    )

    return {
        "success": True,
        "uploaded_month": detected_label,
        "removed_month": removed_month,
        "active_window": active_window_labels,
        "total_customers_updated": len(profiles),
        "total_invoices_in_window": len(active_invoices),
        "rows_read": rows_read,
        "rows_valid": len(matched),
        "rows_skipped": len(errors),
        "errors": errors,
    }


# --- Read-side: Dashboard + Customer Analytics -----------------------------


def _profile_to_dict(profile: PaymentCustomerProfile) -> dict:
    return {
        "party_name": profile.party_name,
        "customer_type": profile.customer_type,
        "total_invoices": profile.total_invoices,
        "average_payment_days": profile.average_payment_days,
        "earliest_invoice": profile.earliest_invoice,
        "latest_invoice": profile.latest_invoice,
        "risk_category": profile.risk_category,
    }


def get_all_customer_profiles() -> list[dict]:
    """Every customer profile, for the Customer Analytics page -- Search,
    Sorting, and Filtering all happen client-side over this full list (the
    same convention ui/findings_page.py already uses), not here. Already
    scoped to the active window -- payment_customer_profiles is only ever
    rebuilt from active-window invoices (see process_historical_report /
    process_monthly_report above)."""
    session = get_config_session()
    try:
        profiles = session.query(PaymentCustomerProfile).order_by(PaymentCustomerProfile.party_name).all()
        return [_profile_to_dict(p) for p in profiles]
    finally:
        session.close()


def get_top_customers(limit: int = 5) -> list[dict]:
    """The `limit` customers with the most invoices -- the closest
    available proxy for "biggest account" given the required columns
    carry no monetary amount field."""
    session = get_config_session()
    try:
        profiles = (
            session.query(PaymentCustomerProfile)
            .order_by(PaymentCustomerProfile.total_invoices.desc())
            .limit(limit)
            .all()
        )
        return [_profile_to_dict(p) for p in profiles]
    finally:
        session.close()


def get_critical_customers() -> list[dict]:
    """Red-risk customer profiles only, sorted by Average Payment Days
    descending -- the Dashboard's "who do I need to chase today" table.

    Adds `last_payment_date`: the most recent Clear Date among the
    customer's active-window invoices (when they last actually paid) --
    distinct from the profile's own earliest/latest_invoice, which are
    LR-Date-based (when invoices were raised, not when they were paid)."""
    session = get_config_session()
    try:
        profiles = (
            session.query(PaymentCustomerProfile)
            .filter_by(risk_category=RISK_RED)
            .order_by(PaymentCustomerProfile.average_payment_days.desc())
            .all()
        )
        active_keys = _active_month_keys(session)
        invoices = [i for i in session.query(PaymentInvoice).all() if (i.year, i.month_number) in active_keys]
    finally:
        session.close()

    last_payment_by_party: dict[str, date] = {}
    for invoice in invoices:
        current = last_payment_by_party.get(invoice.party_name)
        if current is None or invoice.clear_date > current:
            last_payment_by_party[invoice.party_name] = invoice.clear_date

    result = []
    for profile in profiles:
        row = _profile_to_dict(profile)
        row["last_payment_date"] = last_payment_by_party.get(profile.party_name)
        result.append(row)
    return result


def get_dashboard_summary() -> dict:
    """Step 5: {total_customers, average_collection_time, green_customers,
    yellow_customers, orange_customers, red_customers} -- computed
    exclusively from the active six-month window.

    average_collection_time is the mean Payment Days across every active
    invoice (not the mean of each customer's own average) -- an overall
    portfolio figure, not distorted by customers with very different
    invoice counts."""
    session = get_config_session()
    try:
        profiles = session.query(PaymentCustomerProfile).all()
        active_keys = _active_month_keys(session)
        invoices = [i for i in session.query(PaymentInvoice).all() if (i.year, i.month_number) in active_keys]
    finally:
        session.close()

    total_customers = len(profiles)
    average_collection_time = (sum(i.payment_days for i in invoices) / len(invoices)) if invoices else 0.0

    counts = {category: 0 for category in RISK_CATEGORIES}
    for profile in profiles:
        if profile.risk_category in counts:
            counts[profile.risk_category] += 1

    return {
        "total_customers": total_customers,
        "average_collection_time": average_collection_time,
        "green_customers": counts[RISK_GREEN],
        "yellow_customers": counts[RISK_YELLOW],
        "orange_customers": counts[RISK_ORANGE],
        "red_customers": counts[RISK_RED],
    }


def get_monthly_trend() -> list[dict]:
    """Average Payment Days per active month, for the Dashboard's trend
    chart -- restricted to the active six-month window (an archived
    month must never reappear in a chart). Ordered chronologically by
    each month's own `(year, month_number)` key, never by sorting the
    Month label text (see module docstring)."""
    session = get_config_session()
    try:
        active_keys = _active_month_keys(session)
        invoices = [i for i in session.query(PaymentInvoice).all() if (i.year, i.month_number) in active_keys]
    finally:
        session.close()

    if not invoices:
        return []

    by_key: dict[tuple[int, int], list[PaymentInvoice]] = {}
    for invoice in invoices:
        by_key.setdefault((invoice.year, invoice.month_number), []).append(invoice)

    rows = []
    for key in sorted(by_key.keys()):
        group = by_key[key]
        average_payment_days = sum(i.payment_days for i in group) / len(group)
        label = _most_common([i.month for i in group]) or f"{key[0]}-{key[1]:02d}"
        rows.append({"month": label, "average_payment_days": average_payment_days, "invoice_count": len(group)})
    return rows


def get_active_window_labels() -> list[str]:
    """Chronological display labels of the current active six-month
    window, e.g. ["Feb-24", "Mar-24", "Apr-24", "May-24", "Jun-24",
    "Jul-24"] -- for UI captions/summaries."""
    session = get_config_session()
    try:
        rows = session.query(PaymentActiveMonth).order_by(
            PaymentActiveMonth.year, PaymentActiveMonth.month_number
        ).all()
        return [r.month_label for r in rows]
    finally:
        session.close()
