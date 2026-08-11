"""Generic, reusable background-polling trigger for any cloud-synced
module's shell -- Version 2.0, Milestone 22. Built entirely on top of the
already-generic app/module_refresh_service.py; this file adds nothing but
the "tick on a timer, re-render the visible page if something changed"
plumbing, so a module only has to call start() once with its own
module_key.

Originally written specifically for Path Validator (as app/sync_poller.py,
Milestones 17/20); generalized here so Inventory/Payments can start the
exact same poller against their own module_key with no new code -- "keep a
single synchronization framework," not one poller per module.

Reuses the self-rescheduling `.after()` timer pattern established by
ui/email_center_page.py (the first polling loop in this codebase), on a
much slower cadence than that one -- this is a speculative "did another
machine change anything" check against Supabase, not watching a send
already known to be in progress on this machine.

`module_shell` is any Tk widget shaped like PathValidatorModule/
InventoryModule: needs `.winfo_exists()`, `.after(ms, fn)`,
`.winfo_ismapped()`, `.active_page` (str | None), and `.pages` (dict of
name -> page widget, each with an optional `on_show()`). Duck-typed
deliberately -- no shared base class required.
"""

from loguru import logger

from app.module_refresh_service import refresh_module_async

# Deliberately much slower than email_center_page.py's 1000ms poll -- that
# one watches a send already known to be running on THIS machine; this one
# is a speculative cross-machine check against Supabase, so a slower
# cadence avoids needless request volume when nothing has changed. A
# tradeoff, not a fixed answer -- tune per module after real usage.
DEFAULT_POLL_INTERVAL_MS = 15000


def start(module_shell, module_key: str, interval_ms: int = DEFAULT_POLL_INTERVAL_MS) -> None:
    """Starts the poller for `module_shell` under `module_key`. Safe to
    call once per module shell; each tick reschedules itself.

    The first tick is deferred via module_shell.after(0, ...) rather than
    run immediately -- start() is typically called from the module
    shell's __init__(), before the Tk main loop has necessarily started
    (the shell finishes constructing before main.py calls mainloop()).
    Spawning a network-bound background thread synchronously here would
    race: if the Supabase round-trip completes before mainloop() actually
    begins, the thread's own module_shell.after() call fails with "main
    thread is not in main loop". Deferring by one tick guarantees
    mainloop() is already running by the time anything network-bound
    starts."""
    logger.info(f"Sync poller ('{module_key}'): starting (interval={interval_ms}ms)")
    module_shell.after(0, lambda: _tick(module_shell, module_key, interval_ms))


def stop(module_shell, module_key: str) -> None:
    """No-op placeholder for symmetry/testability -- nothing currently
    calls this, since module shells are never torn down mid-session (each
    is constructed exactly once by MainWindow, see ui/main_window.py). Tk
    destroys all pending .after() callbacks automatically when the root
    window closes."""
    logger.info(f"Sync poller ('{module_key}'): stop() called (no-op -- shells are never torn down mid-session)")


def _tick(module_shell, module_key: str, interval_ms: int) -> None:
    if not module_shell.winfo_exists():
        return

    started = refresh_module_async(module_key, lambda result: _on_complete(module_shell, module_key, result))
    if not started:
        logger.debug(f"Sync poller ('{module_key}'): a refresh is already running -- skipping this tick")

    module_shell.after(interval_ms, lambda: _tick(module_shell, module_key, interval_ms))


def _on_complete(module_shell, module_key: str, result) -> None:
    """Runs on the background thread refresh_module_async() spawned --
    only the final "re-render the visible page" step is marshaled back to
    the main thread via module_shell.after()."""
    if not result.success or not result.changed:
        return

    def apply() -> None:
        if not module_shell.winfo_exists() or not module_shell.active_page:
            return
        page = module_shell.pages.get(module_shell.active_page)
        if page is not None and hasattr(page, "on_show") and module_shell.winfo_ismapped():
            logger.debug(f"Sync poller ('{module_key}'): re-rendering '{module_shell.active_page}'")
            page.on_show()

    module_shell.after(0, apply)
