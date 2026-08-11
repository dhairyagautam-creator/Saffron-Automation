"""Central Warehouse (CWH) page: Ahmedabad CWH's own dedicated warehouse
inventory dashboard -- Phase 2 of the Inventory upgrade. Phase 1 (see
app/threshold_service.is_ahmedabad_cwh_branch) excluded Ahmedabad CWH
from every CFA-level threshold/replenishment calculation; this page is
where it gets its own, separate threshold logic instead (see
app/cwh_service.py): warehouse stock compared against total demand
across every real CFA combined.

Entirely separate from the existing Thresholds/Replenishment pages and
their data (InventoryThreshold/InventoryReplenishment tables, which this
page only reads from, never writes to) -- neither of those pages nor
their behavior is touched by this one.
"""

import customtkinter as ctk

from app.cwh_service import get_cwh_overview, get_cwh_summary
from app.table_export_service import RowStyle, default_export_filename, export_rows_with_ui
from ui.components import Card, EmptyState, PrimaryButton, SectionHeader, styled_treeview
from ui.icons import get_icon
from ui.theme import Color, Font, Spacing

COLUMNS = (
    "item_name",
    "division",
    "item_code",
    "physical_stock_display",
    "total_previous_month_sales_display",
    "cwh_threshold_display",
    "surplus_deficit_display",
    "status",
)
HEADINGS = {
    "item_name": "Item Name",
    "division": "Division",
    "item_code": "SKU / Item Code",
    "physical_stock_display": "Physical Stock (CWH)",
    "total_previous_month_sales_display": "Total Prev. Month Sales (All CFAs)",
    "cwh_threshold_display": "CWH Threshold",
    "surplus_deficit_display": "Surplus / Deficit",
    "status": "Status",
}
WIDTHS = {
    "item_name": 220,
    "division": 110,
    "item_code": 110,
    "physical_stock_display": 140,
    "total_previous_month_sales_display": 200,
    "cwh_threshold_display": 120,
    "surplus_deficit_display": 130,
    "status": 150,
}

_STATUS_TAG_BG = {
    "success": Color.SUCCESS_SOFT,
    "error": Color.ERROR_SOFT,
}


def _cwh_row_style(row: dict) -> RowStyle | None:
    """Mirrors this page's own on-screen status row highlight
    (_STATUS_TAG_BG / "cwh_{kind}" Treeview tags) into the exported row's
    cell fill, so a downloaded workbook visually matches the on-screen
    Healthy/Shortage color coding."""
    bg = _STATUS_TAG_BG.get(row.get("status_kind"))
    if bg is None:
        return None
    return RowStyle(fill_color=bg.lstrip("#"))


STATUS_FILTER_OPTIONS = ("All", "Healthy", "Shortage")


class InventoryCwhPage(ctk.CTkFrame):
    """Division filter + unified Search Product (SKU code OR item name) +
    Status filter over the Ahmedabad CWH warehouse overview
    (app/cwh_service.get_cwh_overview)."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)

        self._all_rows: list[dict] = []
        self._current_display_rows: list[dict] = []
        self._has_any_data = False
        self._division_filter = "All"
        self._status_filter = "All"

        self._build_widgets()

    def on_show(self) -> None:
        """Called every time this page becomes visible -- reload from the DB."""
        self._all_rows = get_cwh_overview()
        self._has_any_data = get_cwh_summary()["has_any_data"]
        self._refresh_filter_options()
        self._render_table()

    def _build_widgets(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer,
            "Central Warehouse (CWH)",
            "Ahmedabad CWH stock vs. total demand across every CFA combined",
        ).pack(anchor="w", pady=(0, Spacing.LG))

        table_card = Card(outer)
        table_card.pack(fill="both", expand=True)

        table_body = ctk.CTkFrame(table_card, fg_color="transparent")
        table_body.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)

        filter_row = ctk.CTkFrame(table_body, fg_color="transparent")
        filter_row.pack(fill="x", pady=(0, Spacing.SM))

        ctk.CTkLabel(filter_row, text="Filter:", font=Font.BODY, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(0, Spacing.SM)
        )
        self.division_menu = ctk.CTkOptionMenu(
            filter_row,
            values=["All"],
            command=self._on_division_changed,
            fg_color=Color.SURFACE,
            button_color=Color.PRIMARY,
            button_hover_color=Color.PRIMARY_HOVER,
            text_color=Color.TEXT_PRIMARY,
            width=170,
        )
        self.division_menu.pack(side="left", padx=(0, Spacing.SM))

        self.status_menu = ctk.CTkOptionMenu(
            filter_row,
            values=list(STATUS_FILTER_OPTIONS),
            command=self._on_status_changed,
            fg_color=Color.SURFACE,
            button_color=Color.PRIMARY,
            button_hover_color=Color.PRIMARY_HOVER,
            text_color=Color.TEXT_PRIMARY,
            width=140,
        )
        self.status_menu.set("All")
        self.status_menu.pack(side="left", padx=(0, Spacing.MD))

        # Unified search -- matches EITHER SKU/Item Code or Product/Item
        # Name, so the user never has to pick which field they meant (see
        # _render_table's single `product_query` filter below).
        self.product_search_entry = ctk.CTkEntry(filter_row, placeholder_text="Search Product…")
        self.product_search_entry.pack(side="left", fill="x", expand=True, padx=(0, Spacing.SM))
        self.product_search_entry.bind("<KeyRelease>", lambda event: self._render_table())

        self.export_button = PrimaryButton(
            filter_row,
            text="Export",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_export_clicked,
        )
        self.export_button.pack(side="right")

        self.table_container = ctk.CTkFrame(table_body, fg_color="transparent")
        self.table_container.pack(fill="both", expand=True)

    def _refresh_filter_options(self) -> None:
        division_options = ["All"] + sorted({row["division"] for row in self._all_rows if row["division"]})
        current_division = self._division_filter if self._division_filter in division_options else "All"
        self._division_filter = current_division
        self.division_menu.configure(values=division_options)
        self.division_menu.set(current_division)

    def _on_division_changed(self, value: str) -> None:
        self._division_filter = value
        self._render_table()

    def _on_status_changed(self, value: str) -> None:
        self._status_filter = value
        self._render_table()

    def _on_export_clicked(self) -> None:
        """Exports `self._current_display_rows` -- the exact list
        `_render_table()` just used to populate the Treeview."""
        suffix = "" if self._division_filter == "All" else self._division_filter
        export_rows_with_ui(
            self,
            rows=self._current_display_rows,
            columns=COLUMNS,
            headings=HEADINGS,
            suggested_filename=default_export_filename("CWH", suffix=suffix),
            sheet_title="Central Warehouse",
            row_style_fn=_cwh_row_style,
        )

    def _render_table(self) -> None:
        for widget in self.table_container.winfo_children():
            widget.destroy()
        self._current_display_rows = []

        if not self._has_any_data:
            EmptyState(
                self.table_container,
                "Upload a Previous Month Sales Report and an Inventory Report first — "
                "the Central Warehouse overview is built from both.",
            ).pack(fill="both", expand=True)
            return

        product_query = self.product_search_entry.get().strip().lower()

        rows = self._all_rows
        if self._division_filter != "All":
            rows = [r for r in rows if r["division"] == self._division_filter]
        if self._status_filter != "All":
            rows = [r for r in rows if r["status"] == self._status_filter]
        if product_query:
            # Unified search -- matches SKU/Item Code OR Product/Item Name,
            # so the user doesn't need to choose which field they meant.
            rows = [
                r for r in rows
                if product_query in r["item_code"].lower() or product_query in r["item_name"].lower()
            ]

        self._current_display_rows = rows

        if not rows:
            EmptyState(self.table_container, "No items match the current filter.").pack(
                fill="both", expand=True
            )
            return

        tree = styled_treeview(self.table_container, COLUMNS, HEADINGS, WIDTHS, height=14)
        for kind, bg in _STATUS_TAG_BG.items():
            tree.tag_configure(f"cwh_{kind}", background=bg)
        for row in rows:
            tree.insert(
                "", "end",
                tags=(f"cwh_{row['status_kind']}",),
                values=tuple(row[col] for col in COLUMNS),
            )
        tree.pack(fill="both", expand=True)
