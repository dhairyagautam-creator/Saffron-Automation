"""The Path Validator module: branded sidebar navigation plus page content.

This is the entire Saffron Validator experience, unchanged, now embedded as
one module inside the larger Saffron Automation shell (ui/main_window.py)
rather than being the application's root window itself.
"""

from tkinter import messagebox

import customtkinter as ctk
from loguru import logger
from PIL import Image

from ui.about_page import AboutPage
from ui.email_center_page import EmailCenterPage
from ui.findings_page import FindingsPage
from ui.icons import get_icon
from ui.master_page import MasterPage
from ui.operations_page import OperationsPage
from ui.organization_data_page import OrganizationDataPage
from ui.parameters_page import ParametersPage
from ui.settings_page import SettingsPage
from ui.components import SecondaryButton
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

class PathValidatorModule(ctk.CTkFrame):
    """The Path Validator module: a branded left sidebar and a page content
    area. Embedded as a screen inside MainWindow's screen registry."""

    def __init__(self, master, on_home) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._on_home = on_home

        self.pages: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.active_page: str | None = None

        self._body: ctk.CTkFrame | None = None

        self._build_shell()
        self.show_page("Master")

    # --- Re-entry from Home ------------------------------------------------

    def on_show(self) -> None:
        """Called by MainWindow.show_screen() whenever the user re-enters
        this module from Home — refreshes whichever page was last active via
        its own existing on_show() hook, exactly like clicking its nav
        button again would."""
        self.show_page(self.active_page or "Master")


    # --- Shell construction ----------------------------------------------

    def _build_shell(self) -> None:
        if self._body is not None:
            self._body.destroy()
        self.pages.clear()
        self.nav_buttons.clear()
        self.active_page = None

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

        ctk.CTkFrame(sidebar, fg_color=Color.DIVIDER, height=1).pack(fill="x", padx=16, pady=(0, 8))

        nav_container = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav_container.pack(fill="x", padx=12)

        for name in BASE_PAGES:
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
