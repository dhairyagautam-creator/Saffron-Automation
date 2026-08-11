"""Cloud sync for Payment Analytics -- payment_invoices,
payment_active_months, payment_customer_profiles, outstanding_invoices.
Version 2.0, Payments cloud migration, Milestones 28-30, unified onto the
app-wide Last-Modified-Wins rule in Milestone 35/37 (see
app/sync_service.reconcile_rows()).

Every table now follows the exact same rule: compare each local row
against its cloud counterpart by a reliable "last modified" timestamp,
push whichever side is newer/new, pull whichever side is newer/new. There
is no more full-table delete_rows()-then-push_rows() special case for
payment_active_months/payment_customer_profiles/outstanding_invoices, and
no more explicit-confirmation cloud wipe for a Historical Report re-run --
all of that conflicted with having one consistent rule, so it was removed
(confirmed with the user before implementing).

Known, accepted trade-off of this simplification: this rule has no concept
of deletion. A record removed locally (an evicted month, a customer who
dropped out of the active window, a previous Outstanding Report's rows, or
every payment_invoices row from before a Historical Report reset) is not
deleted from the cloud -- it simply has no local counterpart on the next
reconcile, and will be PULLED BACK if a cloud copy still exists there. This
is acceptable under the stated assumption (only one person edits a given
module at a time) but is a real behavior change from the previous
delete-then-push design, which existed specifically to prevent this.
Genuinely deleting stale cloud rows still requires an explicit call to
app.sync_service.delete_rows() -- not something this module does
automatically anymore.

Payment Analytics has no Developer Mode concept of its own -- no
is_developer_mode() guards here, this data is always "the" data regardless
of mode.
"""

from datetime import datetime

from loguru import logger

from app.sync_service import push_rows, reconcile_rows
from database.connection import get_config_session
from database.models import OutstandingInvoice, PaymentActiveMonth, PaymentCustomerProfile, PaymentInvoice

PAYMENT_INVOICES_TABLE = "payment_invoices"
PAYMENT_ACTIVE_MONTHS_TABLE = "payment_active_months"
PAYMENT_CUSTOMER_PROFILES_TABLE = "payment_customer_profiles"
# The cloud column this table stores its "last modified" timestamp in --
# deliberately NOT "updated_at" (Supabase keeps its own column by that name
# here); see sync_customer_profiles() and Milestone 53.
_CUSTOMER_PROFILE_UPDATED_AT_COLUMN = "last_updated"
OUTSTANDING_INVOICES_TABLE = "outstanding_invoices"

_INVOICE_COLUMNS = (
    "month",
    "year",
    "month_number",
    "party_name",
    "customer_type",
    "invoice_no",
    "lr_date",
    "due_date",
    "clear_date",
    "payment_days",
    "created_at",
)
_ACTIVE_MONTH_COLUMNS = ("year", "month_number", "month_label", "added_at")
_CUSTOMER_PROFILE_COLUMNS = (
    "party_name",
    "customer_type",
    "total_invoices",
    "average_payment_days",
    "earliest_invoice",
    "latest_invoice",
    "risk_category",
)
_OUTSTANDING_COLUMNS = (
    "party_name",
    "team",
    "hq",
    "invoice_no",
    "lr_date",
    "due_date",
    "bill_amount",
    "month",
    "followed_up",
    "uploaded_at",
)


def _parse_ts(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _parse_date(raw):
    if not raw:
        return None
    from datetime import date

    if isinstance(raw, date):
        return raw
    return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()


def _serialize(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


# --- payment_invoices (keyed on cloud_id, immutable once created) ----------


def sync_invoices() -> bool:
    """Last-Modified-Wins reconciliation for payment_invoices, keyed by
    cloud_id, comparing each row's updated_at (immutable once created --
    see database/models.py's PaymentInvoice docstring). A local row
    without a cloud_id yet is generated one and pushed as new. The one
    function both the module-wide Refresh action and the immediate sync
    right after a Historical/Monthly upload call. Returns True if
    anything was pulled locally. No-op is not possible here (Payment
    Analytics has no Developer Mode)."""
    import uuid

    session = get_config_session()
    try:
        rows = session.query(PaymentInvoice).all()
        local_rows = []
        for row in rows:
            if not row.cloud_id:
                row.cloud_id = str(uuid.uuid4())
            item = {column: _serialize(getattr(row, column)) for column in _INVOICE_COLUMNS}
            item["cloud_id"] = row.cloud_id
            item["updated_at"] = row.updated_at
            local_rows.append(item)
        session.commit()
    finally:
        session.close()

    plan = reconcile_rows(PAYMENT_INVOICES_TABLE, local_rows, key_columns=["cloud_id"])
    if not plan.success:
        logger.warning(f"Sync: failed to reconcile payment invoices: {plan.error_message}")
        return False

    changed = False

    if plan.to_push:
        payload = [{k: (v if k != "updated_at" else _serialize(v)) for k, v in row.items()} for row in plan.to_push]
        result = push_rows(PAYMENT_INVOICES_TABLE, payload, on_conflict="cloud_id")
        if not result.success:
            logger.warning(f"Sync: failed to push {len(payload)} payment invoice(s): {result.error_message}")
        else:
            now = datetime.now()
            session = get_config_session()
            try:
                for row in payload:
                    invoice = session.query(PaymentInvoice).filter_by(cloud_id=row["cloud_id"]).first()
                    if invoice is not None:
                        invoice.synced_at = now
                session.commit()
            finally:
                session.close()
            logger.info(f"Sync: pushed {len(payload)} payment invoice(s)")

    if plan.to_pull:
        session = get_config_session()
        try:
            now = datetime.now()
            for cloud_row in plan.to_pull:
                cloud_id = cloud_row["cloud_id"]
                invoice = session.query(PaymentInvoice).filter_by(cloud_id=cloud_id).first()
                if invoice is None:
                    invoice = PaymentInvoice(cloud_id=cloud_id)
                    session.add(invoice)
                for column in _INVOICE_COLUMNS:
                    value = cloud_row.get(column)
                    if column in ("lr_date", "due_date", "clear_date"):
                        value = _parse_date(value)
                    elif column == "created_at":
                        value = _parse_ts(value)
                    setattr(invoice, column, value)
                invoice.updated_at = _parse_ts(cloud_row.get("updated_at"))
                invoice.synced_at = now
            session.commit()
        finally:
            session.close()
        logger.info(f"Sync: pulled {len(plan.to_pull)} payment invoice(s)")
        changed = True

    return changed


# --- payment_active_months (keyed on year+month_number, immutable) --------


def sync_active_months() -> bool:
    """Last-Modified-Wins reconciliation for payment_active_months, keyed
    by (year, month_number), comparing each row's added_at (set once when
    that month becomes active, never changes). Note: an evicted month has
    no local counterpart after eviction and, if its cloud row still
    exists, WILL be pulled back on the next reconcile -- deletion isn't
    part of this rule (see module docstring)."""
    session = get_config_session()
    try:
        rows = session.query(PaymentActiveMonth).all()
        local_rows = [
            {**{column: _serialize(getattr(row, column)) for column in _ACTIVE_MONTH_COLUMNS},
             "updated_at": row.added_at}
            for row in rows
        ]
    finally:
        session.close()

    plan = reconcile_rows(PAYMENT_ACTIVE_MONTHS_TABLE, local_rows, key_columns=["year", "month_number"])
    if not plan.success:
        logger.warning(f"Sync: failed to reconcile payment active months: {plan.error_message}")
        return False

    changed = False

    if plan.to_push:
        payload = [{k: v for k, v in row.items() if k != "updated_at"} for row in plan.to_push]
        result = push_rows(PAYMENT_ACTIVE_MONTHS_TABLE, payload, on_conflict="year,month_number")
        if not result.success:
            logger.warning(f"Sync: failed to push {len(payload)} payment active month(s): {result.error_message}")
        else:
            logger.info(f"Sync: pushed {len(payload)} payment active month(s)")

    if plan.to_pull:
        session = get_config_session()
        try:
            for cloud_row in plan.to_pull:
                existing = (
                    session.query(PaymentActiveMonth)
                    .filter_by(year=cloud_row["year"], month_number=cloud_row["month_number"])
                    .first()
                )
                if existing is None:
                    session.add(
                        PaymentActiveMonth(
                            year=cloud_row["year"],
                            month_number=cloud_row["month_number"],
                            month_label=cloud_row["month_label"],
                            added_at=_parse_ts(cloud_row.get("added_at")) or datetime.now(),
                        )
                    )
                else:
                    existing.month_label = cloud_row["month_label"]
                    existing.added_at = _parse_ts(cloud_row.get("added_at")) or existing.added_at
            session.commit()
        finally:
            session.close()
        logger.info(f"Sync: pulled {len(plan.to_pull)} payment active month(s)")
        changed = True

    return changed


# --- payment_customer_profiles (keyed on party_name) -----------------------


def sync_customer_profiles() -> bool:
    """Last-Modified-Wins reconciliation for payment_customer_profiles,
    keyed by party_name, comparing each row's last_updated (bumped every
    time this table is rebuilt -- see
    app/payment_analytics_service._replace_customer_profiles()). Note: a
    customer who drops out of the active window has no local counterpart
    after the next upload and, if their cloud row still exists, WILL be
    pulled back on the next reconcile -- deletion isn't part of this rule
    (see module docstring)."""
    session = get_config_session()
    try:
        rows = session.query(PaymentCustomerProfile).all()
        local_rows = []
        for row in rows:
            item = {column: _serialize(getattr(row, column)) for column in _CUSTOMER_PROFILE_COLUMNS}
            # Keyed under the cloud column's REAL name ("last_updated"), not
            # "updated_at" -- this table pushes its timestamp as last_updated
            # while Supabase maintains its own separate updated_at, so
            # comparing on "updated_at" compared our value against a
            # server-side "just now" stamp that always looked newer, making
            # every matched row pull and silently discarding local edits.
            # Same defect (and same fix) as app/inventory_sync_service.py --
            # see Milestone 53.
            item[_CUSTOMER_PROFILE_UPDATED_AT_COLUMN] = row.last_updated
            local_rows.append(item)
    finally:
        session.close()

    plan = reconcile_rows(
        PAYMENT_CUSTOMER_PROFILES_TABLE,
        local_rows,
        key_columns=["party_name"],
        updated_at_column=_CUSTOMER_PROFILE_UPDATED_AT_COLUMN,
    )
    if not plan.success:
        logger.warning(f"Sync: failed to reconcile payment customer profiles: {plan.error_message}")
        return False

    changed = False

    if plan.to_push:
        payload = []
        for row in plan.to_push:
            item = dict(row)
            item[_CUSTOMER_PROFILE_UPDATED_AT_COLUMN] = _serialize(row[_CUSTOMER_PROFILE_UPDATED_AT_COLUMN])
            payload.append(item)
        result = push_rows(PAYMENT_CUSTOMER_PROFILES_TABLE, payload, on_conflict="party_name")
        if not result.success:
            logger.warning(f"Sync: failed to push {len(payload)} payment customer profile(s): {result.error_message}")
        else:
            logger.info(f"Sync: pushed {len(payload)} payment customer profile(s)")

    if plan.to_pull:
        session = get_config_session()
        try:
            for cloud_row in plan.to_pull:
                existing = session.query(PaymentCustomerProfile).filter_by(party_name=cloud_row["party_name"]).first()
                if existing is None:
                    existing = PaymentCustomerProfile(party_name=cloud_row["party_name"])
                    session.add(existing)
                for column in _CUSTOMER_PROFILE_COLUMNS:
                    if column == "party_name":
                        continue
                    value = cloud_row.get(column)
                    if column in ("earliest_invoice", "latest_invoice"):
                        value = _parse_date(value)
                    setattr(existing, column, value)
                existing.last_updated = _parse_ts(cloud_row.get("last_updated")) or datetime.now()
            session.commit()
        finally:
            session.close()
        logger.info(f"Sync: pulled {len(plan.to_pull)} payment customer profile(s)")
        changed = True

    return changed


# --- outstanding_invoices (keyed on cloud_id) --------------------------------


def sync_outstanding_invoices() -> bool:
    """Last-Modified-Wins reconciliation for outstanding_invoices, keyed
    by cloud_id, comparing each row's updated_at (bumped both at creation
    and on every followed_up toggle -- see
    app/collections_service.py). The one function both the module-wide
    Refresh action, the immediate sync right after an Outstanding Report
    upload, and the follow-up toggle push all call. Note: a previous
    Outstanding Report's rows have no local counterpart after the next
    upload and, if their cloud rows still exist, WILL be pulled back on
    the next reconcile -- deletion isn't part of this rule (see module
    docstring)."""
    import uuid

    session = get_config_session()
    try:
        rows = session.query(OutstandingInvoice).all()
        local_rows = []
        for row in rows:
            if not row.cloud_id:
                row.cloud_id = str(uuid.uuid4())
            item = {column: _serialize(getattr(row, column)) for column in _OUTSTANDING_COLUMNS}
            item["cloud_id"] = row.cloud_id
            item["followed_up"] = bool(row.followed_up)
            item["updated_at"] = row.updated_at
            local_rows.append(item)
        session.commit()
    finally:
        session.close()

    plan = reconcile_rows(OUTSTANDING_INVOICES_TABLE, local_rows, key_columns=["cloud_id"])
    if not plan.success:
        logger.warning(f"Sync: failed to reconcile outstanding invoices: {plan.error_message}")
        return False

    changed = False

    if plan.to_push:
        payload = [{k: (v if k != "updated_at" else _serialize(v)) for k, v in row.items()} for row in plan.to_push]
        result = push_rows(OUTSTANDING_INVOICES_TABLE, payload, on_conflict="cloud_id")
        if not result.success:
            logger.warning(f"Sync: failed to push {len(payload)} outstanding invoice(s): {result.error_message}")
        else:
            now = datetime.now()
            session = get_config_session()
            try:
                for row in payload:
                    invoice = session.query(OutstandingInvoice).filter_by(cloud_id=row["cloud_id"]).first()
                    if invoice is not None:
                        invoice.synced_at = now
                session.commit()
            finally:
                session.close()
            logger.info(f"Sync: pushed {len(payload)} outstanding invoice(s)")

    if plan.to_pull:
        session = get_config_session()
        try:
            now = datetime.now()
            for cloud_row in plan.to_pull:
                cloud_id = cloud_row["cloud_id"]
                invoice = session.query(OutstandingInvoice).filter_by(cloud_id=cloud_id).first()
                if invoice is None:
                    invoice = OutstandingInvoice(cloud_id=cloud_id)
                    session.add(invoice)
                for column in _OUTSTANDING_COLUMNS:
                    value = cloud_row.get(column)
                    if column in ("lr_date", "due_date"):
                        value = _parse_date(value)
                    elif column == "uploaded_at":
                        value = _parse_ts(value) or now
                    elif column == "followed_up":
                        value = 1 if value else 0
                    setattr(invoice, column, value)
                invoice.updated_at = _parse_ts(cloud_row.get("updated_at")) or now
                invoice.synced_at = now
            session.commit()
        finally:
            session.close()
        logger.info(f"Sync: pulled {len(plan.to_pull)} outstanding invoice(s)")
        changed = True

    return changed


def push_outstanding_invoice_followup(invoice_id: int) -> bool:
    """Pushes a single outstanding invoice's current followed_up state
    immediately -- used right after the Collections Action Center's
    checkbox is toggled, so a click doesn't wait on a full reconcile. A
    direct, immediate push (not a full reconcile) is consistent with
    Last-Modified-Wins, not a deviation: the row was just edited locally,
    so local is unambiguously the newer copy at this instant (its
    updated_at was just bumped by app.collections_service.set_follow_up())."""
    import uuid

    session = get_config_session()
    try:
        row = session.query(OutstandingInvoice).filter_by(id=invoice_id).first()
        if row is None:
            return False
        if not row.cloud_id:
            row.cloud_id = str(uuid.uuid4())
        item = {column: _serialize(getattr(row, column)) for column in _OUTSTANDING_COLUMNS}
        item["cloud_id"] = row.cloud_id
        item["followed_up"] = bool(row.followed_up)
        item["updated_at"] = _serialize(row.updated_at)
        session.commit()
    finally:
        session.close()

    result = push_rows(OUTSTANDING_INVOICES_TABLE, [item], on_conflict="cloud_id")
    if not result.success:
        logger.warning(f"Sync: failed to push follow-up state for invoice_id={invoice_id}: {result.error_message}")
        return False

    session = get_config_session()
    try:
        row = session.query(OutstandingInvoice).filter_by(id=invoice_id).first()
        if row is not None:
            row.synced_at = datetime.now()
            session.commit()
    finally:
        session.close()

    logger.info(f"Sync: pushed follow-up state for invoice_id={invoice_id}")
    return True
