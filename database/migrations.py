"""One-time, idempotent schema fixes applied at startup.

Safe to run on every launch — each step checks whether it's already been
applied and does nothing if so.

Note: no cross-import row cleanup happens here. Under the active-session
model, each import is an independent historical snapshot (tagged by its own
import_id) that may be reloaded by a future feature — rows aren't merged or
deduplicated across different imports, only within a single import (see
database/import_service.py).
"""

from loguru import logger
from sqlalchemy import inspect, text

from database.connection import Base, get_config_engine

RAW_VISITS_TABLE = "raw_visits"
INVESTIGATION_FINDINGS_TABLE = "investigation_findings"
EMAIL_NOTIFICATIONS_TABLE = "email_notifications"
APP_SETTINGS_TABLE = "app_settings"
IMPORT_HISTORY_TABLE = "import_history"
ACTIVE_SESSION_TABLE = "active_session"
WORKBOOK_CONNECTIONS_TABLE = "workbook_connections"
PAYMENT_INVOICES_TABLE = "payment_invoices"
OUTSTANDING_INVOICES_TABLE = "outstanding_invoices"
CWH_STOCK_TABLE = "cwh_stock"
IMPORT_ID_COLUMN = "import_id"

# Kept in sync with app.notification_service.DEFAULT_MASTER_EMAIL — the
# value every existing installation was already hardcoded to send to,
# before the Settings page made it editable.
DEFAULT_MASTER_EMAIL = "gddesk@saffronformulations.com"


def _existing_columns(table_name: str) -> set:
    with get_config_engine().connect() as conn:
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})"))}


def ensure_raw_visits_import_id_column() -> None:
    """Add the import_id column to raw_visits if it predates this fix."""
    if not inspect(get_config_engine()).has_table(RAW_VISITS_TABLE):
        return
    if IMPORT_ID_COLUMN in _existing_columns(RAW_VISITS_TABLE):
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {RAW_VISITS_TABLE} ADD COLUMN {IMPORT_ID_COLUMN} INTEGER"))
    logger.info(f"Migration: added {IMPORT_ID_COLUMN} column to '{RAW_VISITS_TABLE}'")


def ensure_investigation_findings_import_id_column() -> None:
    """Add the import_id column to investigation_findings if it predates this fix."""
    if not inspect(get_config_engine()).has_table(INVESTIGATION_FINDINGS_TABLE):
        return
    if IMPORT_ID_COLUMN in _existing_columns(INVESTIGATION_FINDINGS_TABLE):
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {INVESTIGATION_FINDINGS_TABLE} ADD COLUMN {IMPORT_ID_COLUMN} INTEGER"))
    logger.info(f"Migration: added {IMPORT_ID_COLUMN} column to '{INVESTIGATION_FINDINGS_TABLE}'")


def ensure_email_notifications_error_message_column() -> None:
    """Add the error_message column to email_notifications if it predates
    real SMTP sending (and the Failed status)."""
    if not inspect(get_config_engine()).has_table(EMAIL_NOTIFICATIONS_TABLE):
        return
    if "error_message" in _existing_columns(EMAIL_NOTIFICATIONS_TABLE):
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {EMAIL_NOTIFICATIONS_TABLE} ADD COLUMN error_message TEXT"))
    logger.info(f"Migration: added error_message column to '{EMAIL_NOTIFICATIONS_TABLE}'")


def ensure_investigation_findings_concentration_columns() -> None:
    """Add the structured same-location stats columns to
    investigation_findings if they predate the HTML manager email (see
    app/email_template.py): concentration_percent, valid_visit_count,
    matched_visit_count, radius_meters, threshold_percent."""
    if not inspect(get_config_engine()).has_table(INVESTIGATION_FINDINGS_TABLE):
        return
    existing = _existing_columns(INVESTIGATION_FINDINGS_TABLE)
    new_columns = {
        "concentration_percent": "REAL",
        "valid_visit_count": "INTEGER",
        "matched_visit_count": "INTEGER",
        "radius_meters": "INTEGER",
        "threshold_percent": "REAL",
    }
    with get_config_engine().begin() as conn:
        for column_name, column_type in new_columns.items():
            if column_name not in existing:
                conn.execute(
                    text(f"ALTER TABLE {INVESTIGATION_FINDINGS_TABLE} ADD COLUMN {column_name} {column_type}")
                )
                logger.info(f"Migration: added {column_name} column to '{INVESTIGATION_FINDINGS_TABLE}'")


def migrate_email_settings_to_app_settings() -> None:
    """One-time move from the old `email_settings` table to `app_settings`
    (renamed for clarity/consistency with the requested schema). Copies the
    single settings row over if present, then drops the old table. Safe to
    run multiple times — a no-op once the old table is gone."""
    if not inspect(get_config_engine()).has_table("email_settings"):
        return

    with get_config_engine().begin() as conn:
        old_row = conn.execute(text("SELECT sender_email, app_password, automatic_sending_enabled FROM email_settings WHERE id = 1")).first()
        if old_row is not None and inspect(get_config_engine()).has_table("app_settings"):
            conn.execute(
                text(
                    "INSERT OR REPLACE INTO app_settings "
                    "(id, sender_gmail_address, gmail_app_password, automatic_email_enabled) "
                    "VALUES (1, :sender, :password, :enabled)"
                ),
                {"sender": old_row[0], "password": old_row[1], "enabled": old_row[2]},
            )
        conn.execute(text("DROP TABLE email_settings"))
    logger.info("Migration: moved email_settings -> app_settings")


def ensure_investigation_findings_hospital_suppression_columns() -> None:
    """Add the Hospital Suppression columns to investigation_findings if
    they predate that feature (see app/hospital_service.py):
    cluster_lat, cluster_lon, notification_status, suppression_reason."""
    if not inspect(get_config_engine()).has_table(INVESTIGATION_FINDINGS_TABLE):
        return
    existing = _existing_columns(INVESTIGATION_FINDINGS_TABLE)
    new_columns = {
        "cluster_lat": "REAL",
        "cluster_lon": "REAL",
        "notification_status": "TEXT",
        "suppression_reason": "TEXT",
    }
    with get_config_engine().begin() as conn:
        for column_name, column_type in new_columns.items():
            if column_name not in existing:
                conn.execute(
                    text(f"ALTER TABLE {INVESTIGATION_FINDINGS_TABLE} ADD COLUMN {column_name} {column_type}")
                )
                logger.info(f"Migration: added {column_name} column to '{INVESTIGATION_FINDINGS_TABLE}'")


def ensure_investigation_findings_hospital_detail_columns() -> None:
    """Add the structured hospital-detail columns to investigation_findings
    if they predate the Findings page's suppression detail panel:
    hospital_name, hospital_lat, hospital_lon, hospital_distance_meters."""
    if not inspect(get_config_engine()).has_table(INVESTIGATION_FINDINGS_TABLE):
        return
    existing = _existing_columns(INVESTIGATION_FINDINGS_TABLE)
    new_columns = {
        "hospital_name": "TEXT",
        "hospital_lat": "REAL",
        "hospital_lon": "REAL",
        "hospital_distance_meters": "INTEGER",
    }
    with get_config_engine().begin() as conn:
        for column_name, column_type in new_columns.items():
            if column_name not in existing:
                conn.execute(
                    text(f"ALTER TABLE {INVESTIGATION_FINDINGS_TABLE} ADD COLUMN {column_name} {column_type}")
                )
                logger.info(f"Migration: added {column_name} column to '{INVESTIGATION_FINDINGS_TABLE}'")


def ensure_hospital_lookup_cache_coordinate_columns() -> None:
    """Add hospital_lat/hospital_lon to hospital_lookup_cache if they
    predate the Findings page's suppression detail panel (which needs the
    facility's own coordinates, not just its name/distance)."""
    if not inspect(get_config_engine()).has_table("hospital_lookup_cache"):
        return
    existing = _existing_columns("hospital_lookup_cache")
    with get_config_engine().begin() as conn:
        for column_name in ("hospital_lat", "hospital_lon"):
            if column_name not in existing:
                conn.execute(text(f"ALTER TABLE hospital_lookup_cache ADD COLUMN {column_name} REAL"))
                logger.info(f"Migration: added {column_name} column to 'hospital_lookup_cache'")


def ensure_app_settings_master_email_column() -> None:
    """Add the master_email_address column to app_settings if it predates
    the Settings page making the master-report recipient editable. Existing
    rows are backfilled with the address every installation was already
    hardcoded to use, so behavior doesn't silently change on upgrade."""
    if not inspect(get_config_engine()).has_table(APP_SETTINGS_TABLE):
        return
    if "master_email_address" in _existing_columns(APP_SETTINGS_TABLE):
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {APP_SETTINGS_TABLE} ADD COLUMN master_email_address TEXT"))
        conn.execute(
            text(f"UPDATE {APP_SETTINGS_TABLE} SET master_email_address = :default WHERE master_email_address IS NULL"),
            {"default": DEFAULT_MASTER_EMAIL},
        )
    logger.info(f"Migration: added master_email_address column to '{APP_SETTINGS_TABLE}' (default {DEFAULT_MASTER_EMAIL})")


def ensure_app_settings_setup_completed_column() -> None:
    """Add the setup_completed column to app_settings if it predates the
    first-run Setup Wizard (see ui/setup_wizard.py). An installation that
    already has a sender address configured has clearly already been set
    up by hand before this feature existed — backfilled to 1 so upgrading
    never makes the wizard pop up for someone who's already configured and
    using the app. A genuinely fresh row (or one with no sender configured
    yet) defaults to 0, so the wizard runs on next launch as intended."""
    if not inspect(get_config_engine()).has_table(APP_SETTINGS_TABLE):
        return
    if "setup_completed" in _existing_columns(APP_SETTINGS_TABLE):
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {APP_SETTINGS_TABLE} ADD COLUMN setup_completed INTEGER NOT NULL DEFAULT 0"))
        conn.execute(
            text(
                f"UPDATE {APP_SETTINGS_TABLE} SET setup_completed = 1 "
                "WHERE sender_gmail_address IS NOT NULL AND sender_gmail_address != ''"
            )
        )
    logger.info(f"Migration: added setup_completed column to '{APP_SETTINGS_TABLE}' (backfilled for already-configured installs)")


def ensure_app_settings_inventory_reset_column() -> None:
    """Add the inventory_data_reset_completed column to app_settings if it
    predates the one-time company-wide Inventory factory reset (see
    app/inventory_factory_reset.py). Always backfilled to 0 (never 1) --
    unlike setup_completed's own migration, there is no "already
    effectively done" signal to infer here; every existing installation
    genuinely still needs the reset to run once."""
    if not inspect(get_config_engine()).has_table(APP_SETTINGS_TABLE):
        return
    if "inventory_data_reset_completed" in _existing_columns(APP_SETTINGS_TABLE):
        return
    with get_config_engine().begin() as conn:
        conn.execute(
            text(f"ALTER TABLE {APP_SETTINGS_TABLE} ADD COLUMN inventory_data_reset_completed INTEGER NOT NULL DEFAULT 0")
        )
    logger.info(f"Migration: added inventory_data_reset_completed column to '{APP_SETTINGS_TABLE}'")


def ensure_app_settings_geoapify_key_column() -> None:
    """Add the geoapify_api_key column to app_settings if it predates
    Hospital Suppression's migration off the free OpenStreetMap Overpass
    API (see app/hospital_service.py, app/geoapify_settings_service.py)."""
    if not inspect(get_config_engine()).has_table(APP_SETTINGS_TABLE):
        return
    if "geoapify_api_key" in _existing_columns(APP_SETTINGS_TABLE):
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {APP_SETTINGS_TABLE} ADD COLUMN geoapify_api_key TEXT"))
    logger.info(f"Migration: added geoapify_api_key column to '{APP_SETTINGS_TABLE}'")


def drop_developer_mode_schema() -> None:
    """Collapse the per-environment Developer Mode schema back to one
    environment: keep only the 'user' rows, drop `environment` and the
    dev-password columns, and drop feature_flags outright.

    Developer Mode is gone, so a 'developer' row is unreachable data and the
    columns scoping it are dead. Rebuilt rather than ALTER ... DROP COLUMN
    because both tables carry a UNIQUE constraint over `environment`, which
    SQLite refuses to drop a column out of.

    The old table is RENAMED, not dropped, until the copy has succeeded:
    pysqlite autocommits DDL, so a DROP would be irreversible the moment it
    ran even though the surrounding transaction later failed. Rows go back
    through the ORM's own insert so Python-side column defaults are applied
    for any NOT NULL column the old table didn't have yet."""
    from database.models import AppSettings, RuleParameter

    engine = get_config_engine()

    for model, table in ((AppSettings, APP_SETTINGS_TABLE), (RuleParameter, "rule_parameters")):
        old_table = f"{table}_pre_devmode_removal"
        # A leftover old table means a previous attempt died between the
        # rename and the copy; finish from it rather than stranding the rows.
        resuming = inspect(engine).has_table(old_table)
        if not resuming:
            if not inspect(engine).has_table(table):
                continue
            if "environment" not in _existing_columns(table):
                continue  # already collapsed

        keep = [c.name for c in model.__table__.columns if c.name in _existing_columns(old_table if resuming else table)]
        # NOT NULL columns the old table never had (their defaults are
        # Python-side, so a plain INSERT ... SELECT would violate NOT NULL).
        defaults = {}
        for column in model.__table__.columns:
            if column.name in keep or column.nullable:
                continue
            value = getattr(column.default, "arg", None)
            if value is not None and not callable(value):
                defaults[column.name] = value

        columns = keep + list(defaults)
        selected = keep + [f":{name}__default" for name in defaults]
        with engine.begin() as conn:
            if resuming:
                conn.execute(text(f"DELETE FROM {table}"))  # discard the partial copy
            else:
                conn.execute(text(f"ALTER TABLE {table} RENAME TO {old_table}"))
                model.__table__.create(bind=conn)
            copied = conn.execute(
                text(
                    f"INSERT INTO {table} ({', '.join(columns)}) "
                    f"SELECT {', '.join(selected)} FROM {old_table} WHERE environment = 'user'"
                ),
                {f"{name}__default": value for name, value in defaults.items()},
            ).rowcount
            conn.execute(text(f"DROP TABLE {old_table}"))
        logger.info(
            f"Migration: collapsed '{table}' to a single environment "
            f"({copied} 'user' row(s) kept, 'developer' row(s) discarded)"
        )

    if inspect(engine).has_table("feature_flags"):
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE feature_flags"))
        logger.info("Migration: dropped 'feature_flags' (Developer Mode removed)")


def ensure_payment_invoices_month_key_columns() -> None:
    """Add year/month_number to payment_invoices if it predates the
    rolling six-month window (see app/payment_analytics_service.py) --
    backfilled from each row's own lr_date, which was always required and
    always valid, so every existing row gets a correct key with no data
    loss."""
    table = "payment_invoices"
    if not inspect(get_config_engine()).has_table(table):
        return
    existing = _existing_columns(table)
    if "year" in existing and "month_number" in existing:
        return
    with get_config_engine().begin() as conn:
        if "year" not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN year INTEGER"))
        if "month_number" not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN month_number INTEGER"))
        conn.execute(
            text(
                f"UPDATE {table} SET year = CAST(strftime('%Y', lr_date) AS INTEGER), "
                "month_number = CAST(strftime('%m', lr_date) AS INTEGER) WHERE year IS NULL"
            )
        )
    logger.info(f"Migration: added year/month_number columns to '{table}' (backfilled from lr_date)")


def ensure_outstanding_invoices_bill_amount_month_columns() -> None:
    """Add bill_amount/month to outstanding_invoices if it predates the
    real production Outstanding Report column names (see
    app/collections_service.py, app/excel_validation.py). Existing rows
    simply get NULL for both -- they'll be replaced wholesale by the next
    Outstanding Report upload anyway (see process_outstanding_report())."""
    table = "outstanding_invoices"
    if not inspect(get_config_engine()).has_table(table):
        return
    existing = _existing_columns(table)
    if "bill_amount" in existing and "month" in existing:
        return
    with get_config_engine().begin() as conn:
        if "bill_amount" not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN bill_amount REAL"))
        if "month" not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN month TEXT"))
    logger.info(f"Migration: added bill_amount/month columns to '{table}'")


def ensure_investigation_findings_division_column() -> None:
    """Add the division column to investigation_findings if it predates the
    Findings page's Division display (see rules/same_location.py, which
    populates it from the imported call-report's own "Division" column).
    Existing rows simply get NULL -- they're re-generated the next time the
    rule engine runs for their import_id anyway."""
    if not inspect(get_config_engine()).has_table(INVESTIGATION_FINDINGS_TABLE):
        return
    if "division" in _existing_columns(INVESTIGATION_FINDINGS_TABLE):
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {INVESTIGATION_FINDINGS_TABLE} ADD COLUMN division TEXT"))
    logger.info(f"Migration: added division column to '{INVESTIGATION_FINDINGS_TABLE}'")


def ensure_inventory_new_sales_format_schema() -> None:
    """Rebuild inventory_thresholds and inventory_replenishment if either
    table predates the current compact Monthly Sales Report format
    (Division, CFA, Item Name, Packing, Sales -- see
    app/excel_validation.py's SALES_REPORT_REQUIRED_COLUMNS and
    app/threshold_service.py), OR predates branch_key/item_key -- the
    normalized (case/whitespace-insensitive) matching columns that fix
    the Inventory Dashboard's "0 Products Evaluated" root cause (see
    those models' docstrings in database/models.py: exact-string matching
    between two independently-typed uploaded files silently drops every
    row whose CFA/item name differs even trivially in case or spacing).

    Both tables are always fully regenerated from the next Sales/Inventory
    Report upload (never a source of truth on their own), so dropping and
    recreating loses nothing that isn't reproduced by re-uploading. There
    is no way to reconcile old item_code-keyed or unnormalized rows with
    the new schema automatically anyway."""
    engine = get_config_engine()
    if not inspect(engine).has_table("inventory_thresholds"):
        return
    existing = _existing_columns("inventory_thresholds")
    if "item_code" not in existing and "branch_key" in existing:
        return  # already on the current schema
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS inventory_thresholds"))
        conn.execute(text("DROP TABLE IF EXISTS inventory_replenishment"))
    from database import models  # noqa: F401  (registers both tables on Base.metadata)

    Base.metadata.create_all(
        bind=engine,
        tables=[Base.metadata.tables["inventory_thresholds"], Base.metadata.tables["inventory_replenishment"]],
    )
    logger.info(
        "Migration: rebuilt inventory_thresholds/inventory_replenishment for the new Monthly Sales "
        "Report format and normalized branch/item matching (old data cleared -- re-upload the "
        "Previous Month Sales Report and Inventory Report)"
    )


def drop_obsolete_employee_emails_table() -> None:
    """Drop the old `employee_emails` table — Organization Data workbooks
    now embed each employee's email directly (see app/hierarchy_parser.py),
    so the separate email directory this table backed no longer exists.
    Safe to run multiple times — a no-op once the table is gone."""
    if not inspect(get_config_engine()).has_table("employee_emails"):
        return
    with get_config_engine().begin() as conn:
        conn.execute(text("DROP TABLE employee_emails"))
    logger.info("Migration: dropped obsolete 'employee_emails' table")


def ensure_investigation_findings_updated_at_column() -> None:
    """Add updated_at to investigation_findings if it predates this fix,
    backfilled from each row's own created_at so pre-existing findings get
    a sane initial value rather than NULL. Bumped on every notification-status
    change (app/findings_service.py's set_notification_status())."""
    if not inspect(get_config_engine()).has_table(INVESTIGATION_FINDINGS_TABLE):
        return
    existing = _existing_columns(INVESTIGATION_FINDINGS_TABLE)
    if "updated_at" in existing:
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {INVESTIGATION_FINDINGS_TABLE} ADD COLUMN updated_at DATETIME"))
        conn.execute(
            text(f"UPDATE {INVESTIGATION_FINDINGS_TABLE} SET updated_at = created_at WHERE updated_at IS NULL")
        )
    logger.info(
        f"Migration: added updated_at column to '{INVESTIGATION_FINDINGS_TABLE}' (backfilled from created_at)"
    )


def ensure_email_notifications_updated_at_column() -> None:
    """Add updated_at to email_notifications if it predates this fix --
    same updated_at-backfilled-from-created_at treatment as
    investigation_findings, for the same reason."""
    if not inspect(get_config_engine()).has_table(EMAIL_NOTIFICATIONS_TABLE):
        return
    existing = _existing_columns(EMAIL_NOTIFICATIONS_TABLE)
    if "updated_at" in existing:
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {EMAIL_NOTIFICATIONS_TABLE} ADD COLUMN updated_at DATETIME"))
        conn.execute(
            text(f"UPDATE {EMAIL_NOTIFICATIONS_TABLE} SET updated_at = created_at WHERE updated_at IS NULL")
        )
    logger.info(
        f"Migration: added updated_at column to '{EMAIL_NOTIFICATIONS_TABLE}' (backfilled from created_at)"
    )


def drop_investigation_findings_status_column() -> None:
    """Drop the human-writable review `status` (Open/Reviewed/Ignored)
    column -- the marking system is removed; a finding's only outcome now is
    its automatic `notification_status`. No UNIQUE constraint touches this
    column, so a plain DROP COLUMN (supported since SQLite 3.35) is safe,
    unlike the rename-dance drop_developer_mode_schema() needed above."""
    if not inspect(get_config_engine()).has_table(INVESTIGATION_FINDINGS_TABLE):
        return
    if "status" not in _existing_columns(INVESTIGATION_FINDINGS_TABLE):
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {INVESTIGATION_FINDINGS_TABLE} DROP COLUMN status"))
    logger.info(f"Migration: dropped 'status' column from '{INVESTIGATION_FINDINGS_TABLE}' (review marking removed)")


def ensure_workbook_connections_updated_at_column() -> None:
    """Add updated_at to workbook_connections if it predates this fix --
    the genuine local "last modified" timestamp, bumped in
    app/workbook_connections.set_connection() every time a workbook is
    (re)connected. Backfilled to now() so an existing install doesn't
    start with a NULL."""
    if not inspect(get_config_engine()).has_table(WORKBOOK_CONNECTIONS_TABLE):
        return
    existing = _existing_columns(WORKBOOK_CONNECTIONS_TABLE)
    if "updated_at" in existing:
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {WORKBOOK_CONNECTIONS_TABLE} ADD COLUMN updated_at DATETIME"))
        conn.execute(
            text(f"UPDATE {WORKBOOK_CONNECTIONS_TABLE} SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")
        )
    logger.info(f"Migration: added updated_at column to '{WORKBOOK_CONNECTIONS_TABLE}'")


def ensure_payment_invoices_updated_at_column() -> None:
    """Add updated_at to payment_invoices if it predates this fix,
    backfilled from each row's own created_at -- these rows are immutable
    once inserted (only ever created via a monthly append, or wiped
    wholesale by a Historical Report re-run; individual rows are never
    edited in place), so updated_at never needs bumping again after this
    one-time backfill."""
    if not inspect(get_config_engine()).has_table(PAYMENT_INVOICES_TABLE):
        return
    existing = _existing_columns(PAYMENT_INVOICES_TABLE)
    if "updated_at" in existing:
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {PAYMENT_INVOICES_TABLE} ADD COLUMN updated_at DATETIME"))
        conn.execute(
            text(f"UPDATE {PAYMENT_INVOICES_TABLE} SET updated_at = created_at WHERE updated_at IS NULL")
        )
    logger.info(
        f"Migration: added updated_at column to '{PAYMENT_INVOICES_TABLE}' (backfilled from created_at)"
    )


def ensure_payment_invoices_invoice_no_not_null_and_unique_index() -> None:
    """Make invoice_no NOT NULL and add UNIQUE(party_name, invoice_no, month)
    -- retry-safety against re-uploading the same file (see PaymentInvoice's
    own docstring for why this key, not invoice_no alone or a tighter one).

    invoice_no must be NOT NULL before the unique index means anything:
    SQLite never treats two NULLs as equal in a UNIQUE constraint, so a row
    with a NULL invoice_no would sail through the constraint undetected.
    app.payment_analytics_service._parse_invoice_rows now rejects a missing
    Invoice Number at validation time, but that only stops NEW rows -- this
    migration must also confirm no EXISTING row already has a NULL before
    it can safely enforce NOT NULL. If one exists, this refuses to guess
    what to do with it (drop it? blank it in?) and leaves the column
    nullable and the index un-added, logging loudly instead -- a decorative
    constraint is a known, visible gap; guessing at real financial data is
    not this migration's call to make.

    SQLite has no ALTER TABLE to change a column's nullability in place, so
    (like drop_developer_mode_schema() above) the table is rebuilt: rename,
    recreate from the model's current shape, copy, drop -- with the same
    resume-from-interrupted safety, since pysqlite autocommits DDL and a
    DROP TABLE is irreversible the instant it runs even if a later step in
    the same transaction fails."""
    from database.models import PaymentInvoice

    table = PAYMENT_INVOICES_TABLE
    old_table = f"{table}_pre_notnull_migration"
    engine = get_config_engine()

    resuming = inspect(engine).has_table(old_table)
    if not resuming:
        if not inspect(engine).has_table(table):
            return
        # invoice_no's nullability and the unique constraint are always
        # applied together in the one rebuild below -- nothing else in this
        # codebase ever changes this column's nullability -- so checking
        # nullability alone is sufficient (and more reliable than looking
        # for the constraint as a named index: SQLite stores a UNIQUE
        # table constraint as an unnamed sqlite_autoindex_*, not under the
        # name given in __table_args__, even though that name IS preserved
        # in the table's own CREATE TABLE text).
        already_not_null = any(
            col["name"] == "invoice_no" and col["nullable"] is False
            for col in inspect(engine).get_columns(table)
        )
        if already_not_null:
            return

    source_table = old_table if resuming else table
    with engine.connect() as conn:
        null_count = conn.execute(
            text(f"SELECT COUNT(*) FROM {source_table} WHERE invoice_no IS NULL")
        ).scalar()
    if null_count:
        logger.error(
            f"Migration SKIPPED: {null_count} row(s) in '{source_table}' have a NULL invoice_no. "
            "Refusing to guess (drop vs. backfill) -- invoice_no stays nullable and the "
            "UNIQUE(party_name, invoice_no, month) index is NOT added until this is resolved by hand."
        )
        return

    keep = [c.name for c in PaymentInvoice.__table__.columns]
    with engine.begin() as conn:
        if not resuming:
            conn.execute(text(f"ALTER TABLE {table} RENAME TO {old_table}"))
        else:
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))  # discard any partial rebuild
        PaymentInvoice.__table__.create(bind=conn)
        conn.execute(
            text(f"INSERT INTO {table} ({', '.join(keep)}) SELECT {', '.join(keep)} FROM {old_table}")
        )
        copied = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        conn.execute(text(f"DROP TABLE {old_table}"))
    logger.info(
        f"Migration: '{table}' rebuilt with invoice_no NOT NULL and "
        f"UNIQUE(party_name, invoice_no, month) ({copied} row(s) preserved)"
    )


def ensure_cwh_stock_threshold_columns() -> None:
    """Add total_previous_month_sales/cwh_threshold/surplus_deficit/status
    to cwh_stock (see database/models.py's CwhStock docstring and
    app/cwh_service.py) -- these were added after the table's initial
    Phase 2 release (which only stored closing_stock/transit_stock), so
    an existing cwh_stock table from that earlier version needs them
    backfilled. Existing rows (if any) get 0.0/"Healthy" defaults; they
    are fully recomputed anyway the next time an Inventory Report is
    processed (app/cwh_service.evaluate_cwh_stock() re-evaluates every
    known item on every run), so this backfill only needs to leave the
    schema valid, not the values retroactively correct."""
    if not inspect(get_config_engine()).has_table(CWH_STOCK_TABLE):
        return
    existing = _existing_columns(CWH_STOCK_TABLE)
    added = False
    with get_config_engine().begin() as conn:
        if "total_previous_month_sales" not in existing:
            conn.execute(text(f"ALTER TABLE {CWH_STOCK_TABLE} ADD COLUMN total_previous_month_sales FLOAT"))
            conn.execute(
                text(f"UPDATE {CWH_STOCK_TABLE} SET total_previous_month_sales = 0.0 "
                     "WHERE total_previous_month_sales IS NULL")
            )
            added = True
        if "cwh_threshold" not in existing:
            conn.execute(text(f"ALTER TABLE {CWH_STOCK_TABLE} ADD COLUMN cwh_threshold FLOAT"))
            conn.execute(text(f"UPDATE {CWH_STOCK_TABLE} SET cwh_threshold = 0.0 WHERE cwh_threshold IS NULL"))
            added = True
        if "surplus_deficit" not in existing:
            conn.execute(text(f"ALTER TABLE {CWH_STOCK_TABLE} ADD COLUMN surplus_deficit FLOAT"))
            conn.execute(
                text(f"UPDATE {CWH_STOCK_TABLE} SET surplus_deficit = 0.0 WHERE surplus_deficit IS NULL")
            )
            added = True
        if "status" not in existing:
            conn.execute(text(f"ALTER TABLE {CWH_STOCK_TABLE} ADD COLUMN status TEXT"))
            conn.execute(text(f"UPDATE {CWH_STOCK_TABLE} SET status = 'Healthy' WHERE status IS NULL"))
            added = True
    if added:
        logger.info(
            f"Migration: added total_previous_month_sales/cwh_threshold/surplus_deficit/status "
            f"columns to '{CWH_STOCK_TABLE}'"
        )


def ensure_manager_work_allocation_records_optional_columns() -> None:
    """Add the optional Manager Work Allocation columns (Rep HQ, Zone,
    Region, Team Emp HQ, Total Visits Done in Joint, Dates Spent in Joint,
    General, B-RGD, Total Dr., Covered Dr., General Covered, B-RGD
    Covered) to manager_work_allocation_records if it predates the ABM
    upload-parsing alias fix (app/manager_work_allocation_parser.py) that
    added them to the model. An install that already ran the ABM engine
    once before this fix has the table WITHOUT these columns -- every
    subsequent insert then fails with "table manager_work_allocation_records
    has no column named rep_hq" (etc.) at session.commit() time, which is
    the confirmed root cause of Run Analysis appearing to hang partway
    through: the real OperationalError was masked by a separate bug in
    ui/background_task.py (see that module's own fix) and surfaced only as
    a generic, confusing "Unexpected Error" dialog with the progress
    overlay left stuck on screen. Existing rows (if any) get NULL/blank for
    all of these -- the table is fully deleted+rebuilt on every upload
    anyway (see ManagerWorkAllocationRecord's own docstring), so no data is
    actually lost by the backfill being blank."""
    table = "manager_work_allocation_records"
    if not inspect(get_config_engine()).has_table(table):
        return
    existing = _existing_columns(table)
    new_columns = {
        "rep_hq": "TEXT",
        "zone": "TEXT",
        "region": "TEXT",
        "team_emp_hq": "TEXT",
        "total_visits_done_in_joint": "INTEGER",
        "dates_spent_in_joint": "TEXT",
        "general": "TEXT",
        "b_rgd": "TEXT",
        "total_dr": "TEXT",
        "covered_dr": "TEXT",
        "general_covered": "TEXT",
        "b_rgd_covered": "TEXT",
    }
    added = False
    with get_config_engine().begin() as conn:
        for column_name, column_type in new_columns.items():
            if column_name not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}"))
                added = True
    if added:
        logger.info(f"Migration: added Rep HQ/Zone/Region/... optional columns to '{table}'")


def ensure_manager_work_allocation_records_source_engine_column() -> None:
    """Add source_engine to manager_work_allocation_records if it predates
    the RBM engine (see app/manager_work_allocation_rbm_service.py) --
    ABM and RBM upload separate files and each scope their own
    delete-then-rebuild to their own source_engine value, so one engine's
    Run Analysis can never wipe the other's raw storage. Existing rows (all
    from the ABM engine, the only one that existed before this column)
    are backfilled as 'ABM' so they aren't silently orphaned by a filter
    that expects this column to already be populated."""
    table = "manager_work_allocation_records"
    if not inspect(get_config_engine()).has_table(table):
        return
    if "source_engine" in _existing_columns(table):
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN source_engine TEXT"))
        conn.execute(text(f"UPDATE {table} SET source_engine = 'ABM' WHERE source_engine IS NULL"))
    logger.info(f"Migration: added source_engine column to '{table}' (existing rows backfilled as 'ABM')")


def ensure_manager_work_allocation_findings_rbm_columns() -> None:
    """Add coverage_percent/reason to manager_work_allocation_findings if
    they predate the RBM engine -- see ManagerWorkAllocationFinding's own
    docstring for why these are shared, nullable columns rather than a
    duplicate RBM-only table. Existing (ABM) rows get NULL for both,
    matching what ABM's own INSERT has always left them as."""
    table = "manager_work_allocation_findings"
    if not inspect(get_config_engine()).has_table(table):
        return
    existing = _existing_columns(table)
    added = False
    with get_config_engine().begin() as conn:
        if "coverage_percent" not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN coverage_percent REAL"))
            added = True
        if "reason" not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN reason TEXT"))
            added = True
    if added:
        logger.info(f"Migration: added coverage_percent/reason columns to '{table}'")


def ensure_manager_work_allocation_bm_details_reason_column() -> None:
    """Add reason to manager_work_allocation_bm_details if it predates the
    RBM engine -- see ManagerWorkAllocationBMDetail's own docstring.
    Existing (ABM) rows get NULL, matching what ABM's own INSERT has
    always left this as."""
    table = "manager_work_allocation_bm_details"
    if not inspect(get_config_engine()).has_table(table):
        return
    if "reason" in _existing_columns(table):
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN reason TEXT"))
    logger.info(f"Migration: added reason column to '{table}'")


def ensure_manager_work_allocation_records_month_sort_key_column() -> None:
    """Add month_sort_key to manager_work_allocation_records if it predates
    the rolling six-month history redesign (2026-08-05) -- see that
    model's own docstring: this table's own MEANING changed (from "a
    mirror of the last upload" to "accumulated monthly history keyed by
    month"), so pre-existing rows are not just missing a column, they're
    the wrong SHAPE of data for the new architecture entirely (one row per
    raw uploaded line, not one row per (pair, month)). Clearing them here
    -- the same "safe to wipe, the next upload regenerates it" reasoning
    already used by this file's own ensure_inventory_new_sales_format_schema
    -- rather than leaving them stranded with a NULL month_sort_key (which
    would otherwise need excluding from every future rolling-window query
    by hand). The very next Run Analysis for either engine rebuilds this
    table from scratch via the normal upsert flow regardless."""
    table = "manager_work_allocation_records"
    if not inspect(get_config_engine()).has_table(table):
        return
    if "month_sort_key" in _existing_columns(table):
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN month_sort_key INTEGER"))
        deleted = conn.execute(text(f"DELETE FROM {table}")).rowcount
    logger.info(
        f"Migration: added month_sort_key column to '{table}' and cleared {deleted} pre-redesign row(s) "
        "(rolling six-month history redesign -- re-upload to repopulate)"
    )


def ensure_manager_work_allocation_records_pair_month_unique_index() -> None:
    """Add the UNIQUE(source_engine, emp_code, team_emp_code, month) index
    to manager_work_allocation_records if it predates this fix -- see
    ManagerWorkAllocationRecord's own docstring: this IS the table's
    documented identity already (one row per pair per month), enforced
    only in application logic (app.manager_work_allocation_shared.
    sync_rolling_window's upsert) until now, never at the database level.
    SQLite has no ALTER TABLE ADD CONSTRAINT; a UNIQUE index enforces the
    identical guarantee without a table rebuild."""
    table = "manager_work_allocation_records"
    index_name = "uq_manager_work_allocation_records_pair_month"
    if not inspect(get_config_engine()).has_table(table):
        return
    existing_indexes = {ix["name"] for ix in inspect(get_config_engine()).get_indexes(table)}
    if index_name in existing_indexes:
        return
    with get_config_engine().begin() as conn:
        conn.execute(
            text(
                f"CREATE UNIQUE INDEX {index_name} ON {table} "
                "(source_engine, emp_code, team_emp_code, month)"
            )
        )
    logger.info(f"Migration: added UNIQUE(source_engine, emp_code, team_emp_code, month) index to '{table}'")


def ensure_review_file_slots_sync_columns() -> None:
    """Add uploaded_by/only_on_this_machine to review_file_slots for the
    Review System sync slice (see database/models.py's ReviewFileSlot
    docstring, docs/SYNC_DESIGN.md). Every existing row predates the
    manifest entirely, so a slot that already has a file is, correctly,
    "only on this machine" until it's re-uploaded through the normal path;
    a slot with no file has nothing to flag."""
    table = "review_file_slots"
    if not inspect(get_config_engine()).has_table(table):
        return
    existing = _existing_columns(table)
    added = False
    with get_config_engine().begin() as conn:
        if "uploaded_by" not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN uploaded_by TEXT"))
            added = True
        if "only_on_this_machine" not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN only_on_this_machine INTEGER NOT NULL DEFAULT 0"))
            conn.execute(text(f"UPDATE {table} SET only_on_this_machine = 1 WHERE file_path IS NOT NULL"))
            added = True
    if added:
        logger.info(f"Migration: added uploaded_by/only_on_this_machine columns to '{table}'")


def ensure_review_system_sync_tables() -> None:
    """Create sync_state/sync_module_check/profile_name_cache -- the
    Review System sync slice's own local-only bookkeeping tables (see
    database/models.py, docs/SYNC_DESIGN.md). init_db()'s
    Base.metadata.create_all() already creates these on a brand-new
    install; this covers every existing install that ran init_db() before
    these models existed."""
    from database.models import ProfileNameCache, SyncModuleCheck, SyncState

    engine = get_config_engine()
    for model in (SyncState, SyncModuleCheck, ProfileNameCache):
        if not inspect(engine).has_table(model.__tablename__):
            model.__table__.create(bind=engine)
            logger.info(f"Migration: created '{model.__tablename__}' table")


def ensure_module_data_version_table() -> None:
    """Create module_data_version -- Phase 2 of email authority's local
    per-module counter (see database/models.py, docs/EMAIL_AUTHORITY_PHASE2_CONTEXT.md).
    init_db()'s Base.metadata.create_all() already creates this on a
    brand-new install; this covers every existing install that ran
    init_db() before this model existed."""
    from database.models import ModuleDataVersion

    engine = get_config_engine()
    if not inspect(engine).has_table(ModuleDataVersion.__tablename__):
        ModuleDataVersion.__table__.create(bind=engine)
        logger.info(f"Migration: created '{ModuleDataVersion.__tablename__}' table")


def ensure_work_distribution_doctors_bm_abm_code_columns() -> None:
    """Add bm_code/abm_code to work_distribution_doctors if they predate
    the 2026-08 BM/ABM Code fix (see WorkDistributionDoctor's own
    docstring) -- the old bm/abm (name) columns are left in place,
    unused, rather than dropped (this project's migrations never drop
    columns; SQLAlchemy simply ignores a DB column with no matching model
    attribute). Full-replacement table (see
    app.work_distribution_service's own module docstring), so no backfill
    is attempted -- the very next upload rebuilds every row from scratch
    with real bm_code/abm_code values; existing rows just carry NULL for
    both until then, which nothing currently queries."""
    table = "work_distribution_doctors"
    if not inspect(get_config_engine()).has_table(table):
        return
    existing = _existing_columns(table)
    added = False
    with get_config_engine().begin() as conn:
        if "bm_code" not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN bm_code TEXT"))
            added = True
        if "abm_code" not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN abm_code TEXT"))
            added = True
    if added:
        logger.info(f"Migration: added bm_code/abm_code columns to '{table}'")


def ensure_work_distribution_findings_employee_code_column() -> None:
    """Add employee_code to work_distribution_findings if it predates the
    2026-08 BM/ABM Code fix (see WorkDistributionFinding's own docstring).
    Full-replacement table -- same no-backfill reasoning as
    ensure_work_distribution_doctors_bm_abm_code_columns above."""
    table = "work_distribution_findings"
    if not inspect(get_config_engine()).has_table(table):
        return
    if "employee_code" in _existing_columns(table):
        return
    with get_config_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN employee_code TEXT"))
    logger.info(f"Migration: added employee_code column to '{table}'")


# Every bookkeeping DateTime column in the schema, by table -- see
# backfill_bookkeeping_timestamps_to_utc()'s own docstring. Kept as one
# explicit map (not derived from the model classes at runtime) so this
# migration's behavior is pinned to what existed when it was written, not
# whatever the model happens to look like on some future run.
_TIMESTAMP_BACKFILL_COLUMNS: dict[str, list[str]] = {
    "import_history": ["imported_at"],
    "active_session": ["activated_at"],
    "investigation_findings": ["created_at", "updated_at"],
    "workbook_connections": ["updated_at"],
    "review_file_slots": ["uploaded_at"],
    "review_coverage_email_notifications": ["created_at", "sent_at"],
    "geocode_cache": ["created_at"],
    "hospital_lookup_cache": ["created_at"],
    "email_notifications": ["created_at", "sent_at", "updated_at"],
    "master_email_recipients": ["created_at", "updated_at"],
    "app_settings": ["updated_at"],
    "inventory_thresholds": ["last_updated"],
    "payment_invoices": ["created_at", "updated_at"],
    "payment_active_months": ["added_at"],
    "payment_customer_profiles": ["last_updated"],
    "outstanding_invoices": ["uploaded_at"],
    "inventory_replenishment": ["last_updated"],
    "cwh_stock": ["last_updated"],
    "inventory_email_recipients": ["created_at", "updated_at"],
    "work_distribution_doctors": ["last_updated"],
    "work_distribution_findings": ["last_updated"],
    "manager_work_allocation_records": ["last_updated"],
    "manager_work_allocation_findings": ["last_updated"],
    "manager_work_allocation_bm_details": ["last_updated"],
    "inventory_email_notifications": ["created_at", "sent_at"],
    "work_distribution_email_notifications": ["created_at", "sent_at"],
    "work_distribution_upload_log": ["uploaded_at"],
}


def backfill_bookkeeping_timestamps_to_utc() -> None:
    """One-time backfill: every EXISTING value in the 36 bookkeeping
    timestamp columns above (27 tables) was written by datetime.now() --
    this machine's local wall-clock time. Every one of this codebase's
    write sites has just been switched to database.connection.utcnow()
    instead (see that function's own docstring), so from here on every NEW
    row in these columns is UTC. Without this backfill, the column would
    silently hold two different clocks depending on which row was written
    before vs. after that switch -- exactly the Milestone 53 failure class
    (V2_MIGRATION_LOG.md:1483-1496): one column, two clocks, comparisons
    (ordering, "most recent", replay) silently wrong for whichever rows
    happen to be on the "other" clock.

    *** -5:30 IS SPECIFIC TO THIS DATABASE. IT IS NOT A REUSABLE HELPER. ***
    It is correct here ONLY because every existing row was written by a
    machine running India Standard Time with no DST (confirmed against
    this deployment's own OS: time.tzname() -> India Standard Time, fixed
    UTC+5:30 year-round). A different installation, or this same one on a
    machine in a different timezone, must NOT run this function expecting
    it to do the right thing -- it would apply the wrong shift to rows
    that were never IST to begin with. This is exactly why it is
    marker-guarded (timestamps_backfilled_to_utc on app_settings) and
    written to never run a second time on the same database, not written
    as a general "local-to-UTC" utility anyone could call again later.

    SEQUENCING -- two phases, not one transaction, because pysqlite
    auto-commits before DDL (see drop_developer_mode_schema()'s own
    docstring above -- that migration hit this for real: a DROP TABLE
    inside an engine.begin() block was NOT rolled back when a later
    statement in the same block failed). Mixing the marker column's ADD
    COLUMN into the same transaction as the data UPDATEs below would
    reopen that exact hazard.
      Phase 1 (schema only, idempotent, safe to repeat, no data touched):
        ensure the marker column exists.
      Phase 2 (pure DML, genuinely one atomic transaction -- no DDL
        statement appears in it, so pysqlite's autocommit-before-DDL
        quirk does not apply): if the marker isn't already set, shift
        every column in every table above AND set the marker, together.
        A crash or error anywhere in this phase rolls back the entire
        phase -- either every column shifts and the marker ends up set,
        or nothing does. There is no partial-shift state this phase can
        leave behind.

    NULLs: 13 of these 36 columns are nullable. strftime() returns NULL
    when given a NULL argument (verified directly against sqlite3 before
    writing this), so `SET col = strftime(...)` needs no per-column WHERE
    guard -- a NULL row's column stays NULL, never becomes today's date or
    an error.

    Precision: strftime('%Y-%m-%d %H:%M:%f', ...) preserves millisecond
    precision through the shift -- SQLite's own ceiling; it cannot carry
    the full microsecond precision Python's datetime.now() produces, and
    no code in this app compares these timestamps at sub-second
    granularity (confirmed: nothing anywhere compares a stored bookkeeping
    timestamp against a live datetime.now() -- every use is display or
    ordering, both fine with millisecond resolution)."""
    engine = get_config_engine()
    if not inspect(engine).has_table(APP_SETTINGS_TABLE):
        return

    # --- Phase 1: schema only -----------------------------------------
    if "timestamps_backfilled_to_utc" not in _existing_columns(APP_SETTINGS_TABLE):
        with engine.begin() as conn:
            conn.execute(
                text(f"ALTER TABLE {APP_SETTINGS_TABLE} ADD COLUMN timestamps_backfilled_to_utc INTEGER NOT NULL DEFAULT 0")
            )

    with engine.connect() as conn:
        already_done = conn.execute(
            text(f"SELECT timestamps_backfilled_to_utc FROM {APP_SETTINGS_TABLE} LIMIT 1")
        ).scalar()
    if already_done:
        return

    # --- Phase 2: the shift, pure DML, one transaction -----------------
    shifted: dict[str, int] = {}
    with engine.begin() as conn:
        for table, columns in _TIMESTAMP_BACKFILL_COLUMNS.items():
            if not inspect(engine).has_table(table):
                continue
            cols_here = [c for c in columns if c in _existing_columns(table)]
            if not cols_here:
                continue
            set_clause = ", ".join(
                f"{c} = strftime('%Y-%m-%d %H:%M:%f', {c}, '-5 hours', '-30 minutes')" for c in cols_here
            )
            result = conn.execute(text(f"UPDATE {table} SET {set_clause}"))
            shifted[table] = result.rowcount

        settings_row_count = conn.execute(text(f"SELECT COUNT(*) FROM {APP_SETTINGS_TABLE}")).scalar()
        if settings_row_count:
            conn.execute(text(f"UPDATE {APP_SETTINGS_TABLE} SET timestamps_backfilled_to_utc = 1"))
        else:
            # No row yet (never-configured install) -- every NOT NULL
            # column needs its real default spelled out explicitly here;
            # a raw SQL INSERT does not apply the model's Python-side
            # defaults the way going through the ORM would.
            conn.execute(
                text(
                    f"INSERT INTO {APP_SETTINGS_TABLE} "
                    "(automatic_email_enabled, setup_completed, inventory_data_reset_completed, "
                    "timestamps_backfilled_to_utc) VALUES (0, 0, 0, 1)"
                )
            )

    logger.info(
        "Migration: backfilled bookkeeping timestamps from IST to UTC across "
        f"{len(shifted)} table(s), {sum(shifted.values())} total row(s) touched: {shifted}"
    )


def run_startup_migrations() -> None:
    """Run all migrations, in order. Call once at application startup."""
    ensure_raw_visits_import_id_column()
    ensure_investigation_findings_import_id_column()
    ensure_email_notifications_error_message_column()
    ensure_investigation_findings_concentration_columns()
    migrate_email_settings_to_app_settings()
    drop_obsolete_employee_emails_table()
    ensure_investigation_findings_hospital_suppression_columns()
    ensure_app_settings_master_email_column()
    ensure_investigation_findings_hospital_detail_columns()
    ensure_hospital_lookup_cache_coordinate_columns()
    ensure_app_settings_geoapify_key_column()
    ensure_app_settings_setup_completed_column()
    drop_developer_mode_schema()
    ensure_payment_invoices_month_key_columns()
    ensure_outstanding_invoices_bill_amount_month_columns()
    ensure_investigation_findings_division_column()
    ensure_inventory_new_sales_format_schema()
    ensure_investigation_findings_updated_at_column()
    drop_investigation_findings_status_column()
    ensure_email_notifications_updated_at_column()
    ensure_workbook_connections_updated_at_column()
    ensure_payment_invoices_updated_at_column()
    ensure_payment_invoices_invoice_no_not_null_and_unique_index()
    ensure_cwh_stock_threshold_columns()
    ensure_manager_work_allocation_records_optional_columns()
    ensure_manager_work_allocation_records_source_engine_column()
    ensure_manager_work_allocation_findings_rbm_columns()
    ensure_manager_work_allocation_bm_details_reason_column()
    ensure_manager_work_allocation_records_month_sort_key_column()
    ensure_manager_work_allocation_records_pair_month_unique_index()
    ensure_review_file_slots_sync_columns()
    ensure_review_system_sync_tables()
    ensure_module_data_version_table()
    ensure_work_distribution_doctors_bm_abm_code_columns()
    ensure_work_distribution_findings_employee_code_column()
    ensure_app_settings_inventory_reset_column()
    backfill_bookkeeping_timestamps_to_utc()
