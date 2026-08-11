"""Entry point for the Saffron Automation desktop application."""

import sys

import customtkinter as ctk
from loguru import logger

from app.feature_flags_service import ensure_flag_defaults
from app.inventory_parameters_service import ensure_defaults as ensure_inventory_parameter_defaults
from app.logging_config import configure_logging
from app.manager_work_allocation_parameters_service import (
    ensure_defaults as ensure_manager_work_allocation_parameter_defaults,
)
from app.payment_parameters_service import ensure_defaults as ensure_payment_parameter_defaults
from app.rule_parameters import ensure_defaults
from app.work_distribution_parameters_service import ensure_defaults as ensure_work_distribution_parameter_defaults
from app.supabase_client import log_config_status
from database.connection import init_db
from database.migrations import run_startup_migrations
from ui.main_window import MainWindow

APP_USER_MODEL_ID = "SaffronFormulations.SaffronAutomation"


def _set_windows_app_id() -> None:
    """Give the process its own Application User Model ID on Windows, so
    the taskbar shows our icon/grouping instead of falling back to the
    Python interpreter's — harmless no-op on any other OS or if it fails."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _fatal_startup_error(message: str) -> None:
    """Show a clear, native message box and exit -- used for startup
    failures that happen before (or instead of) any Tk window can appear, so
    the user gets an explanation rather than the app silently vanishing or a
    raw traceback with no console to show it in (see app/logging_config.py's
    docstring for why a windowed exe has no console at all)."""
    logger.error(f"Fatal startup error: {message}")
    if sys.platform == "win32":
        try:
            import ctypes

            MB_ICONERROR = 0x10
            ctypes.windll.user32.MessageBoxW(None, message, "Saffron Automation - Startup Error", MB_ICONERROR)
        except Exception:
            pass
    else:
        print(f"Saffron Automation - Startup Error: {message}")
    sys.exit(1)


def main() -> None:
    # configure_logging() is already internally defensive (see
    # app/logging_config.py), but logging must never be the reason this
    # application fails to start, so the call itself is also guarded here.
    try:
        configure_logging()
    except Exception as exc:
        print(f"Warning: logging setup failed, continuing without it: {exc}")

    # app/config.py resolves (and probes) the writable data directory at
    # import time; DATA_DIR_ERROR is set only if nothing on the machine was
    # writable at all (see _resolve_writable_data_dir).
    from app.config import DATA_DIR_ERROR

    if DATA_DIR_ERROR:
        _fatal_startup_error(
            "Saffron Automation could not find a writable location to store its "
            f"settings and data.\n\n{DATA_DIR_ERROR}\n\n"
            "Please check your Windows user permissions, or contact IT support."
        )
        return

    logger.info("Starting Saffron Automation")
    log_config_status()
    _set_windows_app_id()

    # Locked to Light: the enterprise theme is a deliberate white/light-gray
    # look (see ui/theme.py), not meant to adapt to system dark mode.
    ctk.set_appearance_mode("Light")
    ctk.set_default_color_theme("blue")

    try:
        init_db()
        run_startup_migrations()
        ensure_defaults()
        ensure_payment_parameter_defaults()
        ensure_inventory_parameter_defaults()
        ensure_work_distribution_parameter_defaults()
        ensure_manager_work_allocation_parameter_defaults()
        ensure_flag_defaults()
    except Exception as exc:
        _fatal_startup_error(
            "Saffron Automation could not open its database.\n\n"
            f"{exc!r}\n\n"
            "This usually means the application's data folder is not writable. "
            "Please check your Windows user permissions, or contact IT support."
        )
        return

    app = MainWindow()
    app.mainloop()

    logger.info("Saffron Automation closed")


if __name__ == "__main__":
    main()
