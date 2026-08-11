"""Payment Analytics Customer Analytics page.

A searchable, sortable, filterable table of every customer profile
computed by app/payment_analytics_service.py from the active Historical
Payment Report -- reloaded every time the page is shown (on_show, the
same convention every other data page in this app uses), so it always
reflects the latest processed upload.
"""

import customtkinter as ctk

from app.payment_analytics_service import RISK_CATEGORIES, get_all_customer_profiles
from app.table_export_service import RowStyle, default_export_filename, export_rows_with_ui
from ui.components import Card, EmptyState, PrimaryButton, SectionHeader, StatusBadge, styled_treeview
from ui.icons import get_icon
from ui.theme import Color, Font, Spacing

RISK_BADGE_KIND = {"Green": "success", "Yellow": "warning", "Orange": "primary", "Red": "error"}
RISK_ROW_TAG_COLOR = {
    "Green": Color.SUCCESS_SOFT, "Yellow": Color.WARNING_SOFT,
    "Orange": Color.PRIMARY_SOFT, "Red": Color.ERROR_SOFT,
}


def _customer_row_style(row: dict) -> RowStyle | None:
    """Mirrors this page's own on-screen Risk Category row highlight
    (RISK_ROW_TAG_COLOR) into the exported row's cell fill."""
    color = RISK_ROW_TAG_COLOR.get(row.get("risk_category"))
    if color is None:
        return None
    return RowStyle(fill_color=color.lstrip("#"))

COLUMNS = ("party_name", "customer_type", "average_payment_days", "total_invoices", "risk_category")
HEADINGS = {
    "party_name": "Party Name",
    "customer_type": "Customer Type",
    "average_payment_days": "Average Payment Days",
    "total_invoices": "Total Invoices",
    "risk_category": "Risk Category",
}
WIDTHS = {
    "party_name": 240, "customer_type": 150, "average_payment_days": 170,
    "total_invoices": 120, "risk_category": 120,
}


class PaymentCustomerAnalyticsPage(ctk.CTkFrame):
    """Search, sort, and filter every customer profile in the active
    historical dataset."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)

        self._profiles: list[dict] = []
        self._current_display_rows: list[dict] = []
        self._sort_column = "party_name"
        self._sort_reverse = False
        self._risk_filter = "All"

        self._build_widgets()

    def on_show(self) -> None:
        """Called every time this page becomes visible -- reload from the
        stored dataset."""
        self._profiles = get_all_customer_profiles()
        self._render_table()

    def focus_customer(self, party_name: str) -> None:
        """Called when navigating here from the Dashboard's Critical
        Customers table (on_show() has already reloaded self._profiles by
        this point) -- resets the Risk filter to All, so the target
        customer is never hidden by a stale filter, and pre-fills Search
        with their exact name."""
        self._risk_filter = "All"
        self.risk_menu.set("All")
        self.search_entry.delete(0, "end")
        self.search_entry.insert(0, party_name)
        self._render_table()

    def _build_widgets(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Customer Analytics", "Search and review customer payment behavior"
        ).pack(anchor="w", pady=(0, Spacing.LG))

        table_card = Card(outer)
        table_card.pack(fill="both", expand=True)

        table_body = ctk.CTkFrame(table_card, fg_color="transparent")
        table_body.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)

        filter_row = ctk.CTkFrame(table_body, fg_color="transparent")
        filter_row.pack(fill="x", pady=(0, Spacing.SM))

        ctk.CTkLabel(filter_row, text="Risk:", font=Font.BODY, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(0, Spacing.SM)
        )
        self.risk_menu = ctk.CTkOptionMenu(
            filter_row,
            values=["All"] + RISK_CATEGORIES,
            command=self._on_risk_filter_changed,
            fg_color=Color.SURFACE,
            button_color=Color.PRIMARY,
            button_hover_color=Color.PRIMARY_HOVER,
            text_color=Color.TEXT_PRIMARY,
            width=130,
        )
        self.risk_menu.pack(side="left", padx=(0, Spacing.MD))

        self.search_entry = ctk.CTkEntry(filter_row, placeholder_text="Search party name or customer type…")
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

    def _on_risk_filter_changed(self, value: str) -> None:
        self._risk_filter = value
        self._render_table()

    def _sort_key(self, profile: dict):
        value = profile.get(self._sort_column)
        if self._sort_column == "risk_category" and value in RISK_CATEGORIES:
            value = RISK_CATEGORIES.index(value)
        return (value is None, value)

    def _on_heading_clicked(self, column: str) -> None:
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        self._render_table()

    def _on_export_clicked(self) -> None:
        """Exports `self._current_display_rows` -- the exact list
        `_render_table()` just used to populate the Treeview."""
        suffix = "" if self._risk_filter == "All" else self._risk_filter
        export_rows_with_ui(
            self,
            rows=self._current_display_rows,
            columns=COLUMNS,
            headings=HEADINGS,
            suggested_filename=default_export_filename("CustomerAnalytics", suffix=suffix),
            sheet_title="Customer Analytics",
            row_style_fn=_customer_row_style,
        )

    def _render_table(self) -> None:
        for widget in self.table_container.winfo_children():
            widget.destroy()
        self._current_display_rows = []

        query = self.search_entry.get().strip().lower()
        rows = self._profiles

        if self._risk_filter != "All":
            rows = [r for r in rows if r["risk_category"] == self._risk_filter]

        if query:
            rows = [
                r for r in rows
                if query in r["party_name"].lower() or query in (r["customer_type"] or "").lower()
            ]

        rows = sorted(rows, key=self._sort_key, reverse=self._sort_reverse)

        if not rows:
            message = (
                "No historical payment data yet -- upload a Historical Payment Report on the "
                "Historical Upload page."
                if not self._profiles
                else "No customers match the current search/filter."
            )
            EmptyState(self.table_container, message).pack(fill="both", expand=True)
            return

        # scrollbars=False: this container also holds the Risk Category legend
        # (packed on the right, below), so the generic table scrollbars would
        # fight that custom side-by-side layout. This table is narrow (5
        # columns) so horizontal clipping isn't a concern here.
        tree = styled_treeview(self.table_container, COLUMNS, HEADINGS, WIDTHS, height=14, scrollbars=False)
        for col in COLUMNS:
            tree.heading(col, text=HEADINGS[col], command=lambda c=col: self._on_heading_clicked(c))
        for risk, color in RISK_ROW_TAG_COLOR.items():
            tree.tag_configure(risk, background=color)

        for profile in rows:
            risk = profile["risk_category"]
            # The one place these values are computed -- both the
            # Treeview's own `values=` tuple below and the exported row
            # (Milestone 55) read from this same dict.
            display_row = {
                "party_name": profile["party_name"],
                "customer_type": profile["customer_type"] or "—",
                "average_payment_days": f"{profile['average_payment_days']:.1f}",
                "total_invoices": profile["total_invoices"],
                "risk_category": risk,
            }
            self._current_display_rows.append(display_row)

            tree.insert(
                "", "end",
                tags=(risk,) if risk in RISK_ROW_TAG_COLOR else (),
                values=tuple(display_row[col] for col in COLUMNS),
            )
        tree.pack(side="left", fill="both", expand=True)

        # Risk Category is also rendered as a legend of colored badges beside
        # the table -- Treeview cells can't host a styled widget directly.
        legend = ctk.CTkFrame(self.table_container, fg_color="transparent", width=170)
        legend.pack(side="right", fill="y", padx=(Spacing.MD, 0))
        legend.pack_propagate(False)
        ctk.CTkLabel(
            legend, text="Risk Category", font=Font.SMALL_BOLD, text_color=Color.TEXT_MUTED, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))
        for level, description in (
            ("Green", "< 30 days"), ("Yellow", "30–44 days"), ("Orange", "45–59 days"), ("Red", "≥ 60 days"),
        ):
            row = ctk.CTkFrame(legend, fg_color="transparent")
            row.pack(anchor="w", pady=(0, Spacing.XS))
            StatusBadge(row, level, RISK_BADGE_KIND[level]).pack(side="left")
            ctk.CTkLabel(row, text=description, font=Font.SMALL, text_color=Color.TEXT_MUTED).pack(
                side="left", padx=(Spacing.XS, 0)
            )
