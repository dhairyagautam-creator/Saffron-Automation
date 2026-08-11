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


def ensure_investigation_findings_sync_state_columns() -> None:
    """Add the Phase 1 sync-reliability columns to investigation_findings if
    they predate the dirty/cloud_version design (see app/findings_sync_service.py
    and app/sync_service.reconcile_rows()):

      * cloud_version -- the cloud row's own `updated_at` token this local row
        last acknowledged (server-origin; compared only against the cloud's
        current version, never a local clock).
      * dirty -- 1 when a local business change hasn't been acknowledged by
        the cloud yet.

    Existing rows backfill to dirty=0 (assumed already in sync) and
    cloud_version NULL; their first post-migration reconcile establishes a
    version. Idempotent -- safe to run repeatedly."""
    if not inspect(get_config_engine()).has_table(INVESTIGATION_FINDINGS_TABLE):
        return
    existing = _existing_columns(INVESTIGATION_FINDINGS_TABLE)
    new_columns = {
        "cloud_version": "TEXT",
        "dirty": "INTEGER NOT NULL DEFAULT 0",
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


def ensure_app_settings_environment_columns() -> None:
    """Add the Developer Mode columns to app_settings (see
    database/models.py, app/mode_state.py): `environment` so credentials can
    be kept separate per mode, plus the global `dev_password_hash`/
    `dev_password_salt` gate columns. The existing single row becomes the
    'user' (production) environment, and a one-time copy of it is seeded as
    the 'developer' environment so Developer Mode starts from the real
    production baseline rather than blank."""
    if not inspect(get_config_engine()).has_table(APP_SETTINGS_TABLE):
        return
    existing = _existing_columns(APP_SETTINGS_TABLE)
    with get_config_engine().begin() as conn:
        if "environment" not in existing:
            # NOT NULL DEFAULT 'user' backfills every existing row as the
            # production environment in the same statement.
            conn.execute(text(f"ALTER TABLE {APP_SETTINGS_TABLE} ADD COLUMN environment TEXT NOT NULL DEFAULT 'user'"))
        if "dev_password_hash" not in existing:
            conn.execute(text(f"ALTER TABLE {APP_SETTINGS_TABLE} ADD COLUMN dev_password_hash TEXT"))
        if "dev_password_salt" not in existing:
            conn.execute(text(f"ALTER TABLE {APP_SETTINGS_TABLE} ADD COLUMN dev_password_salt TEXT"))

        # Seed a developer copy of the user row exactly once — only credential
        # columns are copied; the global fields (setup_completed, dev
        # password) are left at defaults on the developer row since they're
        # only ever read from the user row.
        dev_count = conn.execute(
            text(f"SELECT COUNT(*) FROM {APP_SETTINGS_TABLE} WHERE environment = 'developer'")
        ).scalar()
        user_exists = conn.execute(
            text(f"SELECT COUNT(*) FROM {APP_SETTINGS_TABLE} WHERE environment = 'user'")
        ).scalar()
        if not dev_count and user_exists:
            conn.execute(
                text(
                    f"INSERT INTO {APP_SETTINGS_TABLE} "
                    "(environment, sender_gmail_address, gmail_app_password, automatic_email_enabled, "
                    "master_email_address, geoapify_api_key, setup_completed, updated_at) "
                    "SELECT 'developer', sender_gmail_address, gmail_app_password, automatic_email_enabled, "
                    "master_email_address, geoapify_api_key, 0, updated_at "
                    f"FROM {APP_SETTINGS_TABLE} WHERE environment = 'user'"
                )
            )
            logger.info(f"Migration: seeded 'developer' environment copy in '{APP_SETTINGS_TABLE}'")
    if "environment" not in existing:
        logger.info(f"Migration: added environment + dev-password columns to '{APP_SETTINGS_TABLE}'")


def ensure_rule_parameters_environment_column() -> None:
    """Give rule_parameters an `environment` column so User Mode and
    Developer Mode keep separate rule thresholds. SQLite can't alter a
    table's UNIQUE constraint in place — the constraint has to change from
    (rule_name, parameter_name) to (environment, rule_name, parameter_name)
    — so the table is rebuilt: create the new-shape table, copy every
    existing row in as the 'user' environment AND as a 'developer' copy
    (dev starts from the production baseline), then swap it in."""
    table = "rule_parameters"
    if not inspect(get_config_engine()).has_table(table):
        return
    if "environment" in _existing_columns(table):
        return
    with get_config_engine().begin() as conn:
        conn.execute(
            text(
                f"CREATE TABLE {table}_new ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "environment TEXT NOT NULL DEFAULT 'user', "
                "rule_name TEXT NOT NULL, "
                "parameter_name TEXT NOT NULL, "
                "parameter_value TEXT NOT NULL, "
                "UNIQUE(environment, rule_name, parameter_name))"
            )
        )
        for env in ("user", "developer"):
            conn.execute(
                text(
                    f"INSERT INTO {table}_new (environment, rule_name, parameter_name, parameter_value) "
                    f"SELECT '{env}', rule_name, parameter_name, parameter_value FROM {table}"
                )
            )
        conn.execute(text(f"DROP TABLE {table}"))
        conn.execute(text(f"ALTER TABLE {table}_new RENAME TO {table}"))
    logger.info(f"Migration: added environment column to '{table}' (rebuilt; existing rows kept as 'user' + copied to 'developer')")


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


def ensure_hospital_suppression_enabled_in_user_mode() -> None:
    """Promote Hospital Suppression to production. It shipped Developer-
    Mode-only (the feature flag defaulted OFF in the 'user' environment)
    while experimental; from v1.3 it is a standard, always-on stage of the
    Validator notification pipeline (see app/feature_flags_service.py's
    flipped default and app/notification_service.py). ensure_flag_defaults()
    never overwrites an existing row, so the default change alone only
    reaches brand-new installs -- this force-enables the flag for the 'user'
    environment on every existing install too, creating the row if it's
    missing. User-mode flags are never end-user-toggleable (they change only
    via Publish), so enforcing this production invariant on startup is safe;
    a developer can still toggle the separate 'developer'-environment flag
    off for testing without affecting production."""
    table = "feature_flags"
    if not inspect(get_config_engine()).has_table(table):
        return
    with get_config_engine().begin() as conn:
        row = conn.execute(
            text(
                "SELECT id, enabled FROM feature_flags "
                "WHERE environment = 'user' AND flag_name = 'hospital_suppression'"
            )
        ).first()
        if row is None:
            conn.execute(
                text(
                    "INSERT INTO feature_flags (environment, flag_name, enabled) "
                    "VALUES ('user', 'hospital_suppression', 1)"
                )
            )
            logger.info("Migration: enabled hospital_suppression in user mode (row created)")
        elif not row[1]:
            conn.execute(text("UPDATE feature_flags SET enabled = 1 WHERE id = :id"), {"id": row[0]})
            logger.info("Migration: promoted hospital_suppression to ON in user mode (production)")


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


def ensure_import_history_cloud_columns() -> None:
    """Add cloud_id/synced_at/sync_origin to import_history for the Path
    Validator cloud sync effort (see app/import_sync_service.py). cloud_id
    is the client-generated UUID that ties an import together across
    laptops -- local autoincrement ids collide across machines and can
    never be used as the cross-machine key. sync_origin distinguishes an
    import this machine ran itself ('local') from one reconstructed from
    another machine's push ('remote') -- 'remote' imports skip local rule
    evaluation and rely on synced findings instead (see
    app/findings_sync_service.py). Pre-existing rows get cloud_id = NULL
    (SQLite's unique index allows any number of NULLs) and sync_origin
    defaults to 'local' -- they simply aren't visible to another laptop
    until backfilled by hand, per the plan's explicit no-auto-backfill
    decision."""
    if not inspect(get_config_engine()).has_table(IMPORT_HISTORY_TABLE):
        return
    existing = _existing_columns(IMPORT_HISTORY_TABLE)
    with get_config_engine().begin() as conn:
        if "cloud_id" not in existing:
            conn.execute(text(f"ALTER TABLE {IMPORT_HISTORY_TABLE} ADD COLUMN cloud_id TEXT"))
            conn.execute(
                text(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_import_history_cloud_id ON {IMPORT_HISTORY_TABLE}(cloud_id)")
            )
        if "synced_at" not in existing:
            conn.execute(text(f"ALTER TABLE {IMPORT_HISTORY_TABLE} ADD COLUMN synced_at DATETIME"))
        if "sync_origin" not in existing:
            conn.execute(
                text(f"ALTER TABLE {IMPORT_HISTORY_TABLE} ADD COLUMN sync_origin TEXT NOT NULL DEFAULT 'local'")
            )
    if "cloud_id" not in existing:
        logger.info(f"Migration: added cloud_id/synced_at/sync_origin columns to '{IMPORT_HISTORY_TABLE}'")


def ensure_active_session_cloud_columns() -> None:
    """Add import_cloud_id/synced_at to active_session -- the shared "which
    import is the team's current working session" pointer synced via
    path_validator_active_session (see app/import_sync_service.py)."""
    if not inspect(get_config_engine()).has_table(ACTIVE_SESSION_TABLE):
        return
    existing = _existing_columns(ACTIVE_SESSION_TABLE)
    with get_config_engine().begin() as conn:
        if "import_cloud_id" not in existing:
            conn.execute(text(f"ALTER TABLE {ACTIVE_SESSION_TABLE} ADD COLUMN import_cloud_id TEXT"))
        if "synced_at" not in existing:
            conn.execute(text(f"ALTER TABLE {ACTIVE_SESSION_TABLE} ADD COLUMN synced_at DATETIME"))
    if "import_cloud_id" not in existing:
        logger.info(f"Migration: added import_cloud_id/synced_at columns to '{ACTIVE_SESSION_TABLE}'")


def ensure_investigation_findings_cloud_columns() -> None:
    """Add cloud_id/updated_at/synced_at to investigation_findings for
    cloud sync (see app/findings_sync_service.py). updated_at is backfilled
    from each row's own created_at so pre-existing findings get a sane
    initial value rather than NULL; every reviewer status change from here
    on (app/findings_service.py's set_status()/set_notification_status())
    bumps it, which is what the poller's delta-pull relies on."""
    if not inspect(get_config_engine()).has_table(INVESTIGATION_FINDINGS_TABLE):
        return
    existing = _existing_columns(INVESTIGATION_FINDINGS_TABLE)
    with get_config_engine().begin() as conn:
        if "cloud_id" not in existing:
            conn.execute(text(f"ALTER TABLE {INVESTIGATION_FINDINGS_TABLE} ADD COLUMN cloud_id TEXT"))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_investigation_findings_cloud_id "
                    f"ON {INVESTIGATION_FINDINGS_TABLE}(cloud_id)"
                )
            )
        if "updated_at" not in existing:
            conn.execute(text(f"ALTER TABLE {INVESTIGATION_FINDINGS_TABLE} ADD COLUMN updated_at DATETIME"))
            conn.execute(
                text(f"UPDATE {INVESTIGATION_FINDINGS_TABLE} SET updated_at = created_at WHERE updated_at IS NULL")
            )
        if "synced_at" not in existing:
            conn.execute(text(f"ALTER TABLE {INVESTIGATION_FINDINGS_TABLE} ADD COLUMN synced_at DATETIME"))
    if "cloud_id" not in existing:
        logger.info(
            f"Migration: added cloud_id/updated_at/synced_at columns to '{INVESTIGATION_FINDINGS_TABLE}' "
            "(updated_at backfilled from created_at)"
        )


def ensure_email_notifications_cloud_columns() -> None:
    """Add cloud_id/updated_at/synced_at to email_notifications for cloud
    sync (see app/email_sync_service.py) -- same updated_at-backfilled-from-
    created_at treatment as investigation_findings, for the same reason."""
    if not inspect(get_config_engine()).has_table(EMAIL_NOTIFICATIONS_TABLE):
        return
    existing = _existing_columns(EMAIL_NOTIFICATIONS_TABLE)
    with get_config_engine().begin() as conn:
        if "cloud_id" not in existing:
            conn.execute(text(f"ALTER TABLE {EMAIL_NOTIFICATIONS_TABLE} ADD COLUMN cloud_id TEXT"))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_email_notifications_cloud_id "
                    f"ON {EMAIL_NOTIFICATIONS_TABLE}(cloud_id)"
                )
            )
        if "updated_at" not in existing:
            conn.execute(text(f"ALTER TABLE {EMAIL_NOTIFICATIONS_TABLE} ADD COLUMN updated_at DATETIME"))
            conn.execute(
                text(f"UPDATE {EMAIL_NOTIFICATIONS_TABLE} SET updated_at = created_at WHERE updated_at IS NULL")
            )
        if "synced_at" not in existing:
            conn.execute(text(f"ALTER TABLE {EMAIL_NOTIFICATIONS_TABLE} ADD COLUMN synced_at DATETIME"))
    if "cloud_id" not in existing:
        logger.info(
            f"Migration: added cloud_id/updated_at/synced_at columns to '{EMAIL_NOTIFICATIONS_TABLE}' "
            "(updated_at backfilled from created_at)"
        )


def ensure_workbook_connections_cloud_columns() -> None:
    """Add storage_path/cloud_updated_at/synced_at/updated_at to
    workbook_connections (see app/organization_data_sync_service.py).
    storage_path replaces the local file_path's role once a workbook has
    been pushed to Storage -- file_path itself is left alone (still used
    to remember what was last manually browsed on this machine, if
    anything). updated_at is the genuine local "last modified" timestamp
    the app-wide Last-Modified-Wins rule (Milestone 35) compares against
    the cloud's own updated_at -- distinct from cloud_updated_at, which
    only ever records the cloud's timestamp as of the last successful
    sync, not when this workbook was last connected locally. Backfilled
    from cloud_updated_at (or now(), if never synced) so an existing
    install doesn't start with a NULL that would always lose to the
    cloud."""
    if not inspect(get_config_engine()).has_table(WORKBOOK_CONNECTIONS_TABLE):
        return
    existing = _existing_columns(WORKBOOK_CONNECTIONS_TABLE)
    with get_config_engine().begin() as conn:
        if "storage_path" not in existing:
            conn.execute(text(f"ALTER TABLE {WORKBOOK_CONNECTIONS_TABLE} ADD COLUMN storage_path TEXT"))
        if "cloud_updated_at" not in existing:
            conn.execute(text(f"ALTER TABLE {WORKBOOK_CONNECTIONS_TABLE} ADD COLUMN cloud_updated_at DATETIME"))
        if "synced_at" not in existing:
            conn.execute(text(f"ALTER TABLE {WORKBOOK_CONNECTIONS_TABLE} ADD COLUMN synced_at DATETIME"))
        if "updated_at" not in existing:
            conn.execute(text(f"ALTER TABLE {WORKBOOK_CONNECTIONS_TABLE} ADD COLUMN updated_at DATETIME"))
            conn.execute(
                text(
                    f"UPDATE {WORKBOOK_CONNECTIONS_TABLE} SET updated_at = COALESCE(cloud_updated_at, CURRENT_TIMESTAMP) "
                    "WHERE updated_at IS NULL"
                )
            )
    if "storage_path" not in existing:
        logger.info(
            f"Migration: added storage_path/cloud_updated_at/synced_at/updated_at columns to "
            f"'{WORKBOOK_CONNECTIONS_TABLE}'"
        )


def ensure_payment_invoices_cloud_columns() -> None:
    """Add cloud_id/updated_at/synced_at to payment_invoices for the
    Payment Analytics cloud sync effort (see
    app/payment_sync_service.py). updated_at is backfilled from each
    row's own created_at -- these rows are immutable once inserted (only
    ever created via a monthly append, or wiped wholesale by a Historical
    Report re-run; individual rows are never edited in place), so
    updated_at never needs bumping again after this one-time backfill."""
    if not inspect(get_config_engine()).has_table(PAYMENT_INVOICES_TABLE):
        return
    existing = _existing_columns(PAYMENT_INVOICES_TABLE)
    with get_config_engine().begin() as conn:
        if "cloud_id" not in existing:
            conn.execute(text(f"ALTER TABLE {PAYMENT_INVOICES_TABLE} ADD COLUMN cloud_id TEXT"))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_invoices_cloud_id "
                    f"ON {PAYMENT_INVOICES_TABLE}(cloud_id)"
                )
            )
        if "updated_at" not in existing:
            conn.execute(text(f"ALTER TABLE {PAYMENT_INVOICES_TABLE} ADD COLUMN updated_at DATETIME"))
            conn.execute(
                text(f"UPDATE {PAYMENT_INVOICES_TABLE} SET updated_at = created_at WHERE updated_at IS NULL")
            )
        if "synced_at" not in existing:
            conn.execute(text(f"ALTER TABLE {PAYMENT_INVOICES_TABLE} ADD COLUMN synced_at DATETIME"))
    if "cloud_id" not in existing:
        logger.info(
            f"Migration: added cloud_id/updated_at/synced_at columns to '{PAYMENT_INVOICES_TABLE}' "
            "(updated_at backfilled from created_at)"
        )


def ensure_outstanding_invoices_cloud_columns() -> None:
    """Add cloud_id/synced_at to outstanding_invoices (see
    app/payment_sync_service.py). No updated_at needed -- unlike
    payment_invoices, this table is always synced as a full replace in
    both directions (matching its local "Daily Refresh" delete-all +
    reinsert behavior), never a delta-pull; cloud_id exists solely so the
    followed_up checkbox toggle can address one specific row across
    machines between uploads."""
    if not inspect(get_config_engine()).has_table(OUTSTANDING_INVOICES_TABLE):
        return
    existing = _existing_columns(OUTSTANDING_INVOICES_TABLE)
    with get_config_engine().begin() as conn:
        if "cloud_id" not in existing:
            conn.execute(text(f"ALTER TABLE {OUTSTANDING_INVOICES_TABLE} ADD COLUMN cloud_id TEXT"))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_outstanding_invoices_cloud_id "
                    f"ON {OUTSTANDING_INVOICES_TABLE}(cloud_id)"
                )
            )
        if "synced_at" not in existing:
            conn.execute(text(f"ALTER TABLE {OUTSTANDING_INVOICES_TABLE} ADD COLUMN synced_at DATETIME"))
        if "updated_at" not in existing:
            conn.execute(text(f"ALTER TABLE {OUTSTANDING_INVOICES_TABLE} ADD COLUMN updated_at DATETIME"))
            conn.execute(
                text(f"UPDATE {OUTSTANDING_INVOICES_TABLE} SET updated_at = uploaded_at WHERE updated_at IS NULL")
            )
    if "cloud_id" not in existing:
        logger.info(f"Migration: added cloud_id/synced_at/updated_at columns to '{OUTSTANDING_INVOICES_TABLE}'")


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
    ensure_app_settings_environment_columns()
    ensure_rule_parameters_environment_column()
    ensure_payment_invoices_month_key_columns()
    ensure_outstanding_invoices_bill_amount_month_columns()
    ensure_investigation_findings_division_column()
    ensure_inventory_new_sales_format_schema()
    ensure_hospital_suppression_enabled_in_user_mode()
    ensure_import_history_cloud_columns()
    ensure_active_session_cloud_columns()
    ensure_investigation_findings_cloud_columns()
    ensure_investigation_findings_sync_state_columns()
    ensure_email_notifications_cloud_columns()
    ensure_workbook_connections_cloud_columns()
    ensure_payment_invoices_cloud_columns()
    ensure_outstanding_invoices_cloud_columns()
    ensure_cwh_stock_threshold_columns()
    ensure_manager_work_allocation_records_optional_columns()
    ensure_manager_work_allocation_records_source_engine_column()
    ensure_manager_work_allocation_findings_rbm_columns()
    ensure_manager_work_allocation_bm_details_reason_column()
    ensure_manager_work_allocation_records_month_sort_key_column()
