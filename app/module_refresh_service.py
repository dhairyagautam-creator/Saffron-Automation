"""Generic, reusable "Refresh" action for any cloud-synced module -- one
shared mechanism a module's own code plugs into by registering its list of
pull operations, rather than each page/module inventing its own background
thread + concurrency guard + push_config-shaped result.

This module knows nothing about Path Validator, Inventory, or Payments --
exactly the same "generic layer, caller owns the shape" discipline as
app/sync_service.py's push_config/push_rows/etc. Path Validator's own
"what does a Refresh mean" knowledge lives in app/path_validator_refresh.py;
a future Inventory/Payments module registers its own operations list here
under its own module_key with zero changes to this file.
"""

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from loguru import logger

_registry: dict[str, list] = {}
_locks: dict[str, threading.Lock] = {}


def register_pull_operations(module_key: str, operations: list) -> None:
    """Registers the ordered list of zero-argument pull functions that
    make up "Refresh" for `module_key`. Each operation returns a truthy
    value if it changed anything locally (matching every pull_* function's
    existing convention elsewhere in this codebase), or a falsy value/None
    if nothing changed. Call once, at import time, from the module that
    owns this list (see app/path_validator_refresh.py)."""
    _registry[module_key] = list(operations)
    _locks.setdefault(module_key, threading.Lock())


@dataclass
class RefreshResult:
    success: bool
    changed: bool = False
    error_message: Optional[str] = None
    finished_at: Optional[datetime] = None


def is_module_refreshing(module_key: str) -> bool:
    lock = _locks.get(module_key)
    return lock.locked() if lock else False


def refresh_module_async(module_key: str, on_complete: Callable[[RefreshResult], None]) -> bool:
    """Starts a background refresh for `module_key` if one isn't already
    running -- runs every registered operation in order, on one
    background thread, never the caller's (UI) thread. Returns False
    immediately (no-op) if a refresh for this module_key is already in
    progress; `on_complete` is only ever called for a refresh that
    actually ran.

    `on_complete(RefreshResult)` is invoked from the background thread --
    callers marshal back to their own UI thread themselves (e.g. via
    widget.after(0, ...)), exactly like every other background-thread
    pattern already in this codebase (see ui/operations_page.py)."""
    lock = _locks.setdefault(module_key, threading.Lock())
    if not lock.acquire(blocking=False):
        logger.debug(f"Module refresh: '{module_key}' already running -- ignoring this request")
        return False

    def worker() -> None:
        changed = False
        try:
            for operation in _registry.get(module_key, []):
                result = operation()
                if result:
                    changed = True
            logger.info(f"Module refresh: '{module_key}' complete (changed={changed})")
            on_complete(RefreshResult(success=True, changed=changed, finished_at=datetime.now()))
        except Exception as exc:
            logger.warning(f"Module refresh: '{module_key}' failed: {exc!r}")
            on_complete(RefreshResult(success=False, error_message=str(exc)))
        finally:
            lock.release()

    logger.info(f"Module refresh: '{module_key}' starting ({len(_registry.get(module_key, []))} operation(s))")
    threading.Thread(target=worker, daemon=True).start()
    return True
