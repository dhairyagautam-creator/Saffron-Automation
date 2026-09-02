"""Review System -- Hierarchy and HQ Distribution page.

Two sections:
  1. Hierarchy -- the SAME shared employee_hierarchy dataset Path
     Validator's Organization Data page manages (app.workbook_connections
     + app.hierarchy_parser.refresh_hierarchy()) -- there is exactly one
     hierarchy dataset in the application; this page is a THIRD place to
     view/refresh it (the first is ui/organization_data_page.py, the
     second is ui/work_distribution_email_center_page.py's Hierarchy
     Workbooks card, whose exact card layout + the shared
     ui.hierarchy_table_section.HierarchyTableSection this section copies
     verbatim, 2026-08-19 -- no new hierarchy business logic here).
  2. HQ Distribution -- upload area for the master file mapping which
     (Division, Region, HQ) combinations actually exist (see
     app/hq_distribution_service.py). Uploading here ONLY validates and
     stores the file -- it never runs Opus generation, which stays a
     separate, explicit action on File Preview (2026-08-19, explicit
     instruction: the user runs analysis manually after inspecting the
     upload).
"""

import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from loguru import logger

from app.excel_validation import SUPPORTED_EXTENSIONS
from app.hierarchy_parser import refresh_hierarchy
from app.hq_distribution_service import (
    get_hq_distribution_state,
    remove_hq_distribution_file,
    upload_hq_distribution_file,
)
from app.workbook_connections import WORKBOOK_NAMES, get_connections, get_status, set_connection
from ui.background_task import run_in_background
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader, StatusBadge
from ui.hierarchy_table_section import HierarchyTableSection
from ui.icons import get_icon
from ui.theme import Color, Font, Spacing

STATUS_BADGE_KIND = {"Connected": "success", "File Not Found": "error", "Not Configured": "neutral"}


class ReviewHierarchyPage(ctk.CTkFrame):
    """Hierarchy (shared employee_hierarchy dataset) + HQ Distribution
    (upload) for the Review System module."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._busy = False
        self.path_labels: dict[str, ctk.CTkLabel] = {}
        self.status_badges: dict[str, StatusBadge] = {}

        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Hierarchy and HQ Distribution",
            "Organizational hierarchy, and the master file that determines which HQs belong to which division",
        ).pack(anchor="w", pady=(0, Spacing.LG))

        self._build_hierarchy_section(outer)
        self._build_hq_distribution_section(outer)

    def on_show(self) -> None:
        self._refresh_connection_labels()
        self.hierarchy_section.load_from_db()
        if not self._busy:
            self._refresh_hq_distribution_status()

    # --- 1. Hierarchy -- shared employee_hierarchy dataset ------------------

    def _build_hierarchy_section(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x", pady=(0, Spacing.LG))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Hierarchy Workbooks", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "Connect each division's Organization Data workbook -- the same hierarchy data "
                "Path Validator's Organization Data page manages."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        for name in WORKBOOK_NAMES:
            row = Card(body, fg_color=Color.SURFACE, border_width=0)
            row.pack(fill="x", pady=4)

            row_inner = ctk.CTkFrame(row, fg_color="transparent")
            row_inner.pack(fill="x", padx=Spacing.MD, pady=Spacing.SM)

            ctk.CTkLabel(
                row_inner, text=name, width=100, anchor="w", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY
            ).pack(side="left")

            ctk.CTkButton(
                row_inner,
                text="Browse…",
                width=90,
                fg_color="transparent",
                border_width=1,
                border_color=Color.BORDER,
                text_color=Color.TEXT_PRIMARY,
                hover_color=Color.SURFACE,
                command=lambda n=name: self._on_browse_clicked(n),
            ).pack(side="left", padx=(Spacing.MD, 0))

            badge = StatusBadge(row_inner, "Not Configured", "neutral")
            badge.pack(side="right")
            self.status_badges[name] = badge

            path_label = ctk.CTkLabel(
                row_inner, text="Not configured", anchor="w", font=Font.SMALL, text_color=Color.TEXT_MUTED
            )
            path_label.pack(side="left", fill="x", expand=True, padx=Spacing.MD)
            self.path_labels[name] = path_label

        self.refresh_button = PrimaryButton(
            body,
            text="Refresh Hierarchy",
            image=get_icon("refresh", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_refresh_clicked,
        )
        self.refresh_button.pack(pady=(Spacing.SM, 0))

        self.hierarchy_summary_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL, text_color=Color.TEXT_SECONDARY, wraplength=700, justify="left",
        )
        self.hierarchy_summary_label.pack(anchor="w", pady=(Spacing.SM, 0))

        self.hierarchy_section = HierarchyTableSection(parent, export_filename_prefix="ReviewSystemHierarchy")
        self.hierarchy_section.pack(fill="both", expand=True, pady=(0, Spacing.LG))

    def _refresh_connection_labels(self) -> None:
        connections = get_connections()
        for name in WORKBOOK_NAMES:
            file_path = connections.get(name)
            status = get_status(file_path)
            self.path_labels[name].configure(text=file_path or "Not configured")
            self.status_badges[name].set_status(status, STATUS_BADGE_KIND[status])

    def _on_browse_clicked(self, workbook_name: str) -> None:
        file_path = filedialog.askopenfilename(
            title=f"Select {workbook_name} Hierarchy Workbook",
            filetypes=[("Excel files", "*.xlsx")],
        )
        if not file_path:
            return

        set_connection(workbook_name, file_path)
        logger.info(f"Review System hierarchy workbook connected: '{workbook_name}' -> {file_path}")
        self._refresh_connection_labels()

    def _on_refresh_clicked(self) -> None:
        self.refresh_button.configure(state="disabled")
        self.hierarchy_summary_label.configure(text="Refreshing hierarchy…")
        self.update_idletasks()

        def worker() -> None:
            try:
                stats = refresh_hierarchy()
                error = None
            except Exception as exc:
                stats = None
                error = exc
            self.after(0, lambda: self._on_refresh_done(stats, error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_refresh_done(self, stats: dict | None, error: Exception | None) -> None:
        self.refresh_button.configure(state="normal")
        if not self.winfo_exists():
            return

        if error is not None:
            logger.error(f"Review System hierarchy refresh failed: {error}")
            messagebox.showerror("Refresh Failed", f"Could not refresh hierarchy data.\n\n{error}")
            self.hierarchy_summary_label.configure(text="Refresh failed.")
            return

        self.hierarchy_section.load_from_db()
        self.hierarchy_section.update_kpis(stats)

        if not stats["workbooks_read"]:
            self.hierarchy_summary_label.configure(
                text="No hierarchy workbooks are connected yet -- connect at least one above and refresh again."
            )
            return

        summary = f"Processed {stats['worksheets_processed']} worksheet(s) from {', '.join(stats['workbooks_read'])}."
        if stats["workbooks_skipped"]:
            summary += " Skipped: " + ", ".join(
                f"{name} ({reason})" for name, reason in stats["workbooks_skipped"].items()
            )
        self.hierarchy_summary_label.configure(text=summary)

    # --- 2. HQ Distribution --------------------------------------------------

    def _build_hq_distribution_section(self, parent) -> None:
        ctk.CTkLabel(
            parent, text="HQ Distribution", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))
        ctk.CTkLabel(
            parent,
            text="The master file mapping which HQs operate under each division (columns: Division, "
                 "Region Name, HQ). Determines which HQs are analyzed per division in Opus generation -- "
                 "an HQ absent here for a division is treated as not applicable, not as missing data.",
            font=Font.SMALL, text_color=Color.TEXT_SECONDARY, anchor="w", wraplength=700, justify="left",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        self._hq_dist_card = Card(parent)
        self._hq_dist_card.pack(fill="x")
        self._render_hq_distribution_card()

    def _render_hq_distribution_card(self) -> None:
        for widget in self._hq_dist_card.winfo_children():
            widget.destroy()

        body = ctk.CTkFrame(self._hq_dist_card, fg_color="transparent")
        body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        top_line = ctk.CTkFrame(body, fg_color="transparent")
        top_line.pack(fill="x")

        self._upload_button = PrimaryButton(
            top_line, text="Upload HQ Distribution",
            image=get_icon("upload", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_upload_clicked,
        )
        self._upload_button.pack(side="left")

        self._status_line = ctk.CTkFrame(top_line, fg_color="transparent")
        self._status_line.pack(side="left", fill="x", expand=True, padx=(Spacing.MD, 0))

        self._remove_button = SecondaryButton(
            top_line, text="Remove", width=80, height=28, font=Font.SMALL_BOLD, state="disabled",
            text_color=Color.ERROR, border_color=Color.ERROR, command=self._on_remove_clicked,
        )
        self._remove_button.pack(side="right")

        self._error_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL, text_color=Color.ERROR, anchor="w", justify="left", wraplength=650,
        )
        self._division_counts_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w", justify="left",
        )

        self._apply_state(get_hq_distribution_state())

    def _refresh_hq_distribution_status(self) -> None:
        self._apply_state(get_hq_distribution_state())

    def _apply_state(self, state: dict) -> None:
        for widget in self._status_line.winfo_children():
            widget.destroy()
        self._error_label.pack_forget()
        self._division_counts_label.pack_forget()

        if not state["uploaded"]:
            ctk.CTkLabel(
                self._status_line, text="No file selected", font=Font.BODY, text_color=Color.TEXT_MUTED, anchor="w"
            ).pack(side="left")
            self._remove_button.configure(state="disabled")
            return

        self._remove_button.configure(state="normal")

        if state["valid"]:
            StatusBadge(self._status_line, "VALID", "success").pack(side="left", padx=(0, Spacing.SM))
            ctk.CTkLabel(
                self._status_line, text=f"{state['filename']} · {state['row_count']:,} row(s)",
                font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w",
            ).pack(side="left")
            counts_text = "  •  ".join(f"{division}: {count}" for division, count in sorted(state["division_counts"].items()))
            self._division_counts_label.configure(text=counts_text)
            self._division_counts_label.pack(anchor="w", pady=(Spacing.SM, 0))
        else:
            StatusBadge(self._status_line, "INVALID", "error").pack(side="left", padx=(0, Spacing.SM))
            ctk.CTkLabel(
                self._status_line, text=state["filename"] or "", font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w"
            ).pack(side="left")
            self._error_label.configure(text="  •  ".join(state["errors"]) or "This file did not pass validation.")
            self._error_label.pack(fill="x", pady=(Spacing.SM, 0))

    def _set_validating(self) -> None:
        for widget in self._status_line.winfo_children():
            widget.destroy()
        ctk.CTkLabel(
            self._status_line, text="Validating...", font=Font.BODY, text_color=Color.INFO, anchor="w"
        ).pack(side="left")
        self._error_label.pack_forget()
        self._upload_button.configure(state="disabled")

    # --- Actions -------------------------------------------------------------

    def _on_upload_clicked(self) -> None:
        state = get_hq_distribution_state()
        if state["uploaded"]:
            if not messagebox.askyesno(
                "Replace File", f"Existing file:\n{state['filename']}\n\nReplace it with a new upload?"
            ):
                return

        file_path = filedialog.askopenfilename(
            title="Select HQ Distribution",
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
            return

        if Path(file_path).suffix.lower() not in SUPPORTED_EXTENSIONS:
            messagebox.showerror(
                "Unsupported File Type",
                "Unsupported file type. Please upload an Excel (.xlsx, .xls, .xlsm) or CSV (.csv) file.",
            )
            return

        logger.info(f"HQ Distribution: uploading '{file_path}'")
        self._busy = True
        self._set_validating()

        def work(_report_progress):
            return upload_hq_distribution_file(file_path)

        def on_done(result, error):
            self._busy = False
            self._upload_button.configure(state="normal")

            if error is not None:
                logger.error(f"HQ Distribution: failed to store upload: {error}")
                messagebox.showerror("Upload Failed", f"Could not process the selected file.\n\n{error}")
                self._apply_state(get_hq_distribution_state())
                return

            self._apply_state(result)
            if not result["valid"]:
                messagebox.showwarning(
                    "File Invalid",
                    f"'{Path(file_path).name}' did not pass validation:\n\n" + "\n".join(f"• {e}" for e in result["errors"]),
                )
            # Deliberately no analysis/generation triggered here -- the
            # user runs Opus generation manually from File Preview, per
            # explicit instruction (2026-08-19).

        run_in_background(self, work, on_done=on_done)

    def _on_remove_clicked(self) -> None:
        state = get_hq_distribution_state()
        filename = state.get("filename") or "this file"
        if not messagebox.askyesno("Remove File", f"Remove '{filename}'?"):
            return
        remove_hq_distribution_file()
        self._apply_state(get_hq_distribution_state())
