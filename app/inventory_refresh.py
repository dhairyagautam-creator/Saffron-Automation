"""Registers the Inventory Monitoring module's cloud "Refresh" -- mirrors
app/path_validator_refresh.py exactly, reusing the same generic
app/module_refresh_service.py. This is the ONE place that knows what
"Refresh" means for Inventory (Parameters, thresholds, replenishment);
the module-wide Refresh button (see ui/inventory_module.py) calls
refresh_now()/is_refreshing() here instead of each calling individual
pull_* functions or managing its own concurrency guard.

Manual button only for Inventory -- no automatic background poller
(a deliberate choice, confirmed with the user; Path Validator has one via
app/module_sync_poller.py, Inventory does not). If that changes later,
adding one is a single `module_sync_poller.start(module_shell, MODULE_KEY)`
call in ui/inventory_module.py -- no changes needed here.
"""

from app.inventory_parameters_service import pull_and_apply_configuration
from app.inventory_sync_service import sync_replenishment, sync_thresholds
from app.module_refresh_service import is_module_refreshing, refresh_module_async, register_pull_operations

MODULE_KEY = "inventory"

register_pull_operations(
    MODULE_KEY,
    [
        pull_and_apply_configuration,
        sync_thresholds,
        sync_replenishment,
    ],
)


def refresh_now(on_complete) -> bool:
    """Starts a full Inventory cloud refresh in the background if one
    isn't already running. Returns False (no-op) if one is already in
    progress. `on_complete(RefreshResult)` runs on the background thread;
    callers marshal back to their own UI thread."""
    return refresh_module_async(MODULE_KEY, on_complete)


def is_refreshing() -> bool:
    return is_module_refreshing(MODULE_KEY)
