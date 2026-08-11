"""The Path Validator module: branded sidebar navigation plus page content.

Also owns the User Mode / Developer Mode shell — the amber banner, the
"Developer Mode" badge, and the conditional Developer page — and rebuilds
itself when the mode (or a feature flag) changes so every mode-gated
section re-evaluates. See app/mode_state.py for the Developer Mode design.

This is the entire Saffron Validator experience, unchanged, now embedded as
one module inside the larger Saffron Automation shell (ui/main_window.py)
rather than being the application's root window itself.
"""

from tkinter import messagebox

import customtkinter as ctk
from loguru import logger
from PIL import Image

from app import module_sync_poller
from app.mode_state import is_developer_mode, enter_developer_mode, exit_developer_mode
from app.path_validator_refresh import MODULE_KEY as PATH_VALIDATOR_MODULE_KEY
from ui.about_page import AboutPage
from ui.developer_page import DeveloperPage
from ui.email_center_page import EmailCenterPage
from ui.findings_page import FindingsPage
from ui.icons import get_icon
from ui.master_page import MasterPage
from ui.operations_page import OperationsPage
from ui.organization_data_page import OrganizationDataPage
from ui.parameters_page import ParametersPage
from ui.settings_page import SettingsPage
from ui.components import ModuleRefreshControl, SecondaryButton
from ui.theme import LOGO_PNG, Color, Font, Spacing

# Pages shown in every mode, in sidebar order. The Developer page is
# inserted (before About) only while Developer Mode is active — see
# _page_order().
BASE_PAGES = (
    "Master",
    "Operations",
    "Findings",
    "Organization Data",
    "Email Center",
    "Parameters",
    "Settings",
    "About",
)

DEV_BANNER_TEXT = "  DEVELOPER MODE  —  Experimental Mode. Changes do not affect Production until published."


class PathValidatorModule(ctk.CTkFrame):
    """The Path Validator module: an optional Developer-Mode banner, a
    branded left sidebar, and a page content area. Rebuilds its shell on
    mode change. Embedded as a screen inside MainWindow's screen registry."""

    def __init__(self, master, on_home) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._on_home = on_home

        self.pages: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.active_page: str | None = None

        self._banner: ctk.CTkFrame | None = None
        self._body: ctk.CTkFrame | None = None

        self._build_shell()
        self.show_page("Master")
        module_sync_poller.start(self, PATH_VALIDATOR_MODULE_KEY)

    # --- Re-entry from Home ------------------------------------------------

    def on_show(self) -> None:
        """Called by MainWindow.show_screen() whenever the user re-enters
        this module from Home — refreshes whichever page was last active via
        its own existing on_show() hook, exactly like clicking its nav
        button again would."""
        self.show_page(self.active_page or "Master")

    # --- Mode switching --------------------------------------------------

    def set_mode(self, developer: bool) -> None:
        """Enter or leave Developer Mode and rebuild the shell so the banner,
        badge, Developer page, and every mode-gated section update at once."""
        if developer:
            enter_developer_mode()
        else:
            exit_developer_mode()
        self.rebuild_shell()

    def rebuild_shell(self) -> None:
        """Tear down and rebuild the banner + sidebar + pages. Used on mode
        change and on a feature-flag change (so gated sections re-evaluate).
        Rare operation — pages reload their own data via on_show, so a full
        rebuild is safe and keeps everything consistent."""
        previous = self.active_page
        self._build_shell()
        target = previous if previous in self.pages else "Master"
        self.show_page(target)

    def _page_order(self) -> tuple[str, ...]:
        if is_developer_mode():
            # Developer page sits just before About.
            return BASE_PAGES[:-1] + ("Developer", "About")
        return BASE_PAGES

    # --- Shell construction ----------------------------------------------

    def _build_shell(self) -> None:
        if self._banner is not None:
            self._banner.destroy()
            self._banner = None
        if self._body is not None:
            self._body.destroy()
        self.pages.clear()
        self.nav_buttons.clear()
        self.active_page = None

        if is_developer_mode():
            self._banner = ctk.CTkFrame(self, fg_color=Color.PRIMARY, corner_radius=0, height=34)
            self._banner.pack(side="top", fill="x")
            self._banner.pack_propagate(False)
            ctk.CTkLabel(
                self._banner,
                text=DEV_BANNER_TEXT,
                font=Font.SMALL_BOLD,
                text_color=Color.TEXT_ON_PRIMARY,
                anchor="w",
            ).pack(side="left", padx=Spacing.MD)

        self._body = ctk.CTkFrame(self, fg_color=Color.SURFACE, corner_radius=0)
        self._body.pack(side="top", fill="both", expand=True)
        self._body.grid_rowconfigure(0, weight=1)
        self._body.grid_columnconfigure(1, weight=1)

        self._build_sidebar(self._body)
        self._build_pages(self._body)

    def _build_sidebar(self, parent) -> None:
        sidebar = ctk.CTkFrame(parent, width=220, corner_radius=0, fg_color=Color.SIDEBAR_BG)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)

        header = ctk.CTkFrame(sidebar, fg_color="transparent")
        header.pack(fill="x", pady=(24, 20), padx=20)

        if LOGO_PNG.exists():
            logo_image = ctk.CTkImage(light_image=Image.open(LOGO_PNG), size=(36, 36))
            ctk.CTkLabel(header, image=logo_image, text="").pack(side="left", padx=(0, 10))

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left")
        ctk.CTkLabel(
            title_box, text="Saffron", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box, text="Path Validator", font=Font.SMALL, text_color=Color.TEXT_SECONDARY, anchor="w"
        ).pack(anchor="w")

        if is_developer_mode():
            ctk.CTkLabel(
                sidebar,
                text="  DEVELOPER MODE  ",
                font=Font.SMALL_BOLD,
                text_color=Color.TEXT_ON_PRIMARY,
                fg_color=Color.PRIMARY,
                corner_radius=6,
            ).pack(pady=(0, 8))

        ctk.CTkFrame(sidebar, fg_color=Color.DIVIDER, height=1).pack(fill="x", padx=16, pady=(0, 8))

        self._build_refresh_control(sidebar)
        ctk.CTkFrame(sidebar, fg_color=Color.DIVIDER, height=1).pack(fill="x", padx=16, pady=(8, 8))

        nav_container = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav_container.pack(fill="x", padx=12)

        for name in self._page_order():
            button = ctk.CTkButton(
                nav_container,
                text=f"   {name}",
                image=get_icon(name, size=18, color=Color.SIDEBAR_TEXT),
                anchor="w",
                compound="left",
                font=Font.BODY,
                fg_color="transparent",
                hover_color=Color.SIDEBAR_HOVER,
                text_color=Color.SIDEBAR_TEXT,
                corner_radius=8,
                height=40,
                command=lambda n=name: self.show_page(n),
            )
            button.pack(fill="x", pady=2)
            self.nav_buttons[name] = button

        SecondaryButton(
            sidebar, text="← Back to Home", command=self._on_home
        ).pack(fill="x", padx=12, pady=Spacing.MD, side="bottom")
        ctk.CTkFrame(sidebar, fg_color=Color.DIVIDER, height=1).pack(fill="x", padx=16, pady=(8, 0), side="bottom")

    def _build_refresh_control(self, sidebar) -> None:
        """The single, module-wide "Refresh" action: one button + status
        label, built once as part of the sidebar shell (not per-page), so
        it stays in the exact same place regardless of which Path
        Validator page is showing. Uses the shared, reusable
        ui.components.ModuleRefreshControl (Milestone 22) -- the same
        component Inventory/Payments use for their own module-wide
        Refresh, so this button's behavior isn't reimplemented per
        module."""
        self.refresh_control = ModuleRefreshControl(sidebar, module_shell=self, module_key=PATH_VALIDATOR_MODULE_KEY)
        self.refresh_control.pack(fill="x", padx=12)

    def _build_pages(self, parent) -> None:
        container = ctk.CTkFrame(parent, fg_color=Color.SURFACE)
        container.grid(row=0, column=1, sticky="nsew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.pages["Master"] = MasterPage(container)
        self.pages["Operations"] = OperationsPage(container)
        self.pages["Findings"] = FindingsPage(container)
        self.pages["Organization Data"] = OrganizationDataPage(container)
        self.pages["Email Center"] = EmailCenterPage(container)
        self.pages["Parameters"] = ParametersPage(container)
        self.pages["Settings"] = SettingsPage(container, main_window=self)
        self.pages["About"] = AboutPage(container)
        if is_developer_mode():
            self.pages["Developer"] = DeveloperPage(container, main_window=self)

        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")

    # --- Navigation ------------------------------------------------------

    def show_page(self, name: str) -> None:
        if name not in self.pages:
            name = "Master"
        if self.active_page is not None and self.active_page in self.nav_buttons:
            self._style_nav_button(self.active_page, active=False)

        page = self.pages[name]
        if hasattr(page, "on_show"):
            page.on_show()
        page.tkraise()

        self._style_nav_button(name, active=True)
        self.active_page = name

    def _style_nav_button(self, name: str, active: bool) -> None:
        button = self.nav_buttons.get(name)
        if button is None:
            return
        if active:
            button.configure(
                fg_color=Color.SIDEBAR_ACTIVE,
                text_color=Color.SIDEBAR_TEXT_ACTIVE,
                image=get_icon(name, size=18, color=Color.SIDEBAR_TEXT_ACTIVE),
                font=Font.BODY_BOLD,
            )
        else:
            button.configure(
                fg_color="transparent",
                text_color=Color.SIDEBAR_TEXT,
                image=get_icon(name, size=18, color=Color.SIDEBAR_TEXT),
                font=Font.BODY,
            )
