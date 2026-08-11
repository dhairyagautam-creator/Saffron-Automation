"""Inventory Upload page: the Inventory Report upload workflow, on its own
page. Validated via app/excel_validation.py, then fed into
app/replenishment_service.py to compare against the already-generated
Threshold Database and identify which products need replenishment -- see
that module's docstring for exactly what "evaluate replenishment" means
(effective available stock, the comparison rule) and what it does NOT do
yet (no notifications, no automatic replenishment).

Validation + replenishment evaluation both run on a background thread
(see ui/background_task.py) behind a loading overlay
(ui/loading_overlay.py), so the window never looks frozen while a large
workbook is processed.

Automatic email sending (Version 2.0, Milestone 56): once replenishment
evaluation finishes successfully, if Automatic Email Sending is enabled
(see ui/inventory_settings_page.py's Email Configuration card), the
Inventory Automated Email System (app/inventory_notification_service.py)
runs on its OWN separate background thread -- mirroring
ui/operations_page.py's own `_start_automatic_send` pattern exactly: the
upload's own progress bar/loading overlay already finished and hid itself,
so a slow email send (SMTP + attachment generation for every configured
recipient) never blocks or re-shows it. If Automatic Email Sending is
off, nothing further happens automatically -- recipients and their saved
credentials are still configured on Settings/Automated Emails for the
next time it's turned on.
"""

import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from loguru import logger

from app.cwh_service import evaluate_cwh_stock
from app.excel_validation import SUPPORTED_EXTENSIONS, validate_inventory_report
from app.inventory_email_settings_service import is_automatic_sending_enabled
from app.inventory_notification_service import send_inventory_replenishment_emails
from app.inventory_send_state import finish_sending, start_sending
from app.inventory_sync_service import push_replenishment_full_replace
from app.replenishment_service import evaluate_replenishment
from ui.background_task import run_in_background
from ui.components import Card, PrimaryButton, SectionHeader
from ui.icons import get_icon
from ui.loading_overlay import LoadingOverlay
from ui.theme import Color, Font, Spacing


class InventoryUploadPage(ctk.CTkFrame):
    """Upload workflow for the current inventory report."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._inventory_report_path: str | None = None
        self._loaded_df = None
        self._build_widgets()
        self.loading_overlay = LoadingOverlay(self)

    def _build_widgets(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Inventory Upload", "Upload the current inventory report workbook"
        ).pack(anchor="w", pady=(0, Spacing.LG))

        card = Card(outer)
        card.pack(fill="x")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Upload Inventory Report", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text="Select the current inventory report workbook for this branch cycle.",
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
            text="Browse Report",
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
            title="Select Inventory Report",
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
            logger.info("Import Inventory Report cancelled by user")
            return

        if Path(file_path).suffix.lower() not in SUPPORTED_EXTENSIONS:
            logger.warning(f"Rejected unsupported file type for Inventory Report: {file_path}")
            messagebox.showerror(
                "Unsupported File Type",
                "Unsupported file type. Please upload an Excel (.xlsx, .xls, .xlsm) or CSV (.csv) file.",
            )
            return

        logger.info(f"Loading Inventory Report: {file_path}")
        self.browse_button.configure(state="disabled")
        self.loading_overlay.show()

        def work(report_progress):
            # Rescale the engine's own 0-100 progress into 0-80, leaving
            # room for the replenishment-evaluation phase that follows
            # within this same overall progress bar.
            def load_progress(percent, message):
                report_progress(percent * 0.8, message)

            load_result = validate_inventory_report(file_path, progress_callback=load_progress)
            if not load_result["success"] or load_result["error"] is not None:
                return {"load": load_result, "replenishment_stats": None}

            report_progress(85, "Evaluating replenishment...")
            replenishment_stats = evaluate_replenishment(load_result["df"])
            # Additive only -- captures Ahmedabad CWH's own physical stock
            # (app/cwh_service.py) from the exact rows evaluate_replenishment()
            # just excluded, for the separate Central Warehouse page. Guarded
            # so a bug here can never break the existing replenishment result
            # the user is waiting on, same defensive pattern as the cloud
            # sync call just below.
            try:
                evaluate_cwh_stock(load_result["df"])
            except Exception as exc:
                logger.error(f"Failed to evaluate Ahmedabad CWH stock: {exc}")
            report_progress(92, "Syncing to the cloud...")
            try:
                # Full-replace push, not the ordinary Last-Modified-Wins
                # sync_replenishment() -- this upload just replaced the
                # entire local table with a fresh snapshot (see
                # app/replenishment_service.evaluate_replenishment()), so
                # the cloud must be cleared and re-pushed to match, not
                # reconciled row-by-row (which has no delete concept and
                # would pull stale rows back).
                push_replenishment_full_replace()
            except Exception as exc:
                logger.error(f"Failed to sync inventory replenishment to the cloud: {exc}")
            report_progress(98, "Finalizing...")
            report_progress(100, "Done")
            return {"load": load_result, "replenishment_stats": replenishment_stats}

        def on_progress(percent, message):
            self.loading_overlay.update_progress(percent, message)

        def on_done(work_result, error):
            self.browse_button.configure(state="normal")

            if error is not None:
                logger.error(f"Unexpected error validating Inventory Report '{file_path}': {error}")
                self.loading_overlay.hide()
                messagebox.showerror("Import Failed", f"Could not open the selected file.\n\n{error}")
                return

            result = work_result["load"]

            if result["error"] is not None:
                logger.error(f"Failed to load Inventory Report '{file_path}': {result['error']}")
                self.loading_overlay.hide()
                messagebox.showerror("Import Failed", f"Could not open the selected file.\n\n{result['error']}")
                return

            if not result["success"]:
                self.loading_overlay.hide()
                # Deliberate deviation from the Path Validator's plain
                # "\n".join(...) missing-columns format -- this bulleted
                # layout matches the exact format specified for Inventory
                # Report uploads.
                bullet_list = "\n".join(f"• {col}" for col in result["missing_columns"])
                messagebox.showerror(
                    "Invalid Inventory Report",
                    "The uploaded Inventory Report is invalid.\n"
                    f"Missing columns:\n{bullet_list}\n\n"
                    "Please upload a valid Inventory Report.",
                )
                self.status_label.configure(text="", text_color=Color.TEXT_SECONDARY)
                return

            self._inventory_report_path = file_path
            self._loaded_df = result["df"]
            self.file_label.configure(text=Path(file_path).name, text_color=Color.TEXT_PRIMARY)

            stats = work_result["replenishment_stats"]
            summary_text = (
                "Inventory Report validated successfully. "
                f"{stats['evaluated']} product(s) evaluated, "
                f"{stats['replenishment_required']} requiring replenishment."
            )
            self.status_label.configure(text=summary_text, text_color=Color.SUCCESS)
            # Let the bar's animation to 100% actually finish (and be
            # briefly visible) before the overlay disappears.
            self.after(400, self.loading_overlay.hide)

            if is_automatic_sending_enabled():
                self._start_automatic_send(summary_text)

        run_in_background(self, work, on_progress=on_progress, on_done=on_done)

    # --- Automatic Email Sending (Milestone 56) -----------------------------

    def _start_automatic_send(self, base_summary_text: str) -> None:
        """Runs the Inventory Automated Email System on its own background
        thread -- deliberately separate from `run_in_background` above
        (which already finished and is hiding its own loading overlay), so
        a slow SMTP send never blocks or re-shows the upload's progress
        bar. Mirrors ui/operations_page.py's own _start_automatic_send."""
        self.status_label.configure(
            text=base_summary_text + " Sending Replenishment report emails in the background…",
            text_color=Color.SUCCESS,
        )
        start_sending()

        def worker() -> None:
            try:
                result = send_inventory_replenishment_emails()
            except Exception as exc:
                logger.error(f"Automatic Inventory email sending failed: {exc}")
                # `exc` (the `except ... as exc` name) is implicitly deleted
                # the moment this except block exits -- assign it to a plain
                # local first so the lambda's closure still has something to
                # read when Tk actually invokes it later via self.after(0, ...).
                error = exc
                self.after(0, lambda: self._on_automatic_send_done(base_summary_text, error=error))
            else:
                self.after(0, lambda: self._on_automatic_send_done(base_summary_text, result=result))

        threading.Thread(target=worker, daemon=True).start()

    def _on_automatic_send_done(
        self, base_summary_text: str, result: dict | None = None, error: Exception | None = None
    ) -> None:
        finish_sending()
        if not self.winfo_exists():
            return

        if error is not None:
            self.status_label.configure(
                text=base_summary_text + f" Automatic email sending failed: {error}", text_color=Color.ERROR
            )
            return

        self.status_label.configure(
            text=(
                base_summary_text
                + f" {result['sent_count']:,} Replenishment report email(s) sent automatically"
                + (f", {result['failed_count']:,} failed" if result["failed_count"] else "")
                + (f", {result['skipped_count']:,} skipped (no matching data)" if result["skipped_count"] else "")
                + "."
            ),
            text_color=Color.SUCCESS if not result["failed_count"] else Color.WARNING,
        )
