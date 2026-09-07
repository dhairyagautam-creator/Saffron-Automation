"""One-time, company-wide Inventory data factory reset (2026-09, explicit
product decision, not a bug fix). Clears every row of InventoryThreshold/
InventoryReplenishment/CwhStock -- the three tables that hold PREVIOUSLY
UPLOADED Inventory business data (see database/models.py's own docstrings
for each) -- locally. inventory_parameters/inventory_email_recipients/
inventory_email_notifications are never touched -- those are application
settings/configuration, not uploaded data. The Inventory module itself
(UI, upload/processing logic, calculations) is completely untouched; this
only clears already-persisted data.

RUNS EXACTLY ONCE PER INSTALLATION, then becomes a permanent no-op --
gated by AppSettings.inventory_data_reset_completed (see that column's
own docstring; same "GLOBAL, user row only" convention as
setup_completed). The marker is set immediately once the local clear
succeeds.

ORDERING (the reason this must be called from main.py, not from anywhere
inside MainWindow or a background thread): this module is called once,
synchronously, from main() -- see main.py -- strictly AFTER
run_startup_migrations() (so the marker column already exists) and
strictly BEFORE MainWindow()/app.mainloop() ever runs.
"""

from loguru import logger

from database.connection import get_config_session
from database.models import AppSettings, CwhStock, InventoryReplenishment, InventoryThreshold

_USER_ENVIRONMENT = "user"


def _is_reset_already_completed(session) -> bool:
    row = session.query(AppSettings).filter_by(environment=_USER_ENVIRONMENT).first()
    return bool(row and row.inventory_data_reset_completed)


def _mark_reset_completed(session) -> None:
    row = session.query(AppSettings).filter_by(environment=_USER_ENVIRONMENT).first()
    if row is None:
        # No app_settings row exists yet at all (a genuinely brand-new
        # install that hasn't reached the Settings page once) -- create
        # the GLOBAL user row rather than leaving nothing to mark.
        row = AppSettings(environment=_USER_ENVIRONMENT)
        session.add(row)
    row.inventory_data_reset_completed = 1
    session.commit()


def run_inventory_factory_reset_if_needed() -> None:
    """Call once at startup (see main.py) -- safe to call on every launch;
    the marker check makes every call after the first successful one an
    immediate no-op. Never raises: a startup migration must never be the
    reason the application fails to open (mirrors main.py's own
    try/except around run_startup_migrations() and friends)."""
    session = get_config_session()
    try:
        if _is_reset_already_completed(session):
            return

        logger.info("Inventory factory reset: starting one-time company-wide data reset")

        deleted_thresholds = session.query(InventoryThreshold).delete()
        deleted_replenishment = session.query(InventoryReplenishment).delete()
        deleted_cwh = session.query(CwhStock).delete()
        session.commit()
        logger.info(
            f"Inventory factory reset: cleared locally -- {deleted_thresholds} threshold row(s), "
            f"{deleted_replenishment} replenishment row(s), {deleted_cwh} CWH stock row(s)"
        )

        _mark_reset_completed(session)
        logger.info(
            "Inventory factory reset: complete (local data cleared) -- "
            "will never run again on this installation"
        )
    except Exception as exc:
        logger.error(f"Inventory factory reset: unexpected error, will retry on next launch: {exc!r}")
    finally:
        session.close()
