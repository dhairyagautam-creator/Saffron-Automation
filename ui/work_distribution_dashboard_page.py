"""Work Distribution Dashboard: landing page of the module. Reloads from
the database every time this page becomes visible (on_show), matching the
same convention every other module dashboard in this app already uses
(see ui/inventory_dashboard_page.py).

Recent Uploads card added below the KPI grid -- unlike
ui/inventory_dashboard_page.py's own Recent Uploads (a hardcoded
placeholder list, not real data), this reads real rows from
app.work_distribution_upload_log_service, logged at the moment each file is
successfully browsed/connected (see ui/work_distribution_upload_page.py and
ui/work_distribution_email_center_page.py). Always shown regardless of
whether RGD Coverage has any findings yet -- a hierarchy workbook or a
Manager Work Allocation file can be uploaded before RGD Coverage's own KPI
data exists.
"""

import customtkinter as ctk

from app.work_distribution_service import get_dashboard_summary
from app.work_distribution_upload_log_service import get_recent_uploads
from ui.components import Card, EmptyState, KPICard, SectionHeader, styled_treeview
from ui.theme import Color, Font, Spacing

RECENT_UPLOADS_COLUMNS = ("file_name", "upload_type", "division", "status", "uploaded_at")
RECENT_UPLOADS_HEADINGS = {
    "file_name": "File",
    "upload_type": "Type",
    "division": "Division",
    "status": "Status",
    "uploaded_at": "Uploaded At",
}
RECENT_UPLOADS_WIDTHS = {
    "file_name": 220,
    "upload_type": 220,
    "division": 100,
    "status": 90,
    "uploaded_at": 160,
}


class WorkDistributionDashboardPage(ctk.CTkFrame):
    """Landing page of the Work Distribution module."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._build_widgets()

    def on_show(self) -> None:
        summary = get_dashboard_summary()
        # Recent Uploads is unpacked and re-packed on every on_show() (not
        # packed once in _build_widgets()) specifically so its pack() call
        # always happens AFTER kpi_grid's/empty_state's own pack() call
        # below -- Tkinter's pack geometry manager stacks children in the
        # ORDER pack() was CALLED, not creation order, so packing it once
        # up front in _build_widgets() (this page's previous bug) made it
        # render ABOVE the KPI cards regardless of where it was built.
        self.recent_uploads_card.pack_forget()

        if summary["total_employees"] == 0:
            self.kpi_grid.pack_forget()
            self.empty_state.pack(fill="both", expand=True, pady=Spacing.XL)
        else:
            self.empty_state.pack_forget()
            self.kpi_grid.pack(fill="x", pady=(0, Spacing.LG))

            self.kpi_cards["Total Employees"].set_value(f"{summary['total_employees']:,}")
            self.kpi_cards["Doctor Logs"].set_value(f"{summary['total_doctors']:,}")
            self.kpi_cards["Flagged Employees"].set_value(f"{summary['flagged_employees']:,}")
            self.kpi_cards["Average Coverage"].set_value(f"{summary['average_coverage']:.0f}%")

        self.recent_uploads_card.pack(fill="x", pady=(Spacing.LG, 0))
        self._refresh_recent_uploads()

    def _refresh_recent_uploads(self) -> None:
        for widget in self.recent_uploads_container.winfo_children():
            widget.destroy()

        rows = get_recent_uploads()
        if not rows:
            EmptyState(
                self.recent_uploads_container, "No files uploaded yet."
            ).pack(fill="x", pady=Spacing.MD)
            return

        tree = styled_treeview(
            self.recent_uploads_container, RECENT_UPLOADS_COLUMNS, RECENT_UPLOADS_HEADINGS,
            RECENT_UPLOADS_WIDTHS, height=6, scrollbars=False,
        )
        for row in rows:
            tree.insert("", "end", values=tuple(row[col] for col in RECENT_UPLOADS_COLUMNS))
        tree.pack(fill="x")

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer,
            "Work Distribution Dashboard",
            "Monitor monthly doctor coverage for BMs and ABMs",
        ).pack(anchor="w", pady=(0, Spacing.LG))

        self.kpi_cards: dict[str, KPICard] = {}
        self.kpi_grid = ctk.CTkFrame(outer, fg_color="transparent")
        specs = [
            ("Total Employees", Color.PRIMARY),
            ("Doctor Logs", Color.INFO),
            ("Flagged Employees", Color.WARNING),
            ("Average Coverage", Color.SUCCESS),
        ]
        for i in range(len(specs)):
            self.kpi_grid.grid_columnconfigure(i, weight=1)
        for i, (label, accent) in enumerate(specs):
            card = KPICard(self.kpi_grid, label=label, value="—", accent=accent)
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else Spacing.SM, 0))
            self.kpi_cards[label] = card

        self.empty_state = EmptyState(
            outer, "Upload a Work Distribution report to see coverage KPIs here."
        )

        # Built here but NOT packed here -- see on_show(), which packs it
        # AFTER kpi_grid/empty_state every time, so it always renders below
        # them regardless of pack-call order across separate on_show() runs.
        self.recent_uploads_card = Card(outer)
        recent_uploads_body = ctk.CTkFrame(self.recent_uploads_card, fg_color="transparent")
        recent_uploads_body.pack(fill="both", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            recent_uploads_body, text="Recent Uploads", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        self.recent_uploads_container = ctk.CTkFrame(recent_uploads_body, fg_color="transparent")
        self.recent_uploads_container.pack(fill="x")
