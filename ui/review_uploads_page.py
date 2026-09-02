"""Review System -- Uploads page.

Upload + validate the 12 required Review System files (see
app/review_schemas.py for the fixed slot list/schemas). This page is the
UPLOAD + VALIDATION FOUNDATION only -- it never runs analysis; it just
gets every slot to a known, validated state and reflects the single
centralized readiness gate (app.review_upload_service.is_review_analysis_ready).

Visual style deliberately mirrors ui/work_distribution_upload_page.py:
CollapsibleSection per group, one descriptive PrimaryButton per upload row
("Upload {name}") with plain "No file selected" text when empty, nested
CollapsibleSection for a group with multiple sources (Onyx/Guardians/
Xandra) exactly like Work Distribution's own ABM/RBM role sections. Only
the visual layout changed from the previous version -- the 12-slot
structure, schemas, validation rules, and the readiness gate are untouched.
"""

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from loguru import logger

from app.excel_validation import SUPPORTED_EXTENSIONS
from app.review_schemas import COVERAGE_SUMMARY, OPUS_SUMMARY, REVIEW_FILE_SLOTS
from app.review_upload_service import (
    get_all_slot_states,
    is_review_analysis_ready,
    readiness_counts,
    remove_review_file,
    upload_review_file,
)
from ui.background_task import run_in_background
from ui.components import Card, CollapsibleSection, PrimaryButton, SecondaryButton, SectionHeader, StatusBadge
from ui.icons import get_icon
from ui.theme import Color, Font, Radius, Spacing

_CATEGORY_ORDER = (OPUS_SUMMARY, COVERAGE_SUMMARY)
_CATEGORY_INTRO = {
    OPUS_SUMMARY: "Upload the required Opus Summary files.",
    COVERAGE_SUMMARY: "Upload the required Coverage Summary files.",
}


def _grouped_slots():
    """{category: {subcategory: [slot_def, ...]}}, in the exact order
    app.review_schemas.REVIEW_FILE_SLOTS defines -- never a second,
    hand-maintained UI-side ordering."""
    grouped: dict = {}
    for slot in REVIEW_FILE_SLOTS:
        grouped.setdefault(slot.category, {}).setdefault(slot.subcategory, []).append(slot)
    return grouped


class ReviewUploadsPage(ctk.CTkFrame):
    """Uploads + validation for the Review System's 12 required files."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._slot_widgets: dict = {}
        self._busy_slots: set = set()

        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Uploads", "Upload and validate the 12 required Review System files"
        ).pack(anchor="w", pady=(0, Spacing.LG))

        self._readiness_card = Card(outer)
        self._readiness_card.pack(fill="x", pady=(0, Spacing.LG))

        self._tree_container = ctk.CTkFrame(outer, fg_color="transparent")
        self._tree_container.pack(fill="x")

        self._build_tree()
        self._refresh_readiness()

    # --- Lifecycle -----------------------------------------------------------

    def on_show(self) -> None:
        self._refresh_readiness()
        all_states = get_all_slot_states()
        for slot in REVIEW_FILE_SLOTS:
            if slot.slot_id not in self._busy_slots:
                self._apply_slot_state(slot.slot_id, all_states.get(slot.slot_id))

    # --- Readiness header ------------------------------------------------

    def _refresh_readiness(self) -> None:
        for widget in self._readiness_card.winfo_children():
            widget.destroy()

        uploaded, valid, total = readiness_counts()
        ready = is_review_analysis_ready()

        body = ctk.CTkFrame(self._readiness_card, fg_color="transparent")
        body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Review Readiness", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")

        counts_row = ctk.CTkFrame(body, fg_color="transparent")
        counts_row.pack(fill="x", pady=(Spacing.SM, Spacing.SM))
        ctk.CTkLabel(
            counts_row, text=f"{uploaded} / {total} files", font=Font.BODY, text_color=Color.TEXT_SECONDARY
        ).pack(side="left", padx=(0, Spacing.LG))
        ctk.CTkLabel(
            counts_row, text=f"{valid} / {total} valid", font=Font.BODY, text_color=Color.TEXT_SECONDARY
        ).pack(side="left")

        progress = ctk.CTkProgressBar(
            body, height=10, corner_radius=Radius.SM,
            progress_color=Color.SUCCESS if ready else Color.PRIMARY,
        )
        progress.set(valid / total if total else 0)
        progress.pack(fill="x", pady=(0, Spacing.MD))

        StatusBadge(
            body,
            "ANALYSIS READY" if ready else "ANALYSIS LOCKED",
            "success" if ready else "warning",
        ).pack(anchor="w")

    # --- Slot tree ---------------------------------------------------------

    def _build_tree(self) -> None:
        grouped = _grouped_slots()
        all_states = get_all_slot_states()

        for category in _CATEGORY_ORDER:
            subcategories = grouped.get(category, {})
            category_section = CollapsibleSection(self._tree_container, category, expanded=True)
            category_section.pack(fill="x", pady=(0, Spacing.LG))
            body = category_section.body

            ctk.CTkLabel(
                body, text=_CATEGORY_INTRO[category], font=Font.BODY, text_color=Color.TEXT_SECONDARY,
                anchor="w", wraplength=650, justify="left",
            ).pack(anchor="w", pady=(0, Spacing.MD))

            for subcategory, slots in subcategories.items():
                if len(slots) == 1 and slots[0].source_name is None:
                    # A single, unqualified slot (Annual Targets, Primary
                    # Sales, Last Year Primary Sales) -- rendered directly,
                    # matching Work Distribution's own RGD Coverage rows
                    # (no nested grouping for a single upload).
                    self._build_slot_row(body, slots[0], slots[0].display_name, all_states.get(slots[0].slot_id))
                else:
                    sub_section = CollapsibleSection(body, subcategory, expanded=True, nested=True)
                    sub_section.pack(fill="x", pady=(0, Spacing.MD))
                    for slot in slots:
                        # Nested rows are labeled by source only ("Upload
                        # Onyx") -- the subcategory name is already the
                        # section title, exactly matching Work
                        # Distribution's own ABM/RBM division rows
                        # ("Upload Onyx", not "Upload Onyx ABM").
                        self._build_slot_row(sub_section.body, slot, slot.source_name, all_states.get(slot.slot_id))

    def _build_slot_row(self, parent, slot, button_label, initial_state) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, Spacing.SM))

        top_line = ctk.CTkFrame(row, fg_color="transparent")
        top_line.pack(fill="x")

        upload_button = PrimaryButton(
            top_line,
            text=f"Upload {button_label}",
            image=get_icon("upload", size=16, color=Color.TEXT_ON_PRIMARY),
            command=lambda sid=slot.slot_id: self._on_upload_clicked(sid),
        )
        upload_button.pack(side="left")

        status_line = ctk.CTkFrame(top_line, fg_color="transparent")
        status_line.pack(side="left", fill="x", expand=True, padx=(Spacing.MD, 0))

        remove_button = SecondaryButton(
            top_line, text="Remove", width=80, height=28, font=Font.SMALL_BOLD, state="disabled",
            text_color=Color.ERROR, border_color=Color.ERROR,
            command=lambda sid=slot.slot_id: self._on_remove_clicked(sid),
        )
        remove_button.pack(side="right")

        error_label = ctk.CTkLabel(
            row, text="", font=Font.SMALL, text_color=Color.ERROR, anchor="w",
            justify="left", wraplength=650,
        )
        # Packed on demand in _apply_slot_state -- an empty error label
        # would still take up a blank line if always packed.

        self._slot_widgets[slot.slot_id] = {
            "slot": slot,
            "row": row,
            "status_line": status_line,
            "error_label": error_label,
            "upload_button": upload_button,
            "remove_button": remove_button,
        }
        self._apply_slot_state(slot.slot_id, initial_state)

    # --- Per-slot state rendering -------------------------------------------

    def _refresh_slot_row(self, slot_id: str) -> None:
        if slot_id in self._busy_slots:
            return  # a validation is in flight for this slot; let it finish
        state = get_all_slot_states().get(slot_id)
        self._apply_slot_state(slot_id, state)

    def _clear_status_line(self, status_line) -> None:
        for widget in status_line.winfo_children():
            widget.destroy()

    def _apply_slot_state(self, slot_id: str, state: dict | None) -> None:
        widgets = self._slot_widgets.get(slot_id)
        if widgets is None or state is None:
            return

        status_line = widgets["status_line"]
        error_label = widgets["error_label"]
        self._clear_status_line(status_line)

        if not state["uploaded"]:
            ctk.CTkLabel(
                status_line, text="No file selected", font=Font.BODY, text_color=Color.TEXT_MUTED, anchor="w"
            ).pack(side="left")
            error_label.pack_forget()
            error_label.configure(text="")
            widgets["remove_button"].configure(state="disabled")
            return

        widgets["remove_button"].configure(state="normal")

        if state["valid"]:
            StatusBadge(status_line, "VALID", "success").pack(side="left", padx=(0, Spacing.SM))
            rows = state["row_count"]
            cols = state["column_count"]
            ctk.CTkLabel(
                status_line,
                text=f"{state['filename']} · {cols} column{'s' if cols != 1 else ''} · {rows:,} row{'s' if rows != 1 else ''}",
                font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w",
            ).pack(side="left")
            error_label.pack_forget()
            error_label.configure(text="")
        else:
            StatusBadge(status_line, "INVALID", "error").pack(side="left", padx=(0, Spacing.SM))
            ctk.CTkLabel(
                status_line, text=state["filename"] or "", font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w"
            ).pack(side="left")
            error_label.configure(text="  •  ".join(state["errors"]) or "This file did not pass validation.")
            error_label.pack(fill="x", pady=(2, 0))

    def _set_validating(self, slot_id: str) -> None:
        widgets = self._slot_widgets.get(slot_id)
        if widgets is None:
            return
        self._clear_status_line(widgets["status_line"])
        ctk.CTkLabel(
            widgets["status_line"], text="Validating...", font=Font.BODY, text_color=Color.INFO, anchor="w"
        ).pack(side="left")
        widgets["error_label"].pack_forget()
        widgets["upload_button"].configure(state="disabled")

    # --- Actions -------------------------------------------------------------

    def _on_upload_clicked(self, slot_id: str) -> None:
        state = get_all_slot_states().get(slot_id)
        slot = self._slot_widgets[slot_id]["slot"]

        if state and state["uploaded"]:
            if not messagebox.askyesno(
                "Replace File",
                f"Existing file:\n{state['filename']}\n\nReplace it with a new upload?",
            ):
                return

        file_path = filedialog.askopenfilename(
            title=f"Select {slot.display_name}{' — ' + slot.source_name if slot.source_name else ''}",
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

        logger.info(f"Review System: uploading '{file_path}' to slot '{slot_id}'")
        self._busy_slots.add(slot_id)
        self._set_validating(slot_id)

        def work(_report_progress):
            return upload_review_file(slot_id, file_path)

        def on_done(result, error):
            self._busy_slots.discard(slot_id)
            self._slot_widgets[slot_id]["upload_button"].configure(state="normal")

            if error is not None:
                logger.error(f"Review System: failed to store upload for '{slot_id}': {error}")
                messagebox.showerror("Upload Failed", f"Could not process the selected file.\n\n{error}")
                self._refresh_slot_row(slot_id)
                return

            self._apply_slot_state(slot_id, get_all_slot_states()[slot_id])
            self._refresh_readiness()
            if not result["valid"]:
                messagebox.showwarning(
                    "File Invalid",
                    f"'{Path(file_path).name}' did not pass validation:\n\n" + "\n".join(f"• {e}" for e in result["errors"]),
                )

        run_in_background(self, work, on_done=on_done)

    def _on_remove_clicked(self, slot_id: str) -> None:
        state = get_all_slot_states().get(slot_id)
        filename = (state or {}).get("filename") or "this file"
        if not messagebox.askyesno("Remove File", f"Remove '{filename}' from this slot?"):
            return
        remove_review_file(slot_id)
        self._refresh_slot_row(slot_id)
        self._refresh_readiness()
