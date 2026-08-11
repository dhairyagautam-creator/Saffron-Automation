"""Excess Inventory page: Division/CFA filters + unified Search Product
(SKU code OR SKU name) over the CFA-SKU combinations currently exceeding
their own replenishment threshold (see app/excess_inventory_service.py).
Same page shape as ui/inventory_replenishment_page.py and
ui/inventory_cwh_page.py -- filter_row (menus + search + Export), a
styled_treeview table, EmptyState for the no-data/no-match cases. Reloads
from the database every time this page becomes visible (on_show), same
convention as every other Inventory page.

Transfer Candidate row highlight: when a row's `transfer_candidate` flag
(Current Stock >= the configurable Transfer Candidate Multiplier x
Previous Month Sales -- see app/excess_inventory_service.py and the
Excess Inventory Settings card on ui/inventory_settings_page.py) is True,
the ENTIRE row is highlighted green via Color.SUCCESS_SOFT -- the same
background token ui/inventory_cwh_page.py's own "Healthy" status row
already uses, not a new color invented for this page. Purely a visual
flag -- it does not change excess_quantity, status text, or any
underlying value.

Export to Excel: follows the Replenishment/Thresholds pages' reference
pattern exactly (app.table_export_service). `self._current_display_rows`
is set inside `_render_table()` to the exact post-filter/search list
already used to populate the Treeview, so the exported workbook always
matches the current on-screen view. The Transfer Candidate green
highlight is mirrored into the exported cells via
_excess_inventory_row_style(), same as Replenishment's CWH-shortage red.
"""

import customtkinter as ctk

from app.excess_inventory_service import (
    EXCESS_INVENTORY_REPORT_COLUMNS as COLUMNS,
    EXCESS_INVENTORY_REPORT_HEADINGS as HEADINGS,
    get_excess_inventory,
)
from app.replenishment_service import get_replenishment_summary
from app.table_export_service import RowStyle, default_export_filename, export_rows_with_ui
from ui.components import Card, EmptyState, PrimaryButton, SectionHeader, styled_treeview
from ui.icons import get_icon
from ui.theme import Color, Font, Spacing

WIDTHS = {
    "division": 110,
    "branch": 160,
    "item_code": 100,
    "item_name": 220,
    "effective_stock": 110,
    "previous_month_sales_display": 160,
    "threshold_display": 140,
    "excess_quantity_display": 130,
    "excess_percent_display": 100,
    "status": 150,
}


def _excess_inventory_row_style(row: dict) -> RowStyle | None:
    """Mirrors this page's own on-screen Transfer Candidate green row
    highlight into the exported row's cell fill, so a downloaded workbook
    visually matches what the user was looking at."""
    if row.get("transfer_candidate"):
        return RowStyle(fill_color=Color.SUCCESS_SOFT.lstrip("#"))
    return None


class InventoryExcessPage(ctk.CTkFrame):
    """CFA/Division filters + unified SKU search over the Excess Inventory
    table (app/excess_inventory_service.get_excess_inventory)."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)

        self._all_rows: list[dict] = []
        self._current_display_rows: list[dict] = []
        self._total_evaluated = 0
        self._branch_filter = "All"
        self._division_filter = "All"

        self._build_widgets()

    def on_show(self) -> None:
        """Called every time this page becomes visible -- reload from the DB."""
        self._all_rows = get_excess_inventory()
        self._total_evaluated = get_replenishment_summary()["total_evaluated"]
        self._refresh_filter_options()
        self._render_table()

    def _build_widgets(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Excess Inventory", "CFA-SKU combinations currently exceeding their replenishment threshold"
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
        self.branch_menu = ctk.CTkOptionMenu(
            filter_row,
            values=["All"],
            command=self._on_branch_changed,
            fg_color=Color.SURFACE,
            button_color=Color.PRIMARY,
            button_hover_color=Color.PRIMARY_HOVER,
            text_color=Color.TEXT_PRIMARY,
            width=170,
        )
        self.branch_menu.pack(side="left", padx=(0, Spacing.SM))

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
        self.division_menu.pack(side="left", padx=(0, Spacing.MD))

        # Unified search -- matches EITHER SKU Code or SKU Name, so the
        # user never has to pick which field they meant (same convention
        # as Replenishment's/CWH's own single product_query filter).
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
        branch_options = ["All"] + sorted({row["branch"] for row in self._all_rows})
        division_options = ["All"] + sorted({row["division"] for row in self._all_rows if row["division"]})

        current_branch = self._branch_filter if self._branch_filter in branch_options else "All"
        current_division = self._division_filter if self._division_filter in division_options else "All"
        self._branch_filter = current_branch
        self._division_filter = current_division

        self.branch_menu.configure(values=branch_options)
        self.branch_menu.set(current_branch)
        self.division_menu.configure(values=division_options)
        self.division_menu.set(current_division)

    def _on_branch_changed(self, value: str) -> None:
        self._branch_filter = value
        self._render_table()

    def _on_division_changed(self, value: str) -> None:
        self._division_filter = value
        self._render_table()

    def _on_export_clicked(self) -> None:
        """Exports `self._current_display_rows` -- the exact list
        `_render_table()` just used to populate the Treeview (see
        app.table_export_service's module docstring / the Replenishment
        page's reference implementation)."""
        suffix = "_".join(
            value for value in (self._branch_filter, self._division_filter) if value != "All"
        )
        export_rows_with_ui(
            self,
            rows=self._current_display_rows,
            columns=COLUMNS,
            headings=HEADINGS,
            suggested_filename=default_export_filename("ExcessInventory", suffix=suffix),
            sheet_title="Excess Inventory",
            row_style_fn=_excess_inventory_row_style,
        )

    def _render_table(self) -> None:
        for widget in self.table_container.winfo_children():
            widget.destroy()
        self._current_display_rows = []

        if self._total_evaluated == 0:
            EmptyState(
                self.table_container, "Upload an Inventory Report to evaluate excess stock."
            ).pack(fill="both", expand=True)
            return

        if not self._all_rows:
            EmptyState(self.table_container, "No products currently exceed their threshold.").pack(
                fill="both", expand=True
            )
            return

        product_query = self.product_search_entry.get().strip().lower()

        rows = self._all_rows
        if self._branch_filter != "All":
            rows = [r for r in rows if r["branch"] == self._branch_filter]
        if self._division_filter != "All":
            rows = [r for r in rows if r["division"] == self._division_filter]
        if product_query:
            # Unified search -- matches SKU Code OR SKU Name, applied on
            # top of the branch/division filters above.
            rows = [
                r for r in rows
                if product_query in (r["item_code"] or "").lower() or product_query in r["item_name"].lower()
            ]

        # Captured here, BEFORE the "no match" empty-state check below, so
        # Export always reflects reality (see app.table_export_service's
        # own module docstring / the Replenishment page's identical rule).
        self._current_display_rows = rows

        if not rows:
            EmptyState(self.table_container, "No products match the current filter.").pack(
                fill="both", expand=True
            )
            return

        tree = styled_treeview(self.table_container, COLUMNS, HEADINGS, WIDTHS, height=14)
        tree.tag_configure("transfer_candidate", background=Color.SUCCESS_SOFT)
        for row in rows:
            tree.insert(
                "", "end",
                tags=("transfer_candidate",) if row["transfer_candidate"] else (),
                values=tuple(row[col] for col in COLUMNS),
            )
        tree.pack(fill="both", expand=True)
