"""Inventory Replenishment page: BranchLocation/Item Group filters over
the real generated replenishment data (see app/replenishment_service.py).
Only products currently requiring replenishment are ever shown here.
Reloads from the database every time this page becomes visible
(on_show), matching the same convention already used elsewhere.

Threshold and Status columns were removed (no longer needed on this
page); Previous Month Sales was added (the same dataset threshold
generation itself reads, joined fresh on every load -- see
app.replenishment_service.get_replenishment_required). Stock Deficit is
always shown rounded UP to the nearest pack (never down -- see
app.threshold_service.format_deficit_display) and respects the same Raw
Quantity / Packs display toggle the Thresholds page's own Threshold
column already uses -- the toggle only changes how the already-rounded
number is annotated, never the underlying value.

Product Search: a single search box matches EITHER Item Code or Item
Name (the user never has to pick which field they meant), applied on top
of whatever Branch/Division filter is already active -- see
_render_table's `product_query` filter, applied last so a
Division + Search combination narrows correctly.

CWH-availability shortage highlight (Milestone 46, revised per follow-up
request): when a row's cwh_shortage flag (see
app.replenishment_service.get_replenishment_required) is True -- meaning
Ahmedabad CWH's current physical stock of that SAME item_key is less than
this row's Required Replenishment Quantity -- the ENTIRE row is
highlighted with a muted red ("cwh_shortage" tag, Color.CRITICAL_ROW_BG/
CRITICAL_ROW_TEXT), replacing the normal soft-yellow
"requires_replenishment" tag every other row on this page gets. This is a
pure visual warning: the underlying deficit value, the replenishment
decision, and every cell's actual data are completely unaffected -- only
the row's tag (and therefore its color) changes.

Originally implemented as a per-cell-only overlay (bright, saturated red)
via a tk.Label positioned over just the Deficit cell -- revised to a
plain row-level ttk.Treeview tag_configure() (the native, simpler
mechanism) with a muted, Excel "Bad"-style red after user feedback that
the per-cell bright red looked "cartoonish" and that the whole row should
be highlighted instead.

Export to Excel (Milestone 54): this page is the REFERENCE
IMPLEMENTATION of Version 2.0's Table Export Framework -- see
app.table_export_service's own module docstring for the full framework
design and how a future page adopts it. The Export button exports
`self._current_display_rows`, which _render_table() sets to the EXACT
list it just used to populate the Treeview (post branch/division
filter) -- never re-queried or independently re-filtered here -- so the
exported workbook always matches the current on-screen view exactly: a
CFA filter, a Division filter, or (if either is later added to this
page) a search box or column sort would all be captured automatically,
with zero changes needed to the export code below. The CWH-shortage red
highlight is mirrored into the exported cells via
_replenishment_row_style(), so a downloaded workbook looks like what the
user was seeing, not just the same numbers with the color stripped out.

COLUMNS/HEADINGS (Milestone 56): moved to and re-exported from
app.replenishment_service -- the same report shape is now also used by
the Automated Email System (app/inventory_notification_service.py), so it
lives once at the data layer rather than being defined here and
duplicated there. `WIDTHS` (pixel display widths) stays here -- purely a
UI concern the email attachment has no use for.
"""

import customtkinter as ctk

from app.replenishment_service import (
    REPLENISHMENT_REPORT_COLUMNS as COLUMNS,
    REPLENISHMENT_REPORT_HEADINGS as HEADINGS,
    get_replenishment_required,
    get_replenishment_summary,
)
from app.table_export_service import RowStyle, default_export_filename, export_rows_with_ui
from ui.components import Card, EmptyState, PrimaryButton, SectionHeader, styled_treeview
from ui.icons import get_icon
from ui.theme import Color, Font, Spacing

WIDTHS = {
    "branch": 160,
    "division": 110,
    "item_code": 90,
    "item_name": 190,
    "closing_stock": 100,
    "transit_stock": 100,
    "effective_stock": 150,
    "previous_month_sales_display": 160,
    "deficit_display": 140,
}


def _replenishment_row_style(row: dict) -> RowStyle | None:
    """Mirrors this page's own on-screen CWH-shortage row highlight (see
    _render_table's "cwh_shortage" Treeview tag, Color.CRITICAL_ROW_BG/
    CRITICAL_ROW_TEXT) into an exported row's cell fill/font color, so a
    downloaded workbook visually matches what the user was looking at --
    not just the same numbers with the color stripped out."""
    if row.get("cwh_shortage"):
        return RowStyle(
            fill_color=Color.CRITICAL_ROW_BG.lstrip("#"),
            font_color=Color.CRITICAL_ROW_TEXT.lstrip("#"),
        )
    return None


class InventoryReplenishmentPage(ctk.CTkFrame):
    """CFA/Division filters over the real replenishment-required table."""

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
        self._all_rows = get_replenishment_required()
        self._total_evaluated = get_replenishment_summary()["total_evaluated"]
        self._refresh_filter_options()
        self._render_table()

    def _build_widgets(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Replenishment", "Items requiring replenishment across branches"
        ).pack(anchor="w", pady=(0, Spacing.LG))

        table_card = Card(outer)
        table_card.pack(fill="both", expand=True)

        table_body = ctk.CTkFrame(table_card, fg_color="transparent")
        table_body.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)

        filter_row = ctk.CTkFrame(table_body, fg_color="transparent")
        filter_row.pack(fill="x", pady=(0, Spacing.SM))

        self.export_button = PrimaryButton(
            filter_row,
            text="Export",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_export_clicked,
        )
        self.export_button.pack(side="right")

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

        # Unified search -- matches EITHER Item Code or Item Name, so the
        # user never has to pick which field they meant (see
        # _render_table's single `product_query` filter below).
        self.product_search_entry = ctk.CTkEntry(filter_row, placeholder_text="Search Product…")
        self.product_search_entry.pack(side="left", fill="x", expand=True)
        self.product_search_entry.bind("<KeyRelease>", lambda event: self._render_table())

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
        """Exports `self._current_display_rows` -- see the module
        docstring: this is set below to the EXACT same list just used to
        populate the Treeview, so the exported workbook always matches
        the current on-screen view (current branch/division filter,
        and any search/sort this page gains in the future) without this
        handler ever needing to know what filtering is currently active."""
        suffix = "_".join(
            value for value in (self._branch_filter, self._division_filter) if value != "All"
        )
        export_rows_with_ui(
            self,
            rows=self._current_display_rows,
            columns=COLUMNS,
            headings=HEADINGS,
            suggested_filename=default_export_filename("Replenishment", suffix=suffix),
            sheet_title="Replenishment",
            row_style_fn=_replenishment_row_style,
        )

    def _render_table(self) -> None:
        for widget in self.table_container.winfo_children():
            widget.destroy()
        self._current_display_rows = []

        if self._total_evaluated == 0:
            EmptyState(
                self.table_container, "Upload an Inventory Report to evaluate replenishment needs."
            ).pack(fill="both", expand=True)
            return

        if not self._all_rows:
            EmptyState(self.table_container, "All products are within healthy stock levels.").pack(
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
            # Unified search -- matches Item Code OR Item Name, applied on
            # top of the branch/division filters above (e.g. Division=Onyx
            # AND Search=BENULIV narrows to only Onyx's BENULIV rows).
            rows = [
                r for r in rows
                if product_query in (r["item_code"] or "").lower() or product_query in r["item_name"].lower()
            ]

        # Captured here, BEFORE the "no match" empty-state check below, so
        # Export always reflects reality: an empty list when nothing
        # matches the current filter (export_rows_with_ui shows "Nothing
        # to Export" rather than silently doing nothing), never a stale
        # list left over from a previous filter selection.
        self._current_display_rows = rows

        if not rows:
            EmptyState(self.table_container, "No products match the current filter.").pack(
                fill="both", expand=True
            )
            return

        tree = styled_treeview(self.table_container, COLUMNS, HEADINGS, WIDTHS, height=14)
        tree.tag_configure("requires_replenishment", background=Color.WARNING_SOFT)
        tree.tag_configure(
            "cwh_shortage", background=Color.CRITICAL_ROW_BG, foreground=Color.CRITICAL_ROW_TEXT
        )
        for row in rows:
            row_tag = "cwh_shortage" if row.get("cwh_shortage") else "requires_replenishment"
            tree.insert(
                "", "end",
                tags=(row_tag,),
                values=tuple(row[col] for col in COLUMNS),
            )
        tree.pack(fill="both", expand=True)
