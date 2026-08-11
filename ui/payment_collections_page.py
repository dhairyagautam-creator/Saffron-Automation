"""Payment Analytics Collections Action Center page.

The daily operational workspace for the Accounts/Collections team --
unlike the six-month Historical Analytics pages, this focuses only on
currently outstanding invoices needing follow-up (see
app/collections_service.py for the engine). Real upload/processing, real
KPIs, real filtering, and a persisted Follow-up checkbox.

The table is a themed ttk.Treeview (styled_treeview, the same widget
Findings/Customer Analytics already use), not one hand-built CTkFrame per
row -- Section 10's "comfortably handle several thousand outstanding
invoices" requirement rules out a widget-per-cell table (a few thousand
rows x 9 CTk widgets each would be dramatically slower to build than a
single native Treeview holding the same rows). The Follow-up checkbox is
a clickable "☐"/"☑" glyph in the first column instead of a real
ctk.CTkCheckBox widget -- same interaction, same "grey out and move to
the bottom" behavior, just backed by a widget that actually scales.
"""

import threading
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from loguru import logger

from app.collections_service import (
    STATUS_CRITICAL,
    STATUS_DUE_SOON,
    STATUS_FILTER_OPTIONS,
    STATUS_NOT_YET_DUE,
    STATUS_OVERDUE,
    DAYS_OVER_DUE_BUCKET_OPTIONS,
    DUE_DATE_FILTER_OPTIONS,
    FOLLOW_UP_FILTER_OPTIONS,
    TEAM_OPTIONS,
    get_hq_options,
    get_outstanding_invoices,
    matches_days_over_due_bucket,
    matches_due_date_filter,
    process_outstanding_report,
    set_follow_up,
    sort_invoices,
    summarize_invoices,
)
from app.excel_validation import SUPPORTED_EXTENSIONS, load_outstanding_report
from app.table_export_service import RowStyle, default_export_filename, export_rows_with_ui
from ui.background_task import run_in_background
from ui.components import (
    Card,
    EmptyState,
    KPICard,
    PrimaryButton,
    SecondaryButton,
    SectionHeader,
    render_error_banner,
    render_kpi_row,
    render_success_banner,
    render_warning_banner,
    styled_treeview,
)
from ui.icons import get_icon
from ui.loading_overlay import LoadingOverlay
from ui.theme import Color, Font, Spacing

FILE_TYPES = [
    ("Supported files", "*.xlsx *.xls *.xlsm *.csv"),
    ("Excel Workbook (*.xlsx)", "*.xlsx"),
    ("Excel 97-2003 Workbook (*.xls)", "*.xls"),
    ("Excel Macro-Enabled Workbook (*.xlsm)", "*.xlsm"),
    ("CSV files (*.csv)", "*.csv"),
    ("All files", "*.*"),
]

STATUS_ROW_TAG = {
    STATUS_NOT_YET_DUE: "status_not_yet_due",
    STATUS_DUE_SOON: "status_due_soon",
    STATUS_OVERDUE: "status_overdue",
    STATUS_CRITICAL: "status_critical",
}
STATUS_ROW_COLOR = {
    STATUS_NOT_YET_DUE: Color.SUCCESS_SOFT,
    STATUS_DUE_SOON: Color.WARNING_SOFT,
    STATUS_OVERDUE: Color.PRIMARY_SOFT,
    STATUS_CRITICAL: Color.ERROR_SOFT,
}

CHECKED_GLYPH = "☑"
UNCHECKED_GLYPH = "☐"


def _collections_row_style(row: dict) -> RowStyle | None:
    """Mirrors this page's own on-screen row highlight -- the
    "followed_up_row" tag (once an invoice is checked off) takes priority
    over the status color, exactly matching _render_table's own tag
    precedence below."""
    if row.get("_followed_up"):
        return RowStyle(fill_color=Color.SURFACE.lstrip("#"), font_color=Color.TEXT_MUTED.lstrip("#"))
    color = STATUS_ROW_COLOR.get(row.get("status"))
    if color is None:
        return None
    return RowStyle(fill_color=color.lstrip("#"))

COLUMNS = (
    "followed_up", "party_name", "team", "invoice_no", "bill_amount",
    "lr_date", "due_date", "days_outstanding", "days_over_due", "status",
)
# Header text uses "Division"/"Bill No." (a genuine vocabulary change from
# the old placeholder names, matching the real report) but keeps the
# clean "LR Date" spelling for display -- only a punctuation difference
# from the report's actual "L.R. Date" column, which is what
# app/collections_service.py matches against on upload.
HEADINGS = {
    "followed_up": "Follow-up", "party_name": "Party Name", "team": "Division", "invoice_no": "Bill No.",
    "bill_amount": "Bill Amount", "lr_date": "LR Date", "due_date": "Due Date",
    "days_outstanding": "Outstanding", "days_over_due": "Over Due", "status": "Status",
}
WIDTHS = {
    "followed_up": 50, "party_name": 150, "team": 70, "invoice_no": 75, "bill_amount": 90,
    "lr_date": 75, "due_date": 75, "days_outstanding": 75, "days_over_due": 70, "status": 75,
}

UPLOAD_SUMMARY_SPECS = ["Rows Read", "Valid Invoices", "Rows Skipped", "Upload Date & Time"]
KPI_SPECS = [
    ("Total Outstanding Invoices", Color.INFO),
    ("Not Yet Due", Color.SUCCESS),
    ("Due Soon", Color.WARNING),
    ("Overdue", Color.PRIMARY),
    ("Critical", Color.ERROR),
]


def _parse_optional_date(text: str) -> date | None:
    """Lenient parse of a plain YYYY-MM-DD entry box -- blank or
    unparseable both mean "no bound on this side", never a crash."""
    text = text.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


class _UploadCard(Card):
    """Upload + validate + process the Outstanding Report. Replaces the
    entire stored invoice list and clears every Follow-up checkmark --
    Daily Refresh (Step 7)."""

    def __init__(self, master, on_processed) -> None:
        super().__init__(master)
        self._on_processed = on_processed

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Upload Outstanding Report", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "Select the current outstanding invoices workbook. This replaces every invoice below and "
                "clears every Follow-up checkmark -- always the latest report only."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=800,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        action_row = ctk.CTkFrame(body, fg_color="transparent")
        action_row.pack(fill="x")

        self.browse_button = PrimaryButton(
            action_row,
            text="Upload Outstanding Report",
            image=get_icon("upload", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_browse_clicked,
        )
        self.browse_button.pack(side="left")

        self.file_label = ctk.CTkLabel(
            action_row, text="No file selected", font=Font.BODY, text_color=Color.TEXT_MUTED, anchor="w"
        )
        self.file_label.pack(side="left", padx=(Spacing.MD, 0))

        self.result_container = ctk.CTkFrame(body, fg_color="transparent")
        self.result_container.pack(fill="x", pady=(Spacing.MD, 0))

        self.summary_container = ctk.CTkFrame(body, fg_color="transparent")

        self.loading_overlay = LoadingOverlay(self)

    def _on_browse_clicked(self) -> None:
        file_path = filedialog.askopenfilename(title="Select Outstanding Report", filetypes=FILE_TYPES)
        if not file_path:
            return

        self._clear_result()
        self._clear_summary()
        self.file_label.configure(text=Path(file_path).name, text_color=Color.TEXT_PRIMARY)

        if Path(file_path).suffix.lower() not in SUPPORTED_EXTENSIONS:
            render_error_banner(
                self.result_container, "Unsupported File Type",
                ["Please upload an Excel (.xlsx, .xls, .xlsm) or CSV (.csv) file."],
            )
            return

        self.browse_button.configure(state="disabled")
        self.loading_overlay.show()

        def work(report_progress):
            result = load_outstanding_report(file_path, progress_callback=report_progress)
            if result["success"]:
                report_progress(97, "Processing invoices...")
                result["engine"] = process_outstanding_report(result["df"])
                report_progress(99, "Syncing to the cloud...")
                try:
                    from app.payment_sync_service import sync_outstanding_invoices

                    sync_outstanding_invoices()
                except Exception as exc:
                    logger.error(f"Failed to sync outstanding invoices to the cloud: {exc}")
            return result

        def on_progress(percent, message):
            self.loading_overlay.update_progress(percent, message)

        def on_done(result, error):
            self.browse_button.configure(state="normal")
            self.loading_overlay.hide()

            if error is not None:
                render_error_banner(self.result_container, "Import Failed", [f"Could not open the selected file. {error}"])
                return

            if not result["success"]:
                if result["missing_columns"]:
                    render_error_banner(self.result_container, "Missing Columns", result["missing_columns"])
                else:
                    render_error_banner(
                        self.result_container, "Import Failed", [result["error"] or "Could not read the selected file."]
                    )
                return

            engine_result = result["engine"]
            self._show_success(engine_result)
            self._show_summary(engine_result)
            if self._on_processed is not None:
                self._on_processed()

        run_in_background(self, work, on_progress=on_progress, on_done=on_done)

    def _clear_result(self) -> None:
        for widget in self.result_container.winfo_children():
            widget.destroy()

    def _show_success(self, engine_result: dict) -> None:
        self._clear_result()
        render_success_banner(
            self.result_container,
            f"Outstanding Report processed successfully -- {engine_result['total_invoices']:,} invoice(s) loaded.",
        )
        if engine_result["rows_skipped"]:
            render_warning_banner(
                self.result_container,
                f"{engine_result['rows_skipped']} row(s) skipped due to a missing Party Name or an invalid/missing "
                "L.R. Date or Due Date -- see the application log for details.",
            )

    def _clear_summary(self) -> None:
        for widget in self.summary_container.winfo_children():
            widget.destroy()
        self.summary_container.pack_forget()

    def _show_summary(self, engine_result: dict) -> None:
        self._clear_summary()
        self.summary_container.pack(fill="x", pady=(Spacing.MD, 0))
        ctk.CTkLabel(
            self.summary_container, text="Upload Summary", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        values = [
            f"{engine_result['rows_read']:,}", f"{engine_result['rows_valid']:,}",
            f"{engine_result['rows_skipped']:,}", datetime.now().strftime("%Y-%m-%d %H:%M"),
        ]
        render_kpi_row(self.summary_container, UPLOAD_SUMMARY_SPECS, values)


class PaymentCollectionsPage(ctk.CTkFrame):
    """Collections Action Center -- upload, KPI cards, a collapsible
    Filters Panel, and the operational follow-up table."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)

        self._invoices: list[dict] = []
        self._current_display_rows: list[dict] = []
        self._filters_expanded = True

        self._team_filter = "All Divisions"
        self._hq_filter = "All HQs"
        self._status_filter = "All"
        self._days_over_due_filter = "All"
        self._due_date_filter = "All"
        self._follow_up_filter = "All"

        self._build_widgets()

    def on_show(self) -> None:
        """Called every time this page becomes visible -- reload so Days
        Outstanding/Days Over Due/Status reflect today's date even if no
        new report was uploaded since the last visit."""
        self._reload_invoices()

    def _reload_invoices(self) -> None:
        self._invoices = get_outstanding_invoices()
        self._refresh_hq_options()
        self._render_table()

    # --- Layout ------------------------------------------------------

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Collections Action Center",
            "Currently outstanding invoices that need follow-up today",
        ).pack(anchor="w", pady=(0, Spacing.LG))

        _UploadCard(outer, on_processed=self._reload_invoices).pack(fill="x", pady=(0, Spacing.LG))

        self.kpi_container = ctk.CTkFrame(outer, fg_color="transparent")
        self.kpi_container.pack(fill="x", pady=(0, Spacing.LG))

        self._build_filters_panel(outer)
        self._build_table_card(outer)

    # --- KPI cards -----------------------------------------------------

    def _render_kpi_cards(self, summary: dict) -> None:
        for widget in self.kpi_container.winfo_children():
            widget.destroy()

        values = [
            f"{summary['total']:,}", f"{summary['not_yet_due']:,}", f"{summary['due_soon']:,}",
            f"{summary['overdue']:,}", f"{summary['critical']:,}",
        ]
        for i in range(len(KPI_SPECS)):
            self.kpi_container.grid_columnconfigure(i, weight=1)
        for i, ((label, accent), value) in enumerate(zip(KPI_SPECS, values)):
            card = KPICard(self.kpi_container, label=label, value=value, accent=accent)
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else Spacing.SM, 0))

    # --- Filters panel ---------------------------------------------------

    def _build_filters_panel(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(0, Spacing.LG))

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=Spacing.LG, pady=(Spacing.MD, 0))

        self._filters_chevron = ctk.CTkLabel(
            header, text="▾", font=Font.H3, text_color=Color.TEXT_SECONDARY, width=20, anchor="w"
        )
        self._filters_chevron.pack(side="left")
        title_label = ctk.CTkLabel(header, text="Filters", font=Font.H2, text_color=Color.TEXT_PRIMARY, anchor="w")
        title_label.pack(side="left", fill="x", expand=True)
        for widget in (header, self._filters_chevron, title_label):
            widget.bind("<Button-1>", self._toggle_filters)

        self.filters_body = ctk.CTkFrame(card, fg_color="transparent")
        self.filters_body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        self._build_filters_row1(self.filters_body)
        self._build_filters_row2(self.filters_body)
        self._build_filters_row3(self.filters_body)

    def _toggle_filters(self, _event=None) -> None:
        self._filters_expanded = not self._filters_expanded
        if self._filters_expanded:
            self.filters_body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)
            self._filters_chevron.configure(text="▾")
        else:
            self.filters_body.pack_forget()
            self._filters_chevron.configure(text="▸")

    def _dropdown(self, parent, label: str, values: list[str], command) -> ctk.CTkOptionMenu:
        ctk.CTkLabel(parent, text=label, font=Font.BODY, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(0, Spacing.SM)
        )
        menu = ctk.CTkOptionMenu(
            parent, values=values, command=command,
            fg_color=Color.SURFACE, button_color=Color.PRIMARY, button_hover_color=Color.PRIMARY_HOVER,
            text_color=Color.TEXT_PRIMARY, width=130,
        )
        menu.pack(side="left", padx=(0, Spacing.MD))
        return menu

    def _build_filters_row1(self, parent) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, Spacing.SM))
        self.team_menu = self._dropdown(row, "Division:", TEAM_OPTIONS, self._on_team_filter_changed)
        self.hq_menu = self._dropdown(row, "HQ:", ["All HQs"], self._on_hq_filter_changed)
        self.status_menu = self._dropdown(row, "Status:", STATUS_FILTER_OPTIONS, self._on_status_filter_changed)
        self.days_over_due_menu = self._dropdown(
            row, "Days Over Due:", DAYS_OVER_DUE_BUCKET_OPTIONS, self._on_days_over_due_filter_changed
        )

    def _build_filters_row2(self, parent) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, Spacing.SM))

        ctk.CTkLabel(row, text="Party Name:", font=Font.BODY, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(0, Spacing.SM)
        )
        self.party_search_entry = ctk.CTkEntry(row, placeholder_text="Search party name…", width=220)
        self.party_search_entry.pack(side="left", padx=(0, Spacing.MD))
        self.party_search_entry.bind("<KeyRelease>", lambda event: self._render_table())

        ctk.CTkLabel(row, text="Bill No.:", font=Font.BODY, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(0, Spacing.SM)
        )
        self.invoice_search_entry = ctk.CTkEntry(row, placeholder_text="Search bill number…", width=220)
        self.invoice_search_entry.pack(side="left")
        self.invoice_search_entry.bind("<KeyRelease>", lambda event: self._render_table())

    def _build_filters_row3(self, parent) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        self.due_date_menu = self._dropdown(row, "Due Date:", DUE_DATE_FILTER_OPTIONS, self._on_due_date_filter_changed)
        self.follow_up_menu = self._dropdown(
            row, "Follow-up:", FOLLOW_UP_FILTER_OPTIONS, self._on_follow_up_filter_changed
        )

        SecondaryButton(row, text="Clear All Filters", command=self._on_clear_all_filters).pack(side="left")

        self.custom_range_row = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(
            self.custom_range_row, text="From (YYYY-MM-DD):", font=Font.BODY, text_color=Color.TEXT_SECONDARY
        ).pack(side="left", padx=(0, Spacing.SM))
        self.custom_start_entry = ctk.CTkEntry(self.custom_range_row, width=110, placeholder_text="YYYY-MM-DD")
        self.custom_start_entry.pack(side="left", padx=(0, Spacing.MD))
        self.custom_start_entry.bind("<KeyRelease>", lambda event: self._render_table())

        ctk.CTkLabel(
            self.custom_range_row, text="To (YYYY-MM-DD):", font=Font.BODY, text_color=Color.TEXT_SECONDARY
        ).pack(side="left", padx=(0, Spacing.SM))
        self.custom_end_entry = ctk.CTkEntry(self.custom_range_row, width=110, placeholder_text="YYYY-MM-DD")
        self.custom_end_entry.pack(side="left")
        self.custom_end_entry.bind("<KeyRelease>", lambda event: self._render_table())

    # --- Filter change handlers -------------------------------------------

    def _on_team_filter_changed(self, value: str) -> None:
        self._team_filter = value
        self._render_table()

    def _on_hq_filter_changed(self, value: str) -> None:
        self._hq_filter = value
        self._render_table()

    def _on_status_filter_changed(self, value: str) -> None:
        self._status_filter = value
        self._render_table()

    def _on_days_over_due_filter_changed(self, value: str) -> None:
        self._days_over_due_filter = value
        self._render_table()

    def _on_due_date_filter_changed(self, value: str) -> None:
        self._due_date_filter = value
        if value == "Custom Date Range":
            self.custom_range_row.pack(fill="x", pady=(Spacing.SM, 0))
        else:
            self.custom_range_row.pack_forget()
        self._render_table()

    def _on_follow_up_filter_changed(self, value: str) -> None:
        self._follow_up_filter = value
        self._render_table()

    def _on_clear_all_filters(self) -> None:
        self._team_filter = "All Divisions"
        self._hq_filter = "All HQs"
        self._status_filter = "All"
        self._days_over_due_filter = "All"
        self._due_date_filter = "All"
        self._follow_up_filter = "All"

        self.team_menu.set("All Divisions")
        self.hq_menu.set("All HQs")
        self.status_menu.set("All")
        self.days_over_due_menu.set("All")
        self.due_date_menu.set("All")
        self.follow_up_menu.set("All")
        self.custom_range_row.pack_forget()

        self.party_search_entry.delete(0, "end")
        self.invoice_search_entry.delete(0, "end")
        self.custom_start_entry.delete(0, "end")
        self.custom_end_entry.delete(0, "end")

        self._render_table()

    def _refresh_hq_options(self) -> None:
        options = get_hq_options()
        self.hq_menu.configure(values=options)
        if self._hq_filter not in options:
            self._hq_filter = "All HQs"
            self.hq_menu.set("All HQs")

    # --- Filtering + sorting -----------------------------------------------

    def _get_filtered_invoices(self) -> list[dict]:
        rows = self._invoices

        if self._team_filter != "All Divisions":
            rows = [r for r in rows if r["team"] == self._team_filter]
        if self._hq_filter != "All HQs":
            rows = [r for r in rows if r["hq"] == self._hq_filter]

        if self._status_filter == "Followed Up":
            rows = [r for r in rows if r["followed_up"]]
        elif self._status_filter != "All":
            rows = [r for r in rows if r["status"] == self._status_filter]

        if self._days_over_due_filter != "All":
            rows = [r for r in rows if matches_days_over_due_bucket(r, self._days_over_due_filter)]

        party_query = self.party_search_entry.get().strip().lower()
        if party_query:
            rows = [r for r in rows if party_query in r["party_name"].lower()]

        invoice_query = self.invoice_search_entry.get().strip().lower()
        if invoice_query:
            rows = [r for r in rows if invoice_query in (r["invoice_no"] or "").lower()]

        if self._due_date_filter != "All":
            custom_start = _parse_optional_date(self.custom_start_entry.get())
            custom_end = _parse_optional_date(self.custom_end_entry.get())
            rows = [
                r for r in rows
                if matches_due_date_filter(r, self._due_date_filter, custom_start, custom_end)
            ]

        if self._follow_up_filter == "Pending Follow-up":
            rows = [r for r in rows if not r["followed_up"]]
        elif self._follow_up_filter == "Already Followed Up":
            rows = [r for r in rows if r["followed_up"]]

        return rows

    # --- Main table ------------------------------------------------------

    def _build_table_card(self, outer) -> None:
        self.table_card = Card(outer)
        self.table_card.pack(fill="both", expand=True)

        table_header = ctk.CTkFrame(self.table_card, fg_color="transparent")
        table_header.pack(fill="x", padx=Spacing.MD, pady=(Spacing.MD, 0))
        ctk.CTkLabel(
            table_header, text="Outstanding Invoices", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(side="left")
        self.export_button = PrimaryButton(
            table_header,
            text="Export",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_export_clicked,
        )
        self.export_button.pack(side="right")

        self.table_body = ctk.CTkFrame(self.table_card, fg_color="transparent")
        self.table_body.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)

        self._render_table()

    def _on_export_clicked(self) -> None:
        """Exports `self._current_display_rows` -- the exact list
        `_render_table()` just used to populate the Treeview (built from
        the same per-invoice dict the Treeview's own `values=` tuple
        reads)."""
        export_rows_with_ui(
            self,
            rows=self._current_display_rows,
            columns=COLUMNS,
            headings=HEADINGS,
            suggested_filename=default_export_filename("Collections"),
            sheet_title="Collections",
            row_style_fn=_collections_row_style,
        )

    def _render_table(self) -> None:
        for widget in self.table_body.winfo_children():
            widget.destroy()
        self._current_display_rows = []

        if not self._invoices:
            self._render_kpi_cards(summarize_invoices([]))
            EmptyState(
                self.table_body, "No outstanding invoices yet -- upload an Outstanding Report above."
            ).pack(fill="both", expand=True)
            return

        filtered = self._get_filtered_invoices()
        self._render_kpi_cards(summarize_invoices(filtered))

        if not filtered:
            EmptyState(self.table_body, "No invoices match the current filters.").pack(fill="both", expand=True)
            return

        ordered = sort_invoices(filtered)

        tree = styled_treeview(self.table_body, COLUMNS, HEADINGS, WIDTHS, height=14)
        tree.column("followed_up", anchor="center")
        for status, tag in STATUS_ROW_TAG.items():
            tree.tag_configure(tag, background=STATUS_ROW_COLOR[status])
        tree.tag_configure("followed_up_row", background=Color.SURFACE, foreground=Color.TEXT_MUTED)

        for invoice in ordered:
            # The one place these values are computed -- both the Treeview's
            # own `values=` tuple below and the exported row (Milestone 55)
            # read from this same dict. "_followed_up" is not in COLUMNS, so
            # it's invisible to the exported spreadsheet -- it only drives
            # _collections_row_style.
            display_row = {
                "followed_up": CHECKED_GLYPH if invoice["followed_up"] else UNCHECKED_GLYPH,
                "party_name": invoice["party_name"],
                "team": invoice["team"] or "—",
                "invoice_no": invoice["invoice_no"] or "—",
                "bill_amount": f"₹{invoice['bill_amount']:,.2f}" if invoice["bill_amount"] is not None else "—",
                "lr_date": invoice["lr_date"].strftime("%Y-%m-%d"),
                "due_date": invoice["due_date"].strftime("%Y-%m-%d"),
                "days_outstanding": invoice["days_outstanding"],
                "days_over_due": invoice["days_over_due"],
                "status": invoice["status"],
                "_followed_up": invoice["followed_up"],
            }
            self._current_display_rows.append(display_row)

            tags = ("followed_up_row",) if invoice["followed_up"] else (STATUS_ROW_TAG.get(invoice["status"], ""),)
            tree.insert(
                "", "end", iid=str(invoice["id"]), tags=tags,
                values=tuple(display_row[col] for col in COLUMNS),
            )
        tree.bind("<Button-1>", self._on_tree_click)
        tree.pack(fill="both", expand=True)
        self.tree = tree

    def _on_tree_click(self, event) -> None:
        tree = event.widget
        if tree.identify_region(event.x, event.y) != "cell":
            return
        if tree.identify_column(event.x) != "#1":  # only the Follow-up column toggles
            return
        row_id = tree.identify_row(event.y)
        if not row_id:
            return
        self._on_toggle_followup(int(row_id))

    def _on_toggle_followup(self, invoice_id: int) -> None:
        invoice = next((inv for inv in self._invoices if inv["id"] == invoice_id), None)
        if invoice is None:
            return
        invoice["followed_up"] = not invoice["followed_up"]
        set_follow_up(invoice_id, invoice["followed_up"])
        self._render_table()
        self._push_followup_in_background(invoice_id)

    def _push_followup_in_background(self, invoice_id: int) -> None:
        def worker() -> None:
            try:
                from app.payment_sync_service import push_outstanding_invoice_followup

                push_outstanding_invoice_followup(invoice_id)
            except Exception as exc:
                logger.error(f"Failed to sync follow-up state for invoice_id={invoice_id}: {exc}")

        threading.Thread(target=worker, daemon=True).start()
