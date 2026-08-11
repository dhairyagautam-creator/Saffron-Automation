"""The Inventory Monitoring module: branded sidebar navigation plus page
content, mirroring ui/path_validator_module.py's shell shape. No Developer
Mode concept applies here (that's Path-Validator-specific chrome, not
app-wide) — this module's sidebar is always the plain style, and the shell
is built exactly once since there is no mode to rebuild for.
"""

import customtkinter as ctk
from PIL import Image

from app.inventory_refresh import MODULE_KEY as INVENTORY_MODULE_KEY
from ui.components import ModuleRefreshControl, SecondaryButton
from ui.icons import get_icon
from ui.inventory_automated_emails_page import InventoryAutomatedEmailsPage
from ui.inventory_cwh_page import InventoryCwhPage
from ui.inventory_dashboard_page import InventoryDashboardPage
from ui.inventory_excess_page import InventoryExcessPage
from ui.inventory_replenishment_page import InventoryReplenishmentPage
from ui.inventory_settings_page import InventorySettingsPage
from ui.inventory_thresholds_page import InventoryThresholdsPage
from ui.inventory_uploads_page import InventoryUploadsPage
from ui.theme import LOGO_PNG, Color, Font, Spacing

BASE_PAGES = (
    "Dashboard",
    "Uploads",
    "Thresholds",
    "Replenishment",
    "Central Warehouse (CWH)",
    "Excess Inventory",
    "Automated Emails",
    "Settings",
)


class InventoryModule(ctk.CTkFrame):
    """The Inventory Monitoring module: a branded left sidebar and a page
    content area. Embedded as a screen inside MainWindow's screen registry."""

    def __init__(self, master, on_home) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._on_home = on_home

        self.pages: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.active_page: str | None = None

        self._build_shell()
        self.show_page("Dashboard")

    # --- Re-entry from Home ------------------------------------------------

    def on_show(self) -> None:
        """Called by MainWindow.show_screen() whenever the user re-enters
        this module from Home — refreshes whichever page was last active."""
        self.show_page(self.active_page or "Dashboard")

    # --- Shell construction ----------------------------------------------

    def _build_shell(self) -> None:
        body = ctk.CTkFrame(self, fg_color=Color.SURFACE, corner_radius=0)
        body.pack(side="top", fill="both", expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        self._build_sidebar(body)
        self._build_pages(body)

    def _build_sidebar(self, parent) -> None:
        sidebar = ctk.CTkFrame(parent, width=248, corner_radius=0, fg_color=Color.SIDEBAR_BG)
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
            title_box, text="Inventory Monitoring", font=Font.SMALL, text_color=Color.TEXT_SECONDARY, anchor="w"
        ).pack(anchor="w")

        ctk.CTkFrame(sidebar, fg_color=Color.DIVIDER, height=1).pack(fill="x", padx=16, pady=(0, 8))

        # The single, module-wide "Refresh" action -- manual only, no
        # automatic background poller for Inventory (a deliberate choice,
        # confirmed with the user; Path Validator has one via
        # app/module_sync_poller.py). Same shared component Path Validator
        # uses (see ui/path_validator_module.py) -- no duplicated button
        # logic between the two modules.
        self.refresh_control = ModuleRefreshControl(sidebar, module_shell=self, module_key=INVENTORY_MODULE_KEY)
        self.refresh_control.pack(fill="x", padx=12)
        ctk.CTkFrame(sidebar, fg_color=Color.DIVIDER, height=1).pack(fill="x", padx=16, pady=(8, 8))

        nav_container = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav_container.pack(fill="x", padx=12)

        for name in BASE_PAGES:
            # Some Inventory nav labels ("Central Warehouse (CWH)") are
            # notably longer than any Path Validator nav item, hence the
            # smaller font here (vs. Font.BODY elsewhere) so the label fits
            # this sidebar's width without truncation.
            button = ctk.CTkButton(
                nav_container,
                text=f"   {name}",
                image=get_icon(name, size=18, color=Color.SIDEBAR_TEXT),
                anchor="w",
                compound="left",
                font=Font.SMALL,
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

        self.pages["Dashboard"] = InventoryDashboardPage(container)
        self.pages["Uploads"] = InventoryUploadsPage(container)
        self.pages["Thresholds"] = InventoryThresholdsPage(container)
        self.pages["Replenishment"] = InventoryReplenishmentPage(container)
        self.pages["Central Warehouse (CWH)"] = InventoryCwhPage(container)
        self.pages["Excess Inventory"] = InventoryExcessPage(container)
        self.pages["Automated Emails"] = InventoryAutomatedEmailsPage(container)
        self.pages["Settings"] = InventorySettingsPage(container)

        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")

    # --- Navigation ------------------------------------------------------

    def show_page(self, name: str) -> None:
        if name not in self.pages:
            name = "Dashboard"
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
                font=Font.SMALL_BOLD,
            )
        else:
            button.configure(
                fg_color="transparent",
                text_color=Color.SIDEBAR_TEXT,
                image=get_icon(name, size=18, color=Color.SIDEBAR_TEXT),
                font=Font.SMALL,
            )
