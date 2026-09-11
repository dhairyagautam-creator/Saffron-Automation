"""Inventory Upload page: the Inventory Report upload workflow, on its own
page. Validated via app/excel_validation.py, then fed into
app/replenishment_service.py to compare against the Threshold Database and
identify which products need replenishment -- see that module's docstring
for exactly what "evaluate replenishment" means (effective available
stock, the comparison rule) and what it does NOT do yet (no
notifications, no automatic replenishment).

Sync (see docs/SYNC_DESIGN.md, docs/INVENTORY_SYNC_CONTEXT.md): the upload
now goes through app.inventory_sync_service.upload_and_sync(), which --
via app.inventory_upload_service.apply_inventory_report() -- first
recomputes thresholds fresh from whichever Sales Report is currently
retained on this machine (never a standing cached value), then evaluates
replenishment/CWH, then retains this Inventory Report locally and pushes
it to the same manifest/Storage mechanism app/review_sync_service.py
already proved. Local validation/evaluation logic itself is unchanged.

Validation + threshold recompute + replenishment/CWH evaluation all run on
a background thread (see ui/background_task.py) behind a loading overlay
(ui/loading_overlay.py), so the window never looks frozen while a large
workbook is processed.

Emails are no longer sent automatically after upload -- see
ui/inventory_automated_emails_page.py's "Send Emails" button, the only
place app.inventory_notification_service.send_inventory_replenishment_emails
is called from.

UI state after a sync pull (see docs/SYNC_DESIGN.md, ui/review_uploads_page.py's
same pattern): refresh_from_state() renders this slot's CURRENTLY PERSISTED
state (app.inventory_upload_service.get_slot_state), not "whatever this
widget's own last upload happened to return" -- called after this page's
own upload completes, after construction (so a restarted app immediately
shows an already-retained file, not "No file selected"), and by
ui/inventory_uploads_page.py after a pull applies this slot. A pulled file
therefore renders identically to a locally uploaded one -- same filename,
same green success text -- with no visual way to tell them apart, exactly
matching Review System's own slot rendering.

Remove button: local-only, byte-for-byte the same behavior as Review
System's own per-slot Remove button (app.review_upload_service.
remove_review_file) -- see app.inventory_upload_service.remove_inventory_file.
"""

from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable

import customtkinter as ctk
from loguru import logger

from app.excel_validation import SUPPORTED_EXTENSIONS
from app.inventory_sync_service import upload_and_sync
from app.inventory_upload_service import INVENTORY_REPORT_SLOT, get_slot_state, remove_inventory_file
from app.replenishment_service import get_replenishment_summary
from ui.background_task import run_in_background
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader
from ui.icons import get_icon
from ui.loading_overlay import LoadingOverlay
from ui.theme import Color, Font, Spacing


class InventoryUploadPage(ctk.CTkFrame):
    """Upload workflow for the current inventory report."""

    def __init__(self, master, on_uploaded: Callable[[], None] | None = None) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._inventory_report_path: str | None = None
        self._loaded_df = None
        self._on_uploaded = on_uploaded
        self._build_widgets()
        self.loading_overlay = LoadingOverlay(self)
        self.refresh_from_state()

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
        self.file_label.pack(side="left", padx=(Spacing.MD, 0), fill="x", expand=True)

        self.remove_button = SecondaryButton(
            action_row, text="Remove", width=80, height=28, font=Font.SMALL_BOLD, state="disabled",
            text_color=Color.ERROR, border_color=Color.ERROR,
            command=self._on_remove_clicked,
        )
        self.remove_button.pack(side="right")

        self.status_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w"
        )
        self.status_label.pack(anchor="w", pady=(Spacing.SM, 0))

    def refresh_from_state(self) -> None:
        """Renders this slot's CURRENTLY PERSISTED state -- see module
        docstring. Safe to call any time (construction, after this page's
        own upload, or from ui/inventory_uploads_page.py after a pull)."""
        state = get_slot_state(INVENTORY_REPORT_SLOT)
        if not state["uploaded"]:
            self.file_label.configure(text="No file selected", text_color=Color.TEXT_MUTED)
            self.status_label.configure(text="", text_color=Color.TEXT_SECONDARY)
            self.remove_button.configure(state="disabled")
            return

        self.file_label.configure(text=state["filename"], text_color=Color.TEXT_PRIMARY)
        summary = get_replenishment_summary()
        self.status_label.configure(
            text=(
                "Inventory Report validated successfully. "
                f"{summary['total_evaluated']} product(s) evaluated, "
                f"{summary['requiring_replenishment']} requiring replenishment."
            ),
            text_color=Color.SUCCESS,
        )
        self.remove_button.configure(state="normal")

    def _on_remove_clicked(self) -> None:
        state = get_slot_state(INVENTORY_REPORT_SLOT)
        filename = state.get("filename") or "this file"
        if not messagebox.askyesno("Remove File", f"Remove '{filename}' from this slot?"):
            return
        remove_inventory_file(INVENTORY_REPORT_SLOT)
        self._inventory_report_path = None
        self._loaded_df = None
        self.refresh_from_state()
        if self._on_uploaded:
            self._on_uploaded()

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
            report_progress(20, "Validating...")
            result = upload_and_sync(INVENTORY_REPORT_SLOT, file_path)
            report_progress(85, "Evaluating replenishment...")
            report_progress(98, "Syncing...")
            report_progress(100, "Done")
            return result

        def on_progress(percent, message):
            self.loading_overlay.update_progress(percent, message)

        def on_done(result, error):
            self.browse_button.configure(state="normal")

            if error is not None:
                logger.error(f"Unexpected error validating Inventory Report '{file_path}': {error}")
                self.loading_overlay.hide()
                messagebox.showerror("Import Failed", f"Could not open the selected file.\n\n{error}")
                return

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
            self.refresh_from_state()
            if not result.get("synced"):
                messagebox.showwarning("Not Synced", result.get("sync_error") or "This file was not synced to the cloud.")
            if self._on_uploaded:
                self._on_uploaded()
            # Let the bar's animation to 100% actually finish (and be
            # briefly visible) before the overlay disappears.
            self.after(400, self.loading_overlay.hide)

        run_in_background(self, work, on_progress=on_progress, on_done=on_done)
