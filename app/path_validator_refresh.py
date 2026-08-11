"""Registers the Path Validator module's cloud "Refresh" -- one ordered
list of reconciliation operations, run through the generic, reusable
app/module_refresh_service.py. This is the ONE place that knows what
"Refresh" means for Path Validator (Parameters, imports, active session,
findings/emails for the active session, Organization Data workbooks);
every page, the sidebar's Refresh button, and the background poller all
call refresh_now()/is_refreshing() here instead of each calling individual
sync functions or managing their own concurrency guard.

Version 2.0, Milestone 35: every operation below is now a true
bidirectional Last-Modified-Wins reconciliation (see
app/sync_service.reconcile_rows()), not a one-directional pull -- the
same app-wide rule Inventory and Payments also use.
"""

from app.import_sync_service import sync_active_session, sync_findings_and_emails_for_active_session, sync_imports
from app.module_refresh_service import is_module_refreshing, refresh_module_async, register_pull_operations
from app.organization_data_sync_service import sync_workbooks
from app.rule_parameters import pull_and_apply_configuration

MODULE_KEY = "path_validator"

# Order matters: active session must be resolved (and, if it changed,
# reconstructed -- which itself syncs that import's findings) before
# reconciling findings/emails for "whatever the active session is now".
register_pull_operations(
    MODULE_KEY,
    [
        pull_and_apply_configuration,
        sync_imports,
        sync_active_session,
        sync_findings_and_emails_for_active_session,
        sync_workbooks,
    ],
)


def refresh_now(on_complete) -> bool:
    """Starts a full Path Validator cloud refresh in the background if one
    isn't already running. Returns False (no-op) if one is already in
    progress -- callers should treat that as "something is already
    syncing," not an error. `on_complete(RefreshResult)` runs on the
    background thread; callers marshal back to their own UI thread."""
    return refresh_module_async(MODULE_KEY, on_complete)


def is_refreshing() -> bool:
    return is_module_refreshing(MODULE_KEY)
