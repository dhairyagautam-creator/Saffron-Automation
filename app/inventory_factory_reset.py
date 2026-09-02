"""One-time, company-wide Inventory data factory reset (2026-09, explicit
product decision, not a bug fix). Clears every row of InventoryThreshold/
InventoryReplenishment/CwhStock -- the three tables that hold PREVIOUSLY
UPLOADED Inventory business data (see database/models.py's own docstrings
for each) -- both locally and, for the two cloud-synced tables, in
Supabase too. inventory_parameters/inventory_email_recipients/
inventory_email_notifications are never touched -- those are application
settings/configuration, not uploaded data. The Inventory module itself
(UI, upload/processing logic, calculations) is completely untouched; this
only clears already-persisted data.

RUNS EXACTLY ONCE PER INSTALLATION, then becomes a permanent no-op --
gated by AppSettings.inventory_data_reset_completed (see that column's
own docstring; same "GLOBAL, user row only" convention as
setup_completed). The marker is set ONLY after BOTH the local clear and
the cloud clear succeed; a failed cloud clear (Supabase unreachable, or
any other error) leaves it unset so run_inventory_factory_reset_if_needed()
simply tries again on the next launch -- it must never lie about having
completed a reset that didn't fully happen.

CLOUD CLEAR REUSES EXISTING, ALREADY-TESTED CODE: push_thresholds_full_
replace()/push_replenishment_full_replace() (app/inventory_sync_service.py)
already implement exactly "delete every row of this cloud table, then
push whatever the local table currently holds" -- the same mechanism a
real Sales/Inventory Report upload already triggers. Called here AFTER
the local tables have already been cleared, so they push nothing and
simply leave the cloud tables empty too. No new sync code path.

ORDERING / RACE SAFETY (the reason this must be called from main.py,
not from anywhere inside MainWindow or a background thread): this module
is called once, synchronously, from main() -- see main.py -- strictly
AFTER run_startup_migrations() (so the marker column already exists) and
strictly BEFORE MainWindow()/app.mainloop() ever runs, which is also
before app.module_sync_poller ever starts and before any other code path
that could trigger an ordinary Inventory sync (sync_thresholds()/
sync_replenishment(), the Last-Modified-Wins reconcile used for routine
Refresh clicks). Nothing else touches Inventory sync during that window,
so there is no concurrent sync that could race this: it is impossible
for an UPDATED installation to push its own stale local data back into
the cloud mid-reset, because nothing else runs until this function has
already returned.

WHAT THIS CANNOT DO: an installation that has not yet updated to a
version carrying this migration keeps syncing normally in the meantime,
completely unaware of the reset -- if IT pushes its own old local rows
before it updates, that resurrects the cloud tables for everyone,
including installations that already completed their own reset. This is
an inherent limitation of the existing Last-Modified-Wins sync design
(app.sync_service.reconcile_rows() has no concept of cross-device
deletion propagation), not something a per-installation migration can
close -- confirmed and accepted as part of this rollout.
"""

from loguru import logger

from app.inventory_sync_service import push_replenishment_full_replace, push_thresholds_full_replace
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

        # Cloud clear -- see module docstring for why both must succeed
        # before the marker is set, and why calling these AFTER the local
        # clear above is what makes them push nothing.
        thresholds_cloud_ok = push_thresholds_full_replace()
        replenishment_cloud_ok = push_replenishment_full_replace()

        if not (thresholds_cloud_ok and replenishment_cloud_ok):
            logger.warning(
                "Inventory factory reset: local data cleared, but the cloud clear did not fully "
                "succeed (Supabase unreachable, or the request was rejected) -- NOT marking the "
                "reset complete. Will retry on the next launch. In the meantime, an ordinary "
                "Inventory sync during this session may repopulate local data from the cloud if "
                "the cloud still holds old rows -- expected and harmless; the next launch's retry "
                "clears it again."
            )
            return

        _mark_reset_completed(session)
        logger.info(
            "Inventory factory reset: complete (local and cloud both cleared) -- "
            "will never run again on this installation"
        )
    except Exception as exc:
        logger.error(f"Inventory factory reset: unexpected error, will retry on next launch: {exc!r}")
    finally:
        session.close()
