"""Tracks whether the automatic email batch for the active session is
currently being sent, plus live per-stage progress, so the Email Center and
Operations pages can show real-time status without any manual refresh.

In-memory only (not persisted) — this is transient UI state for the single
running app process, not data that needs to survive a restart.
"""

_state = {
    "sending": False,
    "import_id": None,
    "stage": None,
    "label": None,
    "completed": None,
    "total": None,
}


def is_sending(import_id: int | None = None) -> bool:
    """True if a send is currently in progress. If `import_id` is given,
    only True when that specific import is the one being sent."""
    if not _state["sending"]:
        return False
    if import_id is None:
        return True
    return _state["import_id"] == import_id


def start_sending(import_id: int) -> None:
    _state["sending"] = True
    _state["import_id"] = import_id
    _state["stage"] = None
    _state["label"] = None
    _state["completed"] = None
    _state["total"] = None


def finish_sending() -> None:
    _state["sending"] = False
    _state["import_id"] = None
    _state["stage"] = None
    _state["label"] = None
    _state["completed"] = None
    _state["total"] = None


def update_progress(stage: str, label: str | None = None, completed: int | None = None, total: int | None = None) -> None:
    """Record the pipeline's current stage and, for item-based stages, how
    many of how many are done (e.g. stage="sending", completed=2, total=6).
    `label` is the human-readable text to show verbatim (e.g. "RBM"); binary
    stages just pass completed=total=None and rely on `stage`/`label` alone."""
    _state["stage"] = stage
    _state["label"] = label
    _state["completed"] = completed
    _state["total"] = total


def get_progress() -> dict:
    """Snapshot of the current stage/label/completed/total — safe to read
    from the UI thread while a background thread is calling
    update_progress()."""
    return dict(_state)
