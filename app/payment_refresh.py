"""Registers the Payment Analytics module's cloud "Refresh" -- mirrors
app/path_validator_refresh.py/app/inventory_refresh.py exactly, reusing
the same generic app/module_refresh_service.py. This is the ONE place
that knows what "Refresh" means for Payment Analytics (Parameters,
invoices, active months, customer profiles, outstanding invoices); the
module-wide Refresh button (see ui/payment_analytics_module.py) calls
refresh_now()/is_refreshing() here instead of each calling individual
pull_* functions or managing its own concurrency guard.

Manual button only for Payments -- no automatic background poller (a
deliberate choice, confirmed with the user; same as Inventory). Adding
one later, if ever wanted, is a single
`module_sync_poller.start(module_shell, MODULE_KEY)` call in
ui/payment_analytics_module.py -- no changes needed here.
"""

from app.module_refresh_service import is_module_refreshing, refresh_module_async, register_pull_operations
from app.payment_parameters_service import pull_and_apply_configuration
from app.payment_sync_service import (
    sync_active_months,
    sync_customer_profiles,
    sync_invoices,
    sync_outstanding_invoices,
)

MODULE_KEY = "payments"

# Order matters: active months should be current before invoices/profiles
# are interpreted against them, though each reconcile is independently
# correct regardless of order (none of these depend on another having
# just run).
register_pull_operations(
    MODULE_KEY,
    [
        pull_and_apply_configuration,
        sync_active_months,
        sync_invoices,
        sync_customer_profiles,
        sync_outstanding_invoices,
    ],
)


def refresh_now(on_complete) -> bool:
    """Starts a full Payment Analytics cloud refresh in the background if
    one isn't already running. Returns False (no-op) if one is already in
    progress. `on_complete(RefreshResult)` runs on the background thread;
    callers marshal back to their own UI thread."""
    return refresh_module_async(MODULE_KEY, on_complete)


def is_refreshing() -> bool:
    return is_module_refreshing(MODULE_KEY)
