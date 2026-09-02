"""Review System -- File Preview page.

Previews the FINAL REVIEW OUTPUT for Opus Summary, Coverage Summary, and
RGD Visit and Support generation -- the processed/generated result of
applying business logic to the verified uploads, never a raw uploaded
Excel file (see app/review_opus_service.py, app/review_coverage_service.py,
app/review_rgd_service.py, and app/review_output_service.py). Two
selectors stack at the top: report type above division (Xandra / Onyx /
Guardians) -- both reuse ui.components.TabBar, the same switching pattern
the Findings page uses for its two tabs, per the brief that this page
should feel like it belongs to the same application rather than inventing
a second selector look. RGD Visit and Support's title is deliberately
rendered exactly "RGD VISIT AND SUPPORT" (all caps, unlike its Title Case
siblings) per explicit spec (2026-08-19): "Do not rename it, shorten it,
or merge it with another summary."

Only the selected report type + division's generated report is ever shown;
nothing else is computed or displayed until its own tabs are selected.

The embedded table below the action row is the COMPLETE generated grid for
whichever report type is active -- read from the JSON preview sidecar each
generator writes alongside its .xlsx at generation time (there is exactly
one sheet per report today, so no sheet switcher is built).
"""

import shutil
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app.config import REPORTS_DIR
from app.review_coverage_email_settings_service import (
    is_automatic_sending_enabled as coverage_automatic_sending_enabled,
    set_automatic_sending_enabled as set_coverage_automatic_sending_enabled,
)
from app.review_coverage_notification_service import build_notification_batch, send_notification_batch
from app.review_coverage_service import coverage_prerequisites_ready, generate_coverage_summary
from app.review_export_service import export_all_divisions
from app.review_opus_mapping import OPUS_HQ_BLOCKS_BY_DIVISION
from app.review_opus_service import DIVISIONS, generate_opus_summary, opus_prerequisites_ready
from app.review_output_service import (
    get_coverage_summary_preview,
    get_generated_coverage_summary,
    get_generated_opus_summary,
    get_generated_rgd_summary,
    get_opus_summary_preview,
    get_rgd_summary_preview,
)
from app.review_rgd_service import (
    IDENTITY_KEYS as RGD_IDENTITY_KEYS,
    SUPPORT_KEYS as RGD_SUPPORT_KEYS,
    VISIT_KEYS as RGD_VISIT_KEYS,
    generate_rgd_summary,
    rgd_prerequisites_ready,
)
from app.table_export_service import default_export_filename, prompt_save_path
from ui.background_task import run_in_background
from ui.components import Card, EmptyState, PrimaryButton, SectionHeader, TabBar, styled_treeview
from ui.icons import get_icon
from ui.loading_overlay import LoadingOverlay
from ui.theme import Color, Font, Spacing

_UNRESOLVED_COLOR = "#9C0006"

REPORT_TYPES = ("Opus Summary", "Coverage Summary", "RGD VISIT AND SUPPORT")

# Per-report-type wiring -- everything that differs between the two report
# shapes lives here, so _render_generated/_apply_filter stay one path
# rather than two near-duplicate implementations. `identity_keys` are the
# preview row-dict keys shown BEFORE the month columns, `trailing_keys`
# AFTER them (Opus has a CUMULATIVE column; Coverage doesn't).
_REPORT_CONFIG = {
    "Opus Summary": {
        "generate_fn": generate_opus_summary,
        "prerequisites_fn": opus_prerequisites_ready,
        "get_generated_fn": get_generated_opus_summary,
        "get_preview_fn": get_opus_summary_preview,
        "no_mapping_fn": lambda division: OPUS_HQ_BLOCKS_BY_DIVISION.get(division) is None,
        "filename_base": "Opus_Summary",
        "identity_keys": ("region", "hq", "particulars", "no_of_bm", "no"),
        "identity_widths": {"region": 140, "hq": 150, "particulars": 240, "no_of_bm": 70, "no": 40},
        "trailing_keys": ("cumulative",),
        "trailing_widths": {"cumulative": 90},
        "filter_keys": ("region", "hq", "particulars"),
        "row_tag_fn": lambda row: row.get("kind") if row.get("kind") != "data" else None,
    },
    "Coverage Summary": {
        "generate_fn": generate_coverage_summary,
        "prerequisites_fn": coverage_prerequisites_ready,
        "get_generated_fn": get_generated_coverage_summary,
        "get_preview_fn": get_coverage_summary_preview,
        "no_mapping_fn": lambda division: False,
        "filename_base": "Coverage_Summary",
        "identity_keys": ("division", "region", "hq", "emp_code", "name", "designation", "no", "particulars"),
        "identity_widths": {"division": 90, "region": 130, "hq": 130, "emp_code": 80, "name": 160, "designation": 90, "no": 40, "particulars": 150},
        "trailing_keys": (),
        "trailing_widths": {},
        "filter_keys": ("region", "hq", "particulars", "name", "emp_code"),
        "row_tag_fn": lambda row: None,
    },
    "RGD VISIT AND SUPPORT": {
        "generate_fn": generate_rgd_summary,
        "prerequisites_fn": rgd_prerequisites_ready,
        "get_generated_fn": get_generated_rgd_summary,
        "get_preview_fn": get_rgd_summary_preview,
        "no_mapping_fn": lambda division: False,
        "filename_base": "RGD_Visit_and_Support",
        # Flat one-row-per-doctor-record shape (no month/KPI rows like
        # Opus/Coverage) -- every column is an "identity" column and there
        # are no month/trailing columns, which the shared renderer already
        # supports (month_count just comes out to 0).
        "identity_keys": RGD_IDENTITY_KEYS + RGD_SUPPORT_KEYS + RGD_VISIT_KEYS,
        "identity_widths": {
            "region": 100, "hq": 90, "bm_code": 80, "bm_name": 150, "dr_code": 90, "dr_name": 140,
            "town": 100, "category": 110, "speciality": 90,
            "support_feb": 70, "support_mar": 70, "support_apr": 70, "support_may": 70, "support_jun": 70, "support_jul": 70,
            "visit_apr": 55, "visit_may": 55, "visit_jun": 55, "visit_jul": 55, "visit_aug": 55,
        },
        "trailing_keys": (),
        "trailing_widths": {},
        "filter_keys": ("region", "hq", "bm_code", "bm_name", "dr_code", "dr_name"),
        "row_tag_fn": lambda row: None,
    },
}


class ReviewFilePreviewPage(ctk.CTkFrame):
    """Report-type- and division-selectable, full-grid preview of the
    generated Opus/Coverage Summary."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._generating = False
        self._preview_rows_cache: list = []

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(outer, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.MD))

        SectionHeader(
            header_row, "File Preview", "The generated report -- select a report type and division to view or generate it"
        ).pack(side="left", anchor="w")

        PrimaryButton(
            header_row, text="Export Entire File",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._export_entire_file,
        ).pack(side="right", anchor="n")

        self.report_tabs = TabBar(outer, list(REPORT_TYPES), on_change=self._on_selector_changed)
        self.report_tabs.pack(fill="x", pady=(0, Spacing.SM))

        self.division_tabs = TabBar(outer, list(DIVISIONS), on_change=self._on_selector_changed)
        self.division_tabs.pack(fill="x", pady=(0, Spacing.LG))

        self._body_container = ctk.CTkFrame(outer, fg_color="transparent")
        self._body_container.pack(fill="both", expand=True)

        self._render()

    def on_show(self) -> None:
        self._render()

    def _on_selector_changed(self, _value: str) -> None:
        self._render()

    @property
    def _config(self) -> dict:
        return _REPORT_CONFIG[self.report_tabs.active_tab]

    # --- Rendering -----------------------------------------------------------

    def _clear_body(self) -> None:
        for widget in self._body_container.winfo_children():
            widget.destroy()

    def _render_empty(self, message: str) -> None:
        self._clear_body()
        card = Card(self._body_container)
        card.pack(fill="both", expand=True)
        EmptyState(card, message).pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

    def _render(self) -> None:
        if self._generating:
            return  # a background generation is in flight; its own on_done re-renders
        report_type = self.report_tabs.active_tab
        division = self.division_tabs.active_tab
        config = self._config
        self._clear_body()

        if config["no_mapping_fn"](division):
            self._render_empty(
                f"No Region/HQ mapping has been built for {division} yet.\n\n"
                "A manual reference workbook for this division is needed "
                "before it can be generated -- see Xandra for the reference implementation."
            )
            return

        ready, missing = config["prerequisites_fn"](division)
        if not ready:
            self._render_empty(
                f"{division}'s {report_type} needs these source file(s) uploaded and valid first:\n\n"
                + "\n".join(f"  • {slot_id}" for slot_id in missing)
                + "\n\nGo to Uploads to check readiness."
            )
            return

        existing = config["get_generated_fn"](division)
        if existing is None:
            self._render_not_generated(report_type, division)
        else:
            self._render_generated(report_type, division, existing)

    def _render_not_generated(self, report_type: str, division: str) -> None:
        card = Card(self._body_container)
        card.pack(fill="both", expand=True)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text=f"{division}'s {report_type} hasn't been generated yet.",
            font=Font.BODY, text_color=Color.TEXT_MUTED,
        ).pack(pady=(0, Spacing.MD))

        self._status_label = ctk.CTkLabel(body, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED)
        self._status_label.pack(pady=(0, Spacing.SM))

        self._generate_button = PrimaryButton(
            body, text=f"Generate {division} {report_type}",
            command=lambda: self._start_generation(report_type, division),
        )
        self._generate_button.pack()

    def _render_generated(self, report_type: str, division: str, meta: dict) -> None:
        config = self._config

        # Primary action row: search/filter + Export, right at the top --
        # visible without scrolling, matching the Findings page's own
        # filter-row-with-Export placement (see ui/findings_page.py).
        action_row = ctk.CTkFrame(self._body_container, fg_color="transparent")
        action_row.pack(fill="x", pady=(0, Spacing.SM))

        self.search_entry = ctk.CTkEntry(action_row, placeholder_text="Filter by Region, HQ, Name…")
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, Spacing.SM))
        self.search_entry.bind("<KeyRelease>", lambda event: self._apply_filter())

        PrimaryButton(
            action_row, text="Export", image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=lambda: self._export_file(report_type, division, meta["file_path"]),
        ).pack(side="right")

        self._status_label = ctk.CTkLabel(self._body_container, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED)
        self._status_label.pack(anchor="w", pady=(0, Spacing.SM))

        if report_type == "Coverage Summary":
            self._render_coverage_email_controls(division)

        preview_card = Card(self._body_container)
        preview_card.pack(fill="both", expand=True)
        preview_body = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_body.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)

        preview = config["get_preview_fn"](division)
        if preview is None:
            # The .xlsx exists (checked by the caller) but its JSON sidecar
            # doesn't -- only possible for a file generated before this
            # sidecar existed. Regenerating (the Generate button reappears
            # once nothing renders here) writes both together.
            EmptyState(
                preview_body, "This report was generated before the embedded preview existed -- regenerate to see it here."
            ).pack(fill="both", expand=True)
            return
        self._preview_rows_cache = preview["rows"]

        identity_keys = config["identity_keys"]
        trailing_keys = config["trailing_keys"]
        month_count = len(preview["columns"]) - len(identity_keys) - len(trailing_keys)
        month_keys = tuple(f"m{i}" for i in range(month_count))
        columns = identity_keys + month_keys + trailing_keys
        headings = dict(zip(columns, preview["columns"]))
        widths = {**config["identity_widths"], **config["trailing_widths"]}

        self._preview_tree = styled_treeview(preview_body, columns, headings, widths=widths, height=24)
        self._preview_tree.tag_configure("spacer", background=Color.SURFACE, font=Font.SMALL_BOLD)
        self._preview_tree.tag_configure("unresolved", foreground=_UNRESOLVED_COLOR, font=Font.SMALL_BOLD)
        self._preview_tree.pack(fill="both", expand=True)

        self._apply_filter()

    def _apply_filter(self) -> None:
        if not hasattr(self, "_preview_tree") or not self._preview_tree.winfo_exists():
            return
        config = self._config
        query = self.search_entry.get().strip().lower() if hasattr(self, "search_entry") else ""
        filter_keys = config["filter_keys"]
        identity_keys = config["identity_keys"]
        trailing_keys = config["trailing_keys"]

        self._preview_tree.delete(*self._preview_tree.get_children())
        for row in self._preview_rows_cache:
            if query and not any(query in str(row.get(k, "")).lower() for k in filter_keys):
                continue
            values = tuple(row[k] for k in identity_keys) + tuple(row["months"]) + tuple(row[k] for k in trailing_keys)
            tag = config["row_tag_fn"](row)
            self._preview_tree.insert("", "end", values=values, tags=(tag,) if tag else ())

    # --- Coverage Summary automated email workflow --------------------------

    def _render_coverage_email_controls(self, division: str) -> None:
        """Automatic/manual toggle + a manual "Send Emails Now" trigger for
        the Coverage Summary automated-email workflow (see
        app.review_coverage_notification_service) -- one BM Coverage
        Summary file per attachment, grouped into one email per ABM. Only
        shown for the Coverage Summary report type; Opus Summary and RGD
        Visit and Support have no email workflow of their own."""
        row = ctk.CTkFrame(self._body_container, fg_color="transparent")
        row.pack(fill="x", pady=(0, Spacing.SM))

        self._coverage_email_status_label = ctk.CTkLabel(row, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED)
        self._coverage_email_status_label.pack(side="left")

        self._coverage_send_button = ctk.CTkButton(
            row, text="Send Emails Now", font=Font.SMALL_BOLD,
            fg_color=Color.SURFACE, text_color=Color.PRIMARY, hover_color=Color.PRIMARY_SOFT,
            border_width=1, border_color=Color.PRIMARY,
            command=lambda: self._send_coverage_emails(division),
        )
        self._coverage_send_button.pack(side="right", padx=(Spacing.SM, 0))

        self._coverage_automatic_switch = ctk.CTkSwitch(
            row, text="Send automatically after each generation", font=Font.SMALL,
            text_color=Color.TEXT_PRIMARY, progress_color=Color.PRIMARY,
            command=lambda: set_coverage_automatic_sending_enabled(bool(self._coverage_automatic_switch.get())),
        )
        self._coverage_automatic_switch.pack(side="right")
        if coverage_automatic_sending_enabled():
            self._coverage_automatic_switch.select()
        else:
            self._coverage_automatic_switch.deselect()

    def _send_coverage_emails(self, division: str) -> None:
        if self._coverage_send_button.winfo_exists():
            self._coverage_send_button.configure(state="disabled", text="Sending...")
        if self._coverage_email_status_label.winfo_exists():
            self._coverage_email_status_label.configure(text="Building Coverage Summary files and emails...")

        def work_fn(_report_progress):
            drafts = build_notification_batch(division)
            if not drafts:
                return {"sent_count": 0, "failed_count": 0, "drafts": []}
            return send_notification_batch(drafts)

        def on_done(result, error):
            if self._coverage_send_button.winfo_exists():
                self._coverage_send_button.configure(state="normal", text="Send Emails Now")
            if not self._coverage_email_status_label.winfo_exists():
                return
            if error is not None:
                self._coverage_email_status_label.configure(text=f"Sending failed: {error!r}", text_color=Color.ERROR)
                return
            self._coverage_email_status_label.configure(
                text=f"Sent {result['sent_count']} email(s), {result['failed_count']} failed.",
                text_color=Color.TEXT_MUTED,
            )

        run_in_background(self, work_fn, on_done=on_done)

    # --- Export ------------------------------------------------------------

    def _export_file(self, report_type: str, division: str, generated_file_path: str) -> None:
        """Same UX contract as every other Export button in the app (see
        app/table_export_service.py's export_rows_with_ui): a native Save
        dialog seeded at REPORTS_DIR, a LoadingOverlay while the write
        happens on a background thread, then a success/error messagebox.

        Unlike export_rows_with_ui, this does NOT go through
        write_rows_to_excel -- that writer only knows how to dump a flat
        list of dicts into a new single-sheet workbook, which would throw
        away the report's actual formulas, number formats, borders, and
        frozen header. "Export" here means "copy the real generated .xlsx
        to wherever the user chooses", so the exported file is
        byte-for-byte the same workbook Excel would open -- the whole file,
        not a re-derived dump of whatever the on-screen preview shows."""
        suggested = default_export_filename(self._config["filename_base"], suffix=division)
        destination = prompt_save_path(suggested, parent=self)
        if not destination:
            return  # user cancelled -- no thread, no copy, no message

        overlay = LoadingOverlay(self)
        overlay.show()

        def work(report_progress):
            report_progress(50, "Copying workbook...")
            shutil.copyfile(generated_file_path, destination)
            report_progress(100, "Done")
            return destination

        def on_progress(percent, message):
            overlay.update_progress(percent, message)

        def on_done(result, error):
            overlay.hide()
            if error is not None:
                messagebox.showerror("Export Failed", f"Could not export the report.\n\n{error}")
                return
            messagebox.showinfo("Export Complete", f"Exported to:\n{result}")

        run_in_background(self, work, on_progress=on_progress, on_done=on_done)

    def _export_entire_file(self) -> None:
        """Ignores whatever report/division/filter is currently selected --
        always (re)generates and exports the complete, division-isolated
        dataset for all three divisions (see app.review_export_service).
        Three independent .xlsx files, never a zip -- so a single
        destination-folder picker replaces per-report Save dialogs; each
        file is then copied in under its fixed DIVISION.xlsx name."""
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        folder = filedialog.askdirectory(parent=self, title="Choose a folder for XANDRA / ONYX / GUARDIANS", initialdir=str(REPORTS_DIR))
        if not folder:
            return  # user cancelled -- no thread, no export

        overlay = LoadingOverlay(self)
        overlay.show()

        def work(report_progress):
            return export_all_divisions(report_progress=report_progress)

        def on_progress(percent, message):
            overlay.update_progress(percent, message)

        def on_done(result, error):
            overlay.hide()
            if error is not None:
                messagebox.showerror("Export Failed", f"Could not export the report.\n\n{error}")
                return

            written = []
            for division, src_path in result["files"].items():
                if src_path is None:
                    continue
                dest = f"{folder}/{division.strip().upper()}.xlsx"
                shutil.copyfile(src_path, dest)
                written.append(dest)

            problems = [f"{division}: {'; '.join(errs)}" for division, errs in result["errors"].items() if errs]
            if not written:
                messagebox.showerror("Export Failed", "No workbook could be produced for any division.\n\n" + "\n".join(problems))
                return

            summary = f"Exported {len(written)} file(s) to:\n{folder}"
            if problems:
                summary += "\n\nSome sheets were skipped:\n" + "\n".join(problems)
            messagebox.showinfo("Export Complete", summary)

        run_in_background(self, work, on_progress=on_progress, on_done=on_done)

    # --- Generation ------------------------------------------------------

    def _start_generation(self, report_type: str, division: str) -> None:
        if self._generating:
            return  # already running -- ignore a double-click on Generate
        self._generating = True
        self._generate_button_disable_all()
        generate_fn = self._config["generate_fn"]

        def work_fn(report_progress):
            return generate_fn(division, report_progress=report_progress)

        def on_progress(percent, message):
            if self._status_label.winfo_exists():
                self._status_label.configure(text=f"{percent}% -- {message}")

        def on_done(result, error):
            self._generating = False
            if error is not None:
                self._render_empty(f"Generation failed: {error!r}")
                return
            if not result["success"]:
                self._render_empty("Generation failed:\n\n" + "\n".join(result["errors"]))
                return
            self._render()
            if report_type == "Coverage Summary" and coverage_automatic_sending_enabled():
                self._send_coverage_emails(division)

        run_in_background(self, work_fn, on_progress=on_progress, on_done=on_done)

    def _generate_button_disable_all(self) -> None:
        if hasattr(self, "_generate_button") and self._generate_button.winfo_exists():
            self._generate_button.configure(state="disabled", text="Generating...")
