"""Tracks whether the Inventory Automated Email batch is currently being
sent, plus live per-stage progress -- Version 2.0, Milestone 56, mirroring
app/send_state.py (Path Validator's identical-purpose module) exactly.

In-memory only (not persisted) -- transient UI state for the single
running app process, not data that needs to survive a restart.

Deliberately a SEPARATE state dict from Path Validator's own send_state.py
rather than sharing one: an Inventory Report upload and a Path Validator
Run Analysis can each trigger their own automatic send independently (and
potentially at the same moment), and Path Validator's own `is_sending()`
answering for an Inventory send in progress (or vice versa) would be
wrong -- these are two genuinely independent background operations, even
though they share the exact same tracking SHAPE.

No `import_id`-equivalent key: Path Validator's send_state tracks WHICH
import session is being sent because multiple import sessions can exist;
Inventory has no analogous multi-session concept -- there is only ever
one "the current Inventory Replenishment report is being sent right now"
in flight at a time, so `is_sending()` takes no argument."""

_state = {
    "sending": False,
    "stage": None,
    "label": None,
    "completed": None,
    "total": None,
}


def is_sending() -> bool:
    return _state["sending"]


def start_sending() -> None:
    _state["sending"] = True
    _state["stage"] = None
    _state["label"] = None
    _state["completed"] = None
    _state["total"] = None


def finish_sending() -> None:
    _state["sending"] = False
    _state["stage"] = None
    _state["label"] = None
    _state["completed"] = None
    _state["total"] = None


def update_progress(stage: str, label: str | None = None, completed: int | None = None, total: int | None = None) -> None:
    """Record the pipeline's current stage and, for item-based stages, how
    many of how many are done (e.g. stage="sending", completed=2, total=6).
    Mirrors app.send_state.update_progress()'s exact signature."""
    _state["stage"] = stage
    _state["label"] = label
    _state["completed"] = completed
    _state["total"] = total


def get_progress() -> dict:
    """Snapshot of the current stage/label/completed/total -- safe to read
    from the UI thread while a background thread is calling
    update_progress()."""
    return dict(_state)
