"""The single source of truth for which mode the application is in right
now — User Mode (production) or Developer Mode (testing).

Deliberately in-memory only and NOT persisted: every launch starts in User
Mode, and Developer Mode must be unlocked with the password each session
(see app/dev_auth_service.py). This is the safe default — a machine is
never accidentally left in experimental mode across restarts.

Pure leaf module: it imports nothing from the rest of the app and touches
no database, so it can be imported from anywhere (settings services, UI,
the pipeline) without any risk of a circular import. Everything that needs
to know "which environment's data do I read/write" calls
current_environment() here.
"""

from loguru import logger

USER_ENVIRONMENT = "user"
DEVELOPER_ENVIRONMENT = "developer"

_state = {"developer": False}


def is_developer_mode() -> bool:
    return _state["developer"]


def current_environment() -> str:
    """'developer' while Developer Mode is active, otherwise 'user'. Every
    mode-sensitive read/write (credentials, rule parameters, feature flags)
    is scoped to whatever this returns, so the same code path transparently
    uses the right environment's data."""
    return DEVELOPER_ENVIRONMENT if _state["developer"] else USER_ENVIRONMENT


def enter_developer_mode() -> None:
    _state["developer"] = True
    logger.info("Entered Developer Mode")


def exit_developer_mode() -> None:
    _state["developer"] = False
    logger.info("Exited Developer Mode (back to User Mode)")
