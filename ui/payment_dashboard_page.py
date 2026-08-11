"""Payment Analytics Dashboard: landing page of the Payment Analytics
module. Rebuilt from scratch every time it's shown (on_show, the same
convention every other dashboard page in this app uses) from the real
data computed by app/payment_analytics_service.py -- no dummy data, no
workbook access of its own; it only ever reads what the Historical/
Monthly Upload pages' engine runs already stored.

Answers one question -- "who do I need to chase today?" -- rather than
being a generic stats page: Critical (Red) Customers is the primary focus
area (left), Payment Delay Distribution shows overall portfolio health
(right), and Monthly Collection Trend (bottom) shows whether collections
are improving or worsening over the active six-month window.
"""

import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from app.payment_analytics_service import (
    RISK_GREEN,
    RISK_ORANGE,
    RISK_RED,
    RISK_YELLOW,
    get_active_window_labels,
    get_all_customer_profiles,
    get_critical_customers,
    get_dashboard_summary,
    get_monthly_trend,
)
from app.table_export_service import RowStyle, default_export_filename, export_rows_with_ui
from ui.components import Card, EmptyState, KPICard, PrimaryButton, SectionHeader, styled_treeview
from ui.icons import get_icon
from ui.theme import Color, Font, Spacing

# Reuses the existing theme palette rather than inventing new colors:
# Yellow -> WARNING (amber/gold), Orange -> PRIMARY (the brand's true
# orange), Red -> ERROR. Kept in one place so the KPI cards and the
# Critical Customers row tinting can never drift apart.
RISK_COLOR = {
    RISK_GREEN: Color.SUCCESS,
    RISK_YELLOW: Color.WARNING,
    RISK_ORANGE: Color.PRIMARY,
    RISK_RED: Color.ERROR,
}

CRITICAL_COLUMNS = ("party_name", "customer_type", "average_payment_days", "total_invoices", "last_payment_date", "risk_category")
CRITICAL_HEADINGS = {
    "party_name": "Party Name",
    "customer_type": "Customer Type",
    "average_payment_days": "Avg Payment Days",
    "total_invoices": "Number of Invoices",
    "last_payment_date": "Last Payment Date",
    "risk_category": "Risk Status",
}
CRITICAL_WIDTHS = {
    "party_name": 200, "customer_type": 120, "average_payment_days": 130,
    "total_invoices": 130, "last_payment_date": 120, "risk_category": 100,
}

# Payment Delay Distribution buckets -- deliberately distinct boundaries
# from the Green/Yellow/Orange/Red risk thresholds (which are 30/45/60
# strict-less-than cutoffs): these are inclusive day-count buckets meant
# to read as a plain histogram of the whole customer base, not a
# recoloring of the same risk categories.
DELAY_BUCKETS = [
    ("0–30 Days", lambda d: d <= 30),
    ("31–45 Days", lambda d: 30 < d <= 45),
    ("46–60 Days", lambda d: 45 < d <= 60),
    ("60+ Days", lambda d: d > 60),
]
DELAY_BUCKET_COLORS = [Color.SUCCESS, Color.WARNING, Color.PRIMARY, Color.ERROR]


class PaymentDashboardPage(ctk.CTkFrame):
    """Landing page of the Payment Analytics module -- KPI cards, a
    Critical Customers table, a Payment Delay Distribution chart, and a
    Monthly Collection Trend chart, all driven by the real dataset."""

    def __init__(self, master, on_view_customer=None) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._on_view_customer = on_view_customer
        self._content_container: ctk.CTkFrame | None = None
        self._current_display_rows: list[dict] = []
        self._build_shell()

    def _build_shell(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Payment Analytics Dashboard", "A live view of customer payment behavior and risk"
        ).pack(anchor="w", pady=(0, Spacing.LG))

        self._content_container = ctk.CTkFrame(outer, fg_color="transparent")
        self._content_container.pack(fill="both", expand=True)

    def on_show(self) -> None:
        """Called every time this page becomes visible -- clears
        everything and rebuilds from the current stored dataset, so the
        dashboard never shows stale data from a previous upload."""
        for widget in self._content_container.winfo_children():
            widget.destroy()

        summary = get_dashboard_summary()
        if summary["total_customers"] == 0:
            EmptyState(
                self._content_container,
                "No historical payment data yet -- upload a Historical Payment Report on the "
                "Historical Upload page.",
            ).pack(fill="both", expand=True)
            return

        self._render_dashboard(summary)

    # --- Layout ------------------------------------------------------

    def _render_dashboard(self, summary: dict) -> None:
        container = self._content_container

        window = get_active_window_labels()
        if window:
            window_display = f"{window[0]} → {window[-1]}" if len(window) > 1 else window[0]
            ctk.CTkLabel(
                container, text=f"Active window: {window_display} ({len(window)} month(s))",
                font=Font.SMALL_BOLD, text_color=Color.PRIMARY, anchor="w",
            ).pack(anchor="w", pady=(0, Spacing.MD))

        kpi_specs = [
            ("Total Customers", Color.PRIMARY, f"{summary['total_customers']:,}"),
            ("Average Collection Time", Color.INFO, f"{summary['average_collection_time']:.1f} days"),
            ("Green Customers", RISK_COLOR[RISK_GREEN], f"{summary['green_customers']:,}"),
            ("Yellow Customers", RISK_COLOR[RISK_YELLOW], f"{summary['yellow_customers']:,}"),
            ("Orange Customers", RISK_COLOR[RISK_ORANGE], f"{summary['orange_customers']:,}"),
            ("Red Customers", RISK_COLOR[RISK_RED], f"{summary['red_customers']:,}"),
        ]

        kpi_row = ctk.CTkFrame(container, fg_color="transparent")
        kpi_row.pack(fill="x", pady=(0, Spacing.LG))
        for i in range(len(kpi_specs)):
            kpi_row.grid_columnconfigure(i, weight=1)
        for i, (label, accent, value) in enumerate(kpi_specs):
            card = KPICard(kpi_row, label=label, value=value, accent=accent)
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else Spacing.SM, 0))

        main_row = ctk.CTkFrame(container, fg_color="transparent")
        main_row.pack(fill="both", expand=True, pady=(0, Spacing.LG))
        main_row.grid_columnconfigure(0, weight=3)
        main_row.grid_columnconfigure(1, weight=2)

        critical_card = Card(main_row)
        critical_card.grid(row=0, column=0, sticky="nsew", padx=(0, Spacing.SM))
        self._build_critical_customers(critical_card)

        delay_card = Card(main_row)
        delay_card.grid(row=0, column=1, sticky="nsew", padx=(Spacing.SM, 0))
        self._build_delay_distribution_chart(delay_card)

        trend_card = Card(container)
        trend_card.pack(fill="both", expand=True)
        self._build_trend_chart(trend_card)

    def _chart_heading(self, parent, title: str, note: str | None = None):
        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)
        ctk.CTkLabel(body, text=title, font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w").pack(anchor="w")
        if note:
            ctk.CTkLabel(body, text=note, font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w").pack(
                anchor="w", pady=(0, Spacing.SM)
            )
        return body

    def _style_axes(self, ax) -> None:
        ax.set_facecolor(Color.CARD)
        for spine_name in ("top", "right"):
            ax.spines[spine_name].set_visible(False)
        for spine_name in ("left", "bottom"):
            ax.spines[spine_name].set_color(Color.BORDER)
        ax.tick_params(colors=Color.TEXT_SECONDARY, labelsize=8)
        ax.yaxis.grid(True, color=Color.DIVIDER, linewidth=0.8)
        ax.set_axisbelow(True)

    # --- Critical Customers ------------------------------------------------

    def _build_critical_customers(self, parent) -> None:
        body = self._chart_heading(
            parent, "Critical Customers", "Red-risk accounts, worst average payment days first"
        )

        self._current_display_rows = []

        rows = get_critical_customers()
        if not rows:
            EmptyState(body, "No Red-risk customers in the active window.").pack(fill="both", expand=True)
            return

        export_row = ctk.CTkFrame(body, fg_color="transparent")
        export_row.pack(fill="x", pady=(0, Spacing.SM))
        self.export_button = PrimaryButton(
            export_row,
            text="Export",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_export_clicked,
        )
        self.export_button.pack(side="right")

        tree = styled_treeview(body, CRITICAL_COLUMNS, CRITICAL_HEADINGS, CRITICAL_WIDTHS, height=10)
        tree.tag_configure("critical", background=Color.ERROR_SOFT)
        for row in rows:
            last_payment = row["last_payment_date"].strftime("%Y-%m-%d") if row["last_payment_date"] else "—"
            # The one place these values are computed -- both the
            # Treeview's own `values=` tuple below and the exported row
            # (Milestone 55) read from this same dict.
            display_row = {
                "party_name": row["party_name"],
                "customer_type": row["customer_type"] or "—",
                "average_payment_days": f"{row['average_payment_days']:.1f}",
                "total_invoices": row["total_invoices"],
                "last_payment_date": last_payment,
                "risk_category": row["risk_category"],
            }
            self._current_display_rows.append(display_row)

            tree.insert(
                "", "end", tags=("critical",), iid=row["party_name"],
                values=tuple(display_row[col] for col in CRITICAL_COLUMNS),
            )
        tree.pack(fill="both", expand=True)

        if self._on_view_customer is not None:
            tree.bind("<<TreeviewSelect>>", lambda event, t=tree: self._on_critical_row_selected(t))

    def _on_export_clicked(self) -> None:
        """Exports `self._current_display_rows` -- the exact list
        `_build_critical_customers()` just used to populate the Treeview.
        This table has no filters of its own (always every Red-risk
        customer), so there is nothing further to capture beyond the rows
        themselves."""
        export_rows_with_ui(
            self,
            rows=self._current_display_rows,
            columns=CRITICAL_COLUMNS,
            headings=CRITICAL_HEADINGS,
            suggested_filename=default_export_filename("CriticalCustomers"),
            sheet_title="Critical Customers",
            row_style_fn=lambda row: RowStyle(fill_color=Color.ERROR_SOFT.lstrip("#")),
        )

    def _on_critical_row_selected(self, tree) -> None:
        selection = tree.selection()
        if selection and self._on_view_customer is not None:
            self._on_view_customer(selection[0])  # iid == party_name

    # --- Payment Delay Distribution -----------------------------------------

    def _build_delay_distribution_chart(self, parent) -> None:
        body = self._chart_heading(parent, "Payment Delay Distribution", "Customers per average-payment-days bucket")

        profiles = get_all_customer_profiles()
        counts = [sum(1 for p in profiles if predicate(p["average_payment_days"])) for _, predicate in DELAY_BUCKETS]
        labels = [label for label, _ in DELAY_BUCKETS]

        fig = Figure(figsize=(4.4, 3.6), dpi=100, facecolor=Color.CARD)
        ax = fig.add_subplot(111)
        ax.bar(labels, counts, color=DELAY_BUCKET_COLORS, width=0.6)
        self._style_axes(ax)
        for label in ax.get_xticklabels():
            label.set_rotation(20)
            label.set_ha("right")

        canvas = FigureCanvasTkAgg(fig, master=body)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    # --- Monthly Collection Trend --------------------------------------------

    def _build_trend_chart(self, parent) -> None:
        body = self._chart_heading(
            parent, "Monthly Collection Trend",
            "Average payment days per month in the active window -- months ordered chronologically, not by label text",
        )

        trend = get_monthly_trend()
        if not trend:
            EmptyState(body, "No invoice data for the active dataset.").pack(fill="both", expand=True)
            return

        months = [row["month"] for row in trend]
        values = [row["average_payment_days"] for row in trend]

        fig = Figure(figsize=(9, 3.4), dpi=100, facecolor=Color.CARD)
        ax = fig.add_subplot(111)
        ax.plot(months, values, color=Color.PRIMARY, marker="o", linewidth=2)
        ax.fill_between(months, values, color=Color.PRIMARY_SOFT)
        self._style_axes(ax)

        canvas = FigureCanvasTkAgg(fig, master=body)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
