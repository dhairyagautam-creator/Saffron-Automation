"""Payment Analytics Historical Upload page.

Two real upload + validation + engine pipelines, built the same way as
the Validator/Inventory modules: pick a file, validate its required
columns, run it through app/payment_analytics_service.py, all on a
background thread behind a loading overlay, then show a read-only
summary.

- Historical Reports: the one-time (or re-run) baseline load -- replaces
  the entire stored dataset.
- Monthly Report: the rolling six-month window's incremental update --
  validates which month is being uploaded, accepts/rejects it per the
  rules in app/payment_analytics_service.process_monthly_report(), and on
  acceptance evicts the oldest active month and recalculates every
  customer profile, Dashboard KPI, and chart automatically. No manual
  refresh anywhere -- every other Payment Analytics page reads straight
  from the database tables this updates.
"""

from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from loguru import logger

from app.excel_validation import SUPPORTED_EXTENSIONS, load_historical_payment_report, load_monthly_payment_report
from app.payment_analytics_service import process_historical_report, process_monthly_report
from app.payment_sync_service import sync_active_months, sync_customer_profiles, sync_invoices
from ui.background_task import run_in_background
from ui.components import (
    Card,
    PrimaryButton,
    SecondaryButton,
    SectionHeader,
    render_error_banner as _render_error_banner,
    render_kpi_row as _render_kpi_row,
    render_success_banner as _render_success_banner,
    render_warning_banner as _render_warning_banner,
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

HISTORICAL_SUMMARY_SPECS = ["Total Rows", "Total Customers", "Total Invoices", "Upload Date & Time"]
MONTHLY_SUMMARY_SPECS = [
    "Uploaded Month", "Removed Month", "Active Window", "Total Customers Updated", "Total Invoices in Window",
]


class _HistoricalReportCard(Card):
    """Upload + validate the single active Historical Payment Report --
    the baseline. Only one report is active at a time -- uploading a new
    one replaces the entire stored dataset, clearing its banner and
    summary."""

    def __init__(self, master, on_continue) -> None:
        super().__init__(master)
        self._on_continue = on_continue
        self._df = None

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Upload Historical Reports", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "Select the historical payment report workbook. Only one report is active "
                "at a time -- uploading a new one replaces it."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=600,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        action_row = ctk.CTkFrame(body, fg_color="transparent")
        action_row.pack(fill="x")

        self.browse_button = PrimaryButton(
            action_row,
            text="Upload Historical Reports",
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

        self.continue_button = PrimaryButton(
            body, text="Continue", state="disabled", command=self._on_continue_clicked
        )
        self.continue_button.pack(anchor="w", pady=(Spacing.MD, 0))

        self.loading_overlay = LoadingOverlay(self)

    # --- Upload + validation ------------------------------------------------

    def _on_browse_clicked(self) -> None:
        file_path = filedialog.askopenfilename(title="Select Historical Payment Report", filetypes=FILE_TYPES)
        if not file_path:
            return

        self._df = None
        self.continue_button.configure(state="disabled")
        self._clear_result()
        self._clear_summary()
        self.file_label.configure(text=Path(file_path).name, text_color=Color.TEXT_PRIMARY)

        if Path(file_path).suffix.lower() not in SUPPORTED_EXTENSIONS:
            _render_error_banner(
                self.result_container, "Unsupported File Type",
                ["Please upload an Excel (.xlsx, .xls, .xlsm) or CSV (.csv) file."],
            )
            return

        self.browse_button.configure(state="disabled")
        self.loading_overlay.show()

        def work(report_progress):
            result = load_historical_payment_report(file_path, progress_callback=report_progress)
            if result["success"]:
                report_progress(97, "Processing invoices...")
                result["engine"] = process_historical_report(result["df"])
                report_progress(99, "Syncing to the cloud...")
                try:
                    sync_invoices()
                    sync_active_months()
                    sync_customer_profiles()
                except Exception as exc:
                    logger.error(f"Failed to sync historical payment data to the cloud: {exc}")
            return result

        def on_progress(percent, message):
            self.loading_overlay.update_progress(percent, message)

        def on_done(result, error):
            self.browse_button.configure(state="normal")
            self.loading_overlay.hide()

            if error is not None:
                _render_error_banner(self.result_container, "Import Failed", [f"Could not open the selected file. {error}"])
                return

            if not result["success"]:
                if result["missing_columns"]:
                    _render_error_banner(self.result_container, "Missing Columns", result["missing_columns"])
                else:
                    _render_error_banner(
                        self.result_container, "Import Failed", [result["error"] or "Could not read the selected file."]
                    )
                return

            self._df = result["df"]
            engine_result = result["engine"]
            self._show_success(engine_result)
            self._show_summary(self._df, engine_result)
            self.continue_button.configure(state="normal")

        run_in_background(self, work, on_progress=on_progress, on_done=on_done)

    def _on_continue_clicked(self) -> None:
        if self._df is not None and self._on_continue is not None:
            self._on_continue()

    # --- Result banner --------------------------------------------------------

    def _clear_result(self) -> None:
        for widget in self.result_container.winfo_children():
            widget.destroy()

    def _show_success(self, engine_result: dict) -> None:
        self._clear_result()
        _render_success_banner(self.result_container, "Historical payment report validated successfully.")
        skipped = engine_result["rows_skipped"]
        if skipped:
            _render_warning_banner(
                self.result_container,
                f"{skipped} row(s) skipped due to a missing/invalid LR Date or Clear Date "
                "-- see the application log for details.",
            )

    # --- Summary card --------------------------------------------------------

    def _clear_summary(self) -> None:
        for widget in self.summary_container.winfo_children():
            widget.destroy()
        self.summary_container.pack_forget()

    def _show_summary(self, df, engine_result: dict) -> None:
        self._clear_summary()
        self.summary_container.pack(fill="x", pady=(Spacing.MD, 0))

        ctk.CTkLabel(
            self.summary_container, text="Upload Summary", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        total_rows = len(df)
        # Total Customers / Total Invoices come from the engine's own valid,
        # grouped ledger (post-Payment-Days-validation) rather than a naive
        # nunique() over the raw upload -- a row skipped for a bad date
        # should never count toward either figure.
        total_customers = engine_result["customers_count"]
        total_invoices = engine_result["rows_valid"]
        uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M")

        values = [f"{total_rows:,}", f"{total_customers:,}", f"{total_invoices:,}", uploaded_at]
        _render_kpi_row(self.summary_container, HISTORICAL_SUMMARY_SPECS, values)

        if engine_result["months_archived"]:
            ctk.CTkLabel(
                self.summary_container,
                text=(
                    f"Active window: {' → '.join(engine_result['active_window'])} -- "
                    f"{engine_result['months_archived']} older month(s) archived (kept, but excluded from analytics)."
                ),
                font=Font.SMALL,
                text_color=Color.TEXT_MUTED,
                anchor="w",
                wraplength=900,
                justify="left",
            ).pack(anchor="w", pady=(Spacing.SM, 0))


class _MonthlyReportCard(Card):
    """Upload the rolling window's newest month. Validates the month
    against the current active window (app.payment_analytics_service.
    process_monthly_report) and, on acceptance, evicts the oldest month
    and recalculates every customer profile -- the Dashboard and Customer
    Analytics pages pick this up automatically the next time they're
    shown, no manual refresh."""

    def __init__(self, master, on_view_dashboard) -> None:
        super().__init__(master)
        self._on_view_dashboard = on_view_dashboard

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Upload Monthly Report", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "Select the current month's payment report workbook. If it's the next chronological "
                "month, it's added and the oldest active month is automatically removed."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=600,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        action_row = ctk.CTkFrame(body, fg_color="transparent")
        action_row.pack(fill="x")

        self.browse_button = PrimaryButton(
            action_row,
            text="Upload Monthly Report",
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
        file_path = filedialog.askopenfilename(title="Select Monthly Payment Report", filetypes=FILE_TYPES)
        if not file_path:
            return

        self._clear_result()
        self._clear_summary()
        self.file_label.configure(text=Path(file_path).name, text_color=Color.TEXT_PRIMARY)

        if Path(file_path).suffix.lower() not in SUPPORTED_EXTENSIONS:
            _render_error_banner(
                self.result_container, "Unsupported File Type",
                ["Please upload an Excel (.xlsx, .xls, .xlsm) or CSV (.csv) file."],
            )
            return

        self.browse_button.configure(state="disabled")
        self.loading_overlay.show()

        def work(report_progress):
            result = load_monthly_payment_report(file_path, progress_callback=report_progress)
            if result["success"]:
                report_progress(97, "Checking rolling window...")
                result["engine"] = process_monthly_report(result["df"])
                if result["engine"]["success"]:
                    report_progress(99, "Syncing to the cloud...")
                    try:
                        sync_invoices()
                        sync_active_months()
                        sync_customer_profiles()
                    except Exception as exc:
                        logger.error(f"Failed to sync monthly payment data to the cloud: {exc}")
            return result

        def on_progress(percent, message):
            self.loading_overlay.update_progress(percent, message)

        def on_done(result, error):
            self.browse_button.configure(state="normal")
            self.loading_overlay.hide()

            if error is not None:
                _render_error_banner(self.result_container, "Import Failed", [f"Could not open the selected file. {error}"])
                return

            if not result["success"]:
                if result["missing_columns"]:
                    _render_error_banner(self.result_container, "Missing Columns", result["missing_columns"])
                else:
                    _render_error_banner(
                        self.result_container, "Import Failed", [result["error"] or "Could not read the selected file."]
                    )
                return

            engine_result = result["engine"]
            if not engine_result["success"]:
                _render_error_banner(self.result_container, "Upload Rejected", [engine_result["message"]])
                if engine_result["rows_skipped"]:
                    _render_warning_banner(
                        self.result_container,
                        f"{engine_result['rows_skipped']} row(s) in this file were also skipped -- see the "
                        "application log for details.",
                    )
                return

            self._show_success(engine_result)
            self._show_summary(engine_result)

        run_in_background(self, work, on_progress=on_progress, on_done=on_done)

    # --- Result banner --------------------------------------------------------

    def _clear_result(self) -> None:
        for widget in self.result_container.winfo_children():
            widget.destroy()

    def _show_success(self, engine_result: dict) -> None:
        self._clear_result()
        _render_success_banner(
            self.result_container, f"{engine_result['uploaded_month']} added to the active window."
        )
        if engine_result["rows_skipped"]:
            _render_warning_banner(
                self.result_container,
                f"{engine_result['rows_skipped']} row(s) skipped (invalid dates, or didn't belong to "
                f"{engine_result['uploaded_month']}) -- see the application log for details.",
            )

    # --- Summary card --------------------------------------------------------

    def _clear_summary(self) -> None:
        for widget in self.summary_container.winfo_children():
            widget.destroy()
        self.summary_container.pack_forget()

    def _show_summary(self, engine_result: dict) -> None:
        self._clear_summary()
        self.summary_container.pack(fill="x", pady=(Spacing.MD, 0))

        ctk.CTkLabel(
            self.summary_container, text="Monthly Upload Summary", font=Font.H3, text_color=Color.TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w", pady=(0, Spacing.SM))

        window = engine_result["active_window"]
        active_window_display = f"{window[0]} → {window[-1]}" if len(window) > 1 else (window[0] if window else "—")

        values = [
            engine_result["uploaded_month"],
            engine_result["removed_month"] or "None (window not yet full)",
            active_window_display,
            f"{engine_result['total_customers_updated']:,}",
            f"{engine_result['total_invoices_in_window']:,}",
        ]
        _render_kpi_row(self.summary_container, MONTHLY_SUMMARY_SPECS, values)

        if self._on_view_dashboard is not None:
            SecondaryButton(self.summary_container, text="View Dashboard", command=self._on_view_dashboard).pack(
                anchor="w", pady=(Spacing.SM, 0)
            )


class PaymentUploadPage(ctk.CTkFrame):
    """Historical Upload page: the baseline load, plus the rolling
    six-month window's monthly upload."""

    def __init__(self, master, on_continue=None, on_view_dashboard=None) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._build_widgets(on_continue, on_view_dashboard)

    def _build_widgets(self, on_continue, on_view_dashboard) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Historical Upload", "Upload historical and monthly payment reports"
        ).pack(anchor="w", pady=(0, Spacing.LG))

        self.historical_report_card = _HistoricalReportCard(outer, on_continue=on_continue)
        self.historical_report_card.pack(fill="x", pady=(0, Spacing.MD))

        self.monthly_report_card = _MonthlyReportCard(outer, on_view_dashboard=on_view_dashboard)
        self.monthly_report_card.pack(fill="x")
