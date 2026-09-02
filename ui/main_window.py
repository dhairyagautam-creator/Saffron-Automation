"""Top-level application window for Saffron Automation.

Owns only app-wide, startup-level concerns: window title/geometry/icon, the
global Tk exception safety net, the first-run Setup Wizard gate, and a
simple screen registry (Login / Home / Path Validator / Inventory
Monitoring / Payment Analytics) swapped via tkraise() — the same stacking
pattern already used for pages inside a module. Login is just another
screen in this registry (see ui/login_page.py) so future screens (splash,
loading, role-based navigation) can be added the same way, without a
separate window or any restructuring. Everything module-specific (the
Validator's sidebar, Developer Mode banner/badge, etc.) lives in
ui/path_validator_module.py.
"""

import threading
from tkinter import messagebox

import customtkinter as ctk
from loguru import logger

from app import auth_service, module_registry, permissions, rbac_state
from app.app_state_service import is_setup_completed
from app.updater import check_for_updates
from app.version import APP_VERSION, CHANNEL
from ui.home_page import HomePage
from ui.inventory_module import InventoryModule
from ui.login_page import LoginPage
from ui.path_validator_module import PathValidatorModule
from ui.payment_analytics_module import PaymentAnalyticsModule
from ui.review_system_module import ReviewSystemModule
from ui.setup_wizard import SetupWizard
from ui.theme import APP_ICON, Color
from ui.update_notification_dialog import UpdateNotificationDialog
from ui.user_management_page import UserManagementPage
from ui.work_distribution_module import WorkDistributionModule

# Screens gated by a module permission -- checked centrally in
# show_screen() so access is enforced the same way regardless of how a
# screen change was triggered (Home's cards, or any other future caller),
# not just by hiding the entry point. Screens not listed here (Login,
# Home) are unrestricted. Built FROM app.module_registry -- the single
# source of truth for every permission-gated module -- rather than its own
# hand-maintained dict, so a module registered there is automatically
# gated here too with no edit to this file (see that module's own
# docstring for why this consolidation happened).
_MODULE_PERMISSION_CHECKS = {
    module.screen_name: (lambda module=module: permissions.can_access(module.key))
    for module in module_registry.all_modules()
}


class MainWindow(ctk.CTk):
    """Application root: a screen registry (Login / Home / Path Validator /
    Inventory Monitoring / Payment Analytics) shown one at a time via
    tkraise()."""

    def __init__(self) -> None:
        super().__init__()

        # Production's title is left exactly as it's always been -- only a
        # non-production build identifies itself in the title bar, so this
        # is purely additive for Development and a no-op for Production.
        if CHANNEL == "development":
            self.title(f"Saffron Automation — v{APP_VERSION} (Development)")
        else:
            self.title("Saffron Automation")
        self._apply_responsive_geometry()
        self.configure(fg_color=Color.SURFACE)

        if APP_ICON.exists():
            try:
                self.iconbitmap(str(APP_ICON))
            except Exception:
                pass  # icon is cosmetic only; never block startup on it

        self.screens: dict[str, ctk.CTkFrame] = {}
        self.active_screen: str | None = None

        self._build_screens()
        self.show_screen("Login")

        # Global safety net: any exception raised inside a Tkinter widget
        # callback normally falls through to Tkinter's default
        # report_callback_exception, which prints to sys.stderr — None in the
        # windowed build, which would silently kill the app. This logs the
        # traceback and shows a friendly dialog instead, never touching
        # sys.stderr directly.
        self.report_callback_exception = self._report_callback_exception

        if not is_setup_completed():
            self.withdraw()  # hide the main window until setup finishes
            SetupWizard(self, self._on_setup_wizard_finished)

        # Check for updates in the background after the UI is ready.
        # Use after_idle to schedule it after the main loop starts, so it doesn't
        # block the window from appearing.
        self.after_idle(self._check_for_updates_in_background)

    # --- Responsive window sizing ------------------------------------------

    def _apply_responsive_geometry(self) -> None:
        """Size the main window relative to the actual display so it never
        opens larger than the screen.

        The window used to open at a fixed 1180x760. CustomTkinter multiplies
        every geometry by the display's DPI scaling factor (125%/150% Windows
        scaling), so on a smaller screen — or a machine using display scaling
        different from the development laptop — that fixed size exceeded the
        physical screen and clipped the bottom/right of every page (and, in
        turn, squeezed fixed-width row controls like the Organization Data
        Browse button out of view). Here the preferred size is capped to a
        fraction of the screen, computed in CustomTkinter's own logical
        (pre-scaling) units — via _reverse_window_scaling — so the DPI
        multiplication lands the window back inside the display on every
        machine. The window stays fully resizable with a sensible minimum, and
        anything that still can't fit scrolls rather than clipping (scrollable
        pages + table scrollbars).
        """
        preferred_w, preferred_h = 1180, 760
        try:
            # Screen size expressed in CustomTkinter's logical units (physical
            # screen pixels divided back out by the DPI scaling factor), which
            # is the unit geometry()/minsize() expect before CTk re-applies
            # scaling.
            screen_w = int(self._reverse_window_scaling(self.winfo_screenwidth()))
            screen_h = int(self._reverse_window_scaling(self.winfo_screenheight()))
            win_w = min(preferred_w, int(screen_w * 0.92))
            win_h = min(preferred_h, int(screen_h * 0.88))
            self.geometry(f"{win_w}x{win_h}")
            # Never let the minimum exceed the window itself on a small display.
            self.minsize(min(900, win_w), min(600, win_h))
        except Exception:
            # Window sizing must never block startup — fall back to the
            # previous fixed size if scaling detection ever fails.
            self.geometry(f"{preferred_w}x{preferred_h}")
            self.minsize(900, 600)
        self.resizable(True, True)

    # --- Screen construction -----------------------------------------------

    def _build_screens(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.screens["Login"] = LoginPage(
            self, on_login_success=lambda: self.show_screen("Home")
        )
        self.screens["Home"] = HomePage(
            self,
            on_open_module=self.show_screen,
            on_logout=self._handle_logout,
        )
        self.screens["Path Validator"] = PathValidatorModule(
            self, on_home=lambda: self.show_screen("Home")
        )
        self.screens["Inventory Monitoring"] = InventoryModule(
            self, on_home=lambda: self.show_screen("Home")
        )
        self.screens["Payment Analytics"] = PaymentAnalyticsModule(
            self, on_home=lambda: self.show_screen("Home")
        )
        self.screens["Work Distribution"] = WorkDistributionModule(
            self, on_home=lambda: self.show_screen("Home")
        )
        self.screens["User Management"] = UserManagementPage(
            self, on_home=lambda: self.show_screen("Home")
        )
        self.screens["Review System"] = ReviewSystemModule(
            self, on_home=lambda: self.show_screen("Home")
        )

        for screen in self.screens.values():
            screen.grid(row=0, column=0, sticky="nsew")

    # --- Navigation ----------------------------------------------------------

    def show_screen(self, name: str) -> None:
        if name not in self.screens:
            name = "Home"

        permission_check = _MODULE_PERMISSION_CHECKS.get(name)
        if permission_check is not None and not permission_check():
            logger.warning(f"Access denied: current user's role does not grant access to {name!r}.")
            messagebox.showerror("Access Denied", f"You do not have permission to access {name}.")
            return  # stays on whatever screen is currently active

        screen = self.screens[name]
        if hasattr(screen, "on_show"):
            screen.on_show()
        screen.tkraise()
        self.active_screen = name

    # --- Misc ------------------------------------------------------------

    def _handle_logout(self) -> None:
        """Clears the Supabase session (local storage always, server-side
        best-effort -- see auth_service.sign_out()) and returns to Login.
        Runs off the UI thread since it makes a network call."""
        def worker() -> None:
            auth_service.sign_out()
            rbac_state.clear_current_profile()
            self.after(0, lambda: self.show_screen("Login"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_setup_wizard_finished(self) -> None:
        self.deiconify()

    def _check_for_updates_in_background(self) -> None:
        """Check for updates in a background thread to avoid blocking the UI."""
        def check_updates():
            try:
                status = check_for_updates()
                if status.available and status.release_info:
                    # Schedule the dialog to show on the main thread.
                    self.after(0, self._show_update_notification, status)
            except Exception as e:
                logger.warning(f"Error during background update check: {e}")

        thread = threading.Thread(target=check_updates, daemon=True)
        thread.start()

    def _show_update_notification(self, status) -> None:
        """Show the update notification dialog and handle the user's choice."""
        if not status.release_info:
            return

        dialog = UpdateNotificationDialog(
            self,
            current_version=status.current_version,
            new_version=status.latest_version,
            release_url=status.release_info.release_url,
        )
        self.wait_window(dialog)

        if dialog.result == "install":
            import sys

            if sys.platform != "win32":
                # macOS/Linux: the release ships a .dmg, not a runnable Windows
                # installer, and app/updater.launch_installer refuses to Popen a
                # .exe off-Windows. Direct the user to the platform artifact.
                import webbrowser

                logger.info("Update install requested off-Windows; directing user to the macOS artifact")
                messagebox.showinfo(
                    "Install the Update",
                    "Automatic install is available on Windows only.\n\n"
                    f"To update to version {status.latest_version}, download the "
                    "macOS package (.dmg) from the release page and drag Saffron "
                    "Automation into your Applications folder.",
                )
                try:
                    webbrowser.open(status.release_info.release_url)
                except Exception:
                    pass
                return

            logger.info("User chose to install update immediately")
            from app.updater import perform_update
            if perform_update(status.release_info):
                logger.info("Installer launched successfully; user can install when ready")
                # Don't block -- the user can continue using the app while the
                # installer is downloading/preparing. They'll restart after install.
            else:
                messagebox.showerror(
                    "Update Failed",
                    "Failed to download and launch the installer.\n\n"
                    "Please check your internet connection and try again.",
                )
        elif dialog.result == "skip":
            logger.info("User deferred update installation")

    def _report_callback_exception(self, exc, val, tb) -> None:
        logger.opt(exception=(exc, val, tb)).error("Unhandled UI exception")
        try:
            messagebox.showerror(
                "Saffron Automation — Unexpected Error",
                "Something went wrong, but the application will keep running.\n\n"
                f"{val}",
            )
        except Exception:
            pass  # showing the dialog itself must never crash the app either
