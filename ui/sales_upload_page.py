"""Previous Month Sales Upload page: the sales report upload workflow, on
its own page. Loaded and validated via app/excel_validation.py, then fed
into app/threshold_service.py to generate/update replenishment thresholds
-- see that module's docstring for exactly what "generate thresholds"
means (grouping, consolidation, the 1.5x multiplier) and what it does NOT
do yet (no comparison against inventory, no replenishment alerts).

Loading + threshold generation both run on a background thread (see
ui/background_task.py) behind a loading overlay (ui/loading_overlay.py),
so the window never looks frozen while a large workbook is processed.
"""

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from loguru import logger

from app.excel_validation import SUPPORTED_EXTENSIONS, load_sales_report
from app.inventory_sync_service import push_thresholds_full_replace
from app.threshold_service import generate_thresholds_from_sales
from ui.background_task import run_in_background
from ui.components import Card, PrimaryButton, SectionHeader
from ui.icons import get_icon
from ui.loading_overlay import LoadingOverlay
from ui.theme import Color, Font, Spacing


class SalesUploadPage(ctk.CTkFrame):
    """Upload workflow for the previous month's sales report."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._sales_report_path: str | None = None
        self._loaded_df = None
        self._build_widgets()
        self.loading_overlay = LoadingOverlay(self)

    def _build_widgets(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Previous Month Sales Upload", "Upload last month's sales report workbook"
        ).pack(anchor="w", pady=(0, Spacing.LG))

        card = Card(outer)
        card.pack(fill="x")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Upload Previous Month Sales Report", font=Font.H3,
            text_color=Color.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text="Select last month's sales report used to calculate replenishment thresholds.",
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
            text="Browse Sales Report",
            image=get_icon("upload", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_browse_clicked,
        )
        self.browse_button.pack(side="left")

        self.file_label = ctk.CTkLabel(
            action_row, text="No file selected", font=Font.BODY, text_color=Color.TEXT_MUTED, anchor="w"
        )
        self.file_label.pack(side="left", padx=(Spacing.MD, 0))

        self.status_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w"
        )
        self.status_label.pack(anchor="w", pady=(Spacing.SM, 0))

    def _on_browse_clicked(self) -> None:
        self.status_label.configure(text="", text_color=Color.TEXT_SECONDARY)
        self._loaded_df = None

        file_path = filedialog.askopenfilename(
            title="Select Previous Month Sales Report",
            filetypes=[
                ("Supported files", "*.xlsx *.xls *.xlsm *.csv"),
                ("Excel Workbook (*.xlsx)", "*.xlsx"),
                ("Excel 97-2003 Workbook (*.xls)", "*.xls"),
                ("Excel Macro-Enabled Workbook (*.xlsm)", "*.xlsm"),
                ("CSV files (*.csv)", "*.csv"),
                ("All files", "*.*"),
            ],
        )
        if not file_path:
            logger.info("Import Previous Month Sales Report cancelled by user")
            return

        if Path(file_path).suffix.lower() not in SUPPORTED_EXTENSIONS:
            logger.warning(f"Rejected unsupported file type for Previous Month Sales Report: {file_path}")
            messagebox.showerror(
                "Unsupported File Type",
                "Unsupported file type. Please upload an Excel (.xlsx, .xls, .xlsm) or CSV (.csv) file.",
            )
            return

        logger.info(f"Loading Previous Month Sales Report: {file_path}")
        self.browse_button.configure(state="disabled")
        self.loading_overlay.show()

        def work(report_progress):
            # Rescale the engine's own 0-100 progress into 0-80, leaving
            # room for the threshold-generation phase that follows within
            # this same overall progress bar.
            def load_progress(percent, message):
                report_progress(percent * 0.8, message)

            load_result = load_sales_report(file_path, progress_callback=load_progress)
            if not load_result["success"] or load_result["error"] is not None:
                return {"load": load_result, "threshold_stats": None}

            report_progress(85, "Generating thresholds...")
            threshold_stats = generate_thresholds_from_sales(load_result["df"])
            report_progress(92, "Syncing to the cloud...")
            try:
                # Full-replace push, not the ordinary Last-Modified-Wins
                # sync_thresholds() -- this upload just replaced the
                # entire local table with a fresh snapshot (see
                # app/threshold_service.generate_thresholds_from_sales()),
                # so the cloud must be cleared and re-pushed to match, not
                # reconciled row-by-row (which has no delete concept and
                # would pull stale rows back).
                push_thresholds_full_replace()
            except Exception as exc:
                logger.error(f"Failed to sync inventory thresholds to the cloud: {exc}")
            report_progress(98, "Finalizing...")
            report_progress(100, "Done")
            return {"load": load_result, "threshold_stats": threshold_stats}

        def on_progress(percent, message):
            self.loading_overlay.update_progress(percent, message)

        def on_done(work_result, error):
            self.browse_button.configure(state="normal")

            if error is not None:
                logger.error(f"Unexpected error loading Previous Month Sales Report '{file_path}': {error}")
                self.loading_overlay.hide()
                messagebox.showerror("Import Failed", f"Could not open the selected file.\n\n{error}")
                return

            result = work_result["load"]

            if result["error"] is not None:
                logger.error(f"Failed to load Previous Month Sales Report '{file_path}': {result['error']}")
                self.loading_overlay.hide()
                messagebox.showerror("Import Failed", f"Could not open the selected file.\n\n{result['error']}")
                return

            if not result["success"]:
                self.loading_overlay.hide()
                bullet_list = "\n".join(f"• {col}" for col in result["missing_columns"])
                messagebox.showerror(
                    "Invalid Previous Month Sales Report",
                    "The uploaded Previous Month Sales Report is invalid.\n"
                    f"Missing columns:\n{bullet_list}\n\n"
                    "Please upload a valid Previous Month Sales Report.",
                )
                self.status_label.configure(text="", text_color=Color.TEXT_SECONDARY)
                return

            self._sales_report_path = file_path
            self._loaded_df = result["df"]
            self.file_label.configure(text=Path(file_path).name, text_color=Color.TEXT_PRIMARY)

            threshold_stats = work_result["threshold_stats"]
            self.status_label.configure(
                text=(
                    "Previous Month Sales Report validated successfully. "
                    f"{threshold_stats['unique_combinations']} threshold(s) generated."
                ),
                text_color=Color.SUCCESS,
            )
            # Let the bar's animation to 100% actually finish (and be
            # briefly visible) before the overlay disappears.
            self.after(400, self.loading_overlay.hide)

        run_in_background(self, work, on_progress=on_progress, on_done=on_done)
