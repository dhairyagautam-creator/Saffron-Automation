"""Inventory Thresholds page: search + filters over the real generated
threshold data (see app/threshold_service.py). Reloads from the database
every time this page becomes visible (on_show), matching the same
convention already used by ui/findings_page.py and others -- there is no
caching anywhere in this page; every visit re-queries InventoryThreshold
fresh via get_all_thresholds().

Export to Excel (Milestone 55 -- Table Export Framework rollout): follows
the Replenishment page's reference pattern exactly (see
app.table_export_service's own docstring and ui/inventory_replenishment_page.py).
`self._current_display_rows` is set inside `_render_table()` to the exact
post-filter/search list already used to populate the Treeview.
"""

import customtkinter as ctk
from loguru import logger

from app.table_export_service import default_export_filename, export_rows_with_ui
from app.threshold_service import get_all_thresholds
from ui.components import Card, EmptyState, PrimaryButton, SectionHeader, styled_treeview
from ui.icons import get_icon
from ui.theme import Color, Font, Spacing

COLUMNS = ("branch", "division", "item_name", "packing", "previous_month_sales", "threshold_display", "last_updated")
HEADINGS = {
    "branch": "CFA",
    "division": "Division",
    "item_name": "Item Name",
    "packing": "Packing",
    "previous_month_sales": "Previous Month Sales",
    "threshold_display": "Threshold",
    "last_updated": "Last Updated",
}
WIDTHS = {
    "branch": 160,
    "division": 120,
    "item_name": 220,
    "packing": 80,
    "previous_month_sales": 150,
    "threshold_display": 140,
    "last_updated": 110,
}


class InventoryThresholdsPage(ctk.CTkFrame):
    """Search + CFA/Division filters over the generated threshold table."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)

        self._all_rows: list[dict] = []
        self._current_display_rows: list[dict] = []
        self._branch_filter = "All"
        self._division_filter = "All"

        self._build_widgets()

    def on_show(self) -> None:
        """Called every time this page becomes visible -- reload from the DB."""
        self._all_rows = get_all_thresholds()
        distinct_raw_thresholds = {round(r["raw_threshold"] / r["previous_month_sales"], 4) for r in self._all_rows if r["previous_month_sales"]}
        logger.debug(
            f"Thresholds page loaded {len(self._all_rows)} row(s) fresh from the DB; distinct "
            f"raw_threshold/previous_month_sales ratios currently present: {sorted(distinct_raw_thresholds)} "
            "(more than one value here means some rows were generated under an older CFA "
            "Threshold Multiplier and haven't been reprocessed since -- not a display bug, see "
            "app.threshold_service.generate_thresholds_from_sales's upsert-only note)"
        )
        self._refresh_filter_options()
        self._render_table()

    def _build_widgets(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Thresholds", "Replenishment thresholds by branch and item"
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

        self.search_entry = ctk.CTkEntry(filter_row, placeholder_text="Search item name…")
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, Spacing.SM))
        self.search_entry.bind("<KeyRelease>", lambda event: self._render_table())

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
            suggested_filename=default_export_filename("Thresholds", suffix=suffix),
            sheet_title="Thresholds",
        )

    def _render_table(self) -> None:
        for widget in self.table_container.winfo_children():
            widget.destroy()
        self._current_display_rows = []

        if not self._all_rows:
            EmptyState(
                self.table_container, "No thresholds generated yet — upload a Previous Month Sales Report."
            ).pack(fill="both", expand=True)
            return

        query = self.search_entry.get().strip().lower()
        rows = self._all_rows
        if self._branch_filter != "All":
            rows = [r for r in rows if r["branch"] == self._branch_filter]
        if self._division_filter != "All":
            rows = [r for r in rows if r["division"] == self._division_filter]
        if query:
            rows = [r for r in rows if query in r["item_name"].lower()]

        self._current_display_rows = rows

        if not rows:
            EmptyState(self.table_container, "No thresholds match the current filter.").pack(
                fill="both", expand=True
            )
            return

        tree = styled_treeview(self.table_container, COLUMNS, HEADINGS, WIDTHS, height=14)
        for row in rows:
            tree.insert("", "end", values=tuple(row[col] for col in COLUMNS))
        tree.pack(fill="both", expand=True)
