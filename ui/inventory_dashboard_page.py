"""Inventory Dashboard: recent uploads and real replenishment summary
KPIs (see app/replenishment_service.py).

A true dashboard — no upload controls here; those live on their own
dedicated Uploads page (ui/inventory_uploads_page.py).
"""

import customtkinter as ctk

from app.replenishment_service import get_replenishment_summary
from ui.components import Card, KPICard, SectionHeader, styled_treeview
from ui.theme import Color, Font, Spacing

KPI_SPECS = [
    ("Total Products Evaluated", Color.PRIMARY),
    ("Products Requiring Replenishment", Color.WARNING),
    ("Products with Healthy Stock", Color.SUCCESS),
]

RECENT_UPLOADS_COLUMNS = ("file_name", "type", "uploaded_at")
RECENT_UPLOADS_HEADINGS = {"file_name": "File", "type": "Type", "uploaded_at": "Uploaded At"}
RECENT_UPLOADS_WIDTHS = {"file_name": 280, "type": 180, "uploaded_at": 160}
RECENT_UPLOADS_ROWS = [
    ("Inventory_Report_June2026.xlsx", "Inventory Report", "2026-07-01 09:14"),
    ("Sales_Report_June2026.xlsx", "Previous Month Sales", "2026-07-01 09:16"),
    ("Inventory_Report_May2026.xlsx", "Inventory Report", "2026-06-01 10:02"),
    ("Sales_Report_May2026.xlsx", "Previous Month Sales", "2026-06-01 10:05"),
]


class InventoryDashboardPage(ctk.CTkFrame):
    """Landing page of the Inventory Monitoring module."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self.kpi_cards: dict[str, KPICard] = {}
        self._build_widgets()

    def on_show(self) -> None:
        """Called every time this page becomes visible -- reload from the DB."""
        summary = get_replenishment_summary()
        self.kpi_cards["Total Products Evaluated"].set_value(f"{summary['total_evaluated']:,}")
        self.kpi_cards["Products Requiring Replenishment"].set_value(f"{summary['requiring_replenishment']:,}")
        self.kpi_cards["Products with Healthy Stock"].set_value(f"{summary['healthy']:,}")

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer,
            "Inventory Dashboard",
            "Monitor inventory status across branches",
        ).pack(anchor="w", pady=(0, Spacing.LG))

        kpi_grid = ctk.CTkFrame(outer, fg_color="transparent")
        kpi_grid.pack(fill="x", pady=(0, Spacing.LG))
        for i in range(len(KPI_SPECS)):
            kpi_grid.grid_columnconfigure(i, weight=1)
        for i, (label, accent) in enumerate(KPI_SPECS):
            card = KPICard(kpi_grid, label=label, value="—", accent=accent)
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else Spacing.SM, 0))
            self.kpi_cards[label] = card

        recent_card = Card(outer)
        recent_card.pack(fill="x", pady=(0, Spacing.LG))
        recent_body = ctk.CTkFrame(recent_card, fg_color="transparent")
        recent_body.pack(fill="both", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            recent_body, text="Recent Uploads", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        tree = styled_treeview(
            recent_body, RECENT_UPLOADS_COLUMNS, RECENT_UPLOADS_HEADINGS, RECENT_UPLOADS_WIDTHS,
            height=4, scrollbars=False,
        )
        for row in RECENT_UPLOADS_ROWS:
            tree.insert("", "end", values=row)
        tree.pack(fill="x")
