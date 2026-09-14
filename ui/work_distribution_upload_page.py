"""Work Distribution Upload page.

Version 2.1 architecture update: this module now holds TWO independent
monthly business processes that share the same hierarchy, Email Center,
Findings, Exports, and Employee Details -- RGD Coverage (fully implemented)
and Manager Work Allocation (UI scaffold only, this phase). The page is
split into two collapsible sections (see ui.components.CollapsibleSection)
so both live here without either crowding the other out.

RGD Coverage section: UNCHANGED. Mirrors Path Validator's own
multi-file-then-Run-Analysis workflow (see ui/operations_page.py's own
DIVISION_SLOTS pattern) rather than a single combined file -- the Work
Distribution report is NOT one master file; it is already split by
division (confirmed against the real files this module was built against).
Workflow: Browse Onyx -> Browse Guardians -> Browse Xandra -> Run Analysis.
Each Browse only parses that division's file (app.work_distribution_parser)
and holds the result in memory -- analysis does NOT start automatically.
Only Run Analysis (enabled once all three are loaded) concatenates the
three parsed doctor lists into one dataset and runs the full KPI pipeline
(app.work_distribution_service.process_work_distribution_report).

Manager Work Allocation section, Phase 3 (ABM + RBM both live): both
engines' three division uploads parse for real
(app.manager_work_allocation_parser.parse_manager_work_allocation_report --
the SAME parser for both, the report format is identical) and the shared
Run Analysis button (enabled once all SIX files -- 3 ABM + 3 RBM -- are
loaded) combines each engine's own three files and runs BOTH calculation
pipelines (app.manager_work_allocation_service.process_manager_work_allocation_report
for ABM, app.manager_work_allocation_rbm_service.process_rbm_report for
RBM) in one click -- mirrors RGD Coverage's own Browse/Run Analysis
workflow, just fanned out over two engines instead of one.

Emails are no longer sent automatically after either Run Analysis button --
see ui/work_distribution_findings_page.py's "Send Emails" button, the only
place app.work_distribution_notification_service.send_notification_batch is
called from.
"""

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from loguru import logger

from app.manager_work_allocation_parser import (
    SUPPORTED_EXTENSIONS as MWA_SUPPORTED_EXTENSIONS,
    parse_manager_work_allocation_report,
)
from app.manager_work_allocation_rbm_service import process_rbm_report
from app.manager_work_allocation_service import process_manager_work_allocation_report
from app.work_distribution_parser import SUPPORTED_EXTENSIONS, parse_work_distribution_report
from app.work_distribution_service import process_work_distribution_report
from app.work_distribution_upload_log_service import record_upload
from app.work_distribution_upload_service import ABM, RBM, RGD, get_slot_state, slot_id_for
from app.work_distribution_sync_service import (
    ABM_SLOTS,
    RBM_SLOTS,
    RGD_SLOTS,
    SLOT_LABELS,
    apply_pending_updates,
    check_for_updates,
    upload_and_sync,
)
from ui.background_task import run_in_background
from ui.components import CollapsibleSection, PrimaryButton, SecondaryButton, SectionHeader
from ui.icons import get_icon
from ui.loading_overlay import LoadingOverlay
from ui.theme import Color, Font, Radius, Spacing

# This page's own 9 slots out of Work Distribution sync's 12 (the other 3
# are the hierarchy workbooks -- ui/work_distribution_email_center_page.py's
# own banner). One shared check_for_updates()/apply_pending_updates() pair
# covers all 12; each page just filters down to its own slot_ids -- see
# app/work_distribution_sync_service.py's module docstring.
_PAGE_SLOTS = RGD_SLOTS + ABM_SLOTS + RBM_SLOTS

# The monthly coverage report comes as one file per division -- each with
# identical columns (see app.work_distribution_parser.FIXED_REQUIRED_COLUMNS)
# and its own correct Division value already in every row. All three must
# be loaded before Run Analysis enables; they're combined into one doctor
# list right before the existing, unchanged KPI pipeline runs -- see
# _on_run_analysis_clicked. Mirrors ui/operations_page.py's own
# DIVISION_SLOTS exactly.
DIVISION_SLOTS = ("Onyx", "Guardians", "Xandra")

# Manager Work Allocation has its own ABM/RBM roster, each with the same
# three division uploads. Both are now live engines (Phase 3) -- see
# module docstring.
MWA_ROLES = ("ABM", "RBM")


class WorkDistributionUploadPage(ctk.CTkFrame):
    """RGD Coverage (fully working) + Manager Work Allocation (UI-only placeholder)."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self.browse_buttons: dict[str, PrimaryButton] = {}
        self.file_labels: dict[str, ctk.CTkLabel] = {}
        self._loaded_doctors: dict[str, list] = {}
        self._loaded_file_names: dict[str, str] = {}
        self._mwa_file_labels: dict[str, ctk.CTkLabel] = {}
        self._mwa_browse_buttons: dict[str, dict[str, PrimaryButton]] = {role: {} for role in MWA_ROLES}
        self._mwa_records: dict[str, dict[str, list]] = {role: {} for role in MWA_ROLES}
        self._checking = False
        self._banner_dismissed = False
        self._build_widgets()
        self.loading_overlay = LoadingOverlay(self)
        self.refresh_from_state()

    def on_show(self) -> None:
        self._run_check()

    def refresh_from_state(self) -> None:
        """Renders every slot's CURRENTLY PERSISTED filename -- same
        pull-to-render-parity pattern as ui/inventory_upload_page.py's own
        refresh_from_state(): a pulled file renders identically to a
        locally uploaded one, no visual way to tell them apart. Never
        touches self._loaded_doctors/_mwa_records or the Run Analysis
        buttons -- those stay local-upload-only (see
        app/work_distribution_sync_service.py's upload_and_sync docstring:
        a sync pull never substitutes for the manual Run Analysis click,
        it auto-runs the combined engine itself once all 3 divisions'
        current files are retained)."""
        for division in DIVISION_SLOTS:
            state = get_slot_state(RGD, division)
            if state["uploaded"]:
                self.file_labels[division].configure(text=state["filename"], text_color=Color.TEXT_PRIMARY)
        for role in MWA_ROLES:
            for division in DIVISION_SLOTS:
                state = get_slot_state(ABM if role == "ABM" else RBM, division)
                if state["uploaded"]:
                    self._mwa_file_labels[f"{role}_{division}"].configure(
                        text=state["filename"], text_color=Color.TEXT_PRIMARY
                    )

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(outer, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.SM))
        SectionHeader(
            header_row, "Work Distribution Upload", "Upload each division's current month's reports"
        ).pack(side="left", anchor="w")
        self._refresh_button = SecondaryButton(
            header_row, text="Refresh", image=get_icon("refresh", size=14, color=Color.PRIMARY),
            command=self._run_check,
        )
        self._refresh_button.pack(side="right", anchor="n")

        self._sync_status_label = ctk.CTkLabel(
            outer, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w"
        )
        self._sync_status_label.pack(anchor="w", pady=(0, Spacing.SM))

        self._banner_container = ctk.CTkFrame(outer, fg_color="transparent")
        self._banner_container.pack(fill="x", pady=(0, Spacing.LG))

        rgd_section = CollapsibleSection(outer, "RGD Coverage", expanded=True)
        rgd_section.pack(fill="x", pady=(0, Spacing.LG))
        self._build_rgd_coverage_body(rgd_section.body)

        mwa_section = CollapsibleSection(outer, "Manager Work Allocation", expanded=False)
        mwa_section.pack(fill="x")
        self._build_manager_work_allocation_body(mwa_section.body)

    # --- RGD Coverage (unchanged behavior) ---------------------------------

    def _build_rgd_coverage_body(self, body) -> None:
        ctk.CTkLabel(
            body, text="Upload Coverage Reports", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "Select each division's monthly doctor coverage workbook. Once all three are "
                "loaded, they're combined into one dataset before Run Analysis processes them --"
                " exactly as if it were a single file."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        for division in DIVISION_SLOTS:
            slot_row = ctk.CTkFrame(body, fg_color="transparent")
            slot_row.pack(fill="x", pady=(0, Spacing.SM))

            browse_button = PrimaryButton(
                slot_row,
                text=f"Upload {division} Coverage Report",
                image=get_icon("upload", size=16, color=Color.TEXT_ON_PRIMARY),
                command=lambda d=division: self._on_browse_clicked(d),
            )
            browse_button.pack(side="left")
            self.browse_buttons[division] = browse_button

            file_label = ctk.CTkLabel(
                slot_row, text="No file selected", font=Font.BODY, text_color=Color.TEXT_MUTED, anchor="w"
            )
            file_label.pack(side="left", padx=(Spacing.MD, 0))
            self.file_labels[division] = file_label

        self.run_button = PrimaryButton(
            body, text="Run Analysis", height=44, font=Font.H3, state="disabled",
            command=self._on_run_analysis_clicked,
        )
        self.run_button.pack(fill="x", pady=(Spacing.SM, 0))

        self.status_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w",
            wraplength=650, justify="left",
        )
        self.status_label.pack(anchor="w", pady=(Spacing.SM, 0))

    # --- Manager Work Allocation (both ABM and RBM engines live) -----------

    def _build_manager_work_allocation_body(self, body) -> None:
        ctk.CTkLabel(
            body, text="Manager Work Allocation", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "Select each division's monthly joint-working report for both ABM and RBM, then "
                "Run Analysis -- both engines run together in one click."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        for role in MWA_ROLES:
            role_section = CollapsibleSection(body, role, expanded=True, nested=True)
            role_section.pack(fill="x", pady=(0, Spacing.MD))
            for division in DIVISION_SLOTS:
                self._build_mwa_upload_row(role_section.body, role, division)

        self.mwa_run_button = PrimaryButton(
            body, text="Run Analysis", height=44, font=Font.H3, state="disabled",
            command=self._on_mwa_run_analysis_clicked,
        )
        self.mwa_run_button.pack(fill="x", pady=(Spacing.SM, 0))

        self.mwa_status_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w",
            wraplength=650, justify="left",
        )
        self.mwa_status_label.pack(anchor="w", pady=(Spacing.SM, 0))

    def _build_mwa_upload_row(self, parent, role: str, division: str) -> None:
        key = f"{role}_{division}"
        slot_row = ctk.CTkFrame(parent, fg_color="transparent")
        slot_row.pack(fill="x", pady=(0, Spacing.SM))

        browse_button = PrimaryButton(
            slot_row,
            text=f"Upload {division}",
            image=get_icon("upload", size=16, color=Color.TEXT_ON_PRIMARY),
            command=lambda: self._on_mwa_browse_clicked(role, division),
        )
        browse_button.pack(side="left")
        self._mwa_browse_buttons[role][division] = browse_button

        file_label = ctk.CTkLabel(
            slot_row, text="No file selected", font=Font.BODY, text_color=Color.TEXT_MUTED, anchor="w"
        )
        file_label.pack(side="left", padx=(Spacing.MD, 0))
        self._mwa_file_labels[key] = file_label

    def _all_mwa_browse_buttons(self):
        for role_buttons in self._mwa_browse_buttons.values():
            yield from role_buttons.values()

    def _on_mwa_browse_clicked(self, role: str, division: str) -> None:
        key = f"{role}_{division}"
        file_path = filedialog.askopenfilename(
            title=f"Select {division} {role} Work Allocation Report",
            filetypes=[
                ("Supported files", "*.xlsx *.xls *.xlsm *.csv"),
                ("Excel Workbook (*.xlsx)", "*.xlsx"),
                ("CSV files (*.csv)", "*.csv"),
                ("All files", "*.*"),
            ],
        )
        if not file_path:
            logger.info(f"{division} {role} Work Allocation report upload cancelled by user")
            return

        if Path(file_path).suffix.lower() not in MWA_SUPPORTED_EXTENSIONS:
            messagebox.showerror(
                "Unsupported File Type",
                "Unsupported file type. Please upload an Excel (.xlsx, .xls, .xlsm) or CSV (.csv) file.",
            )
            return

        logger.info(f"Loading {division} {role} Work Allocation report: {file_path}")
        self._mwa_browse_buttons[role][division].configure(state="disabled")
        self.mwa_run_button.configure(state="disabled")
        self.loading_overlay.show()

        def work(report_progress):
            return parse_manager_work_allocation_report(file_path, progress_callback=report_progress)

        def on_progress(percent, message):
            self.loading_overlay.update_progress(percent, message)

        def on_done(parse_result, error):
            self._mwa_browse_buttons[role][division].configure(state="normal")

            if error is not None:
                logger.error(f"Unexpected error parsing {division} {role} Work Allocation report '{file_path}': {error}")
                self.loading_overlay.hide()
                messagebox.showerror("Upload Failed", f"Could not process the selected file.\n\n{error}")
                return

            if parse_result["error"] is not None:
                self.loading_overlay.hide()
                messagebox.showerror("Upload Failed", f"Could not open the selected file.\n\n{parse_result['error']}")
                return

            if not parse_result["success"]:
                self.loading_overlay.hide()
                debug = parse_result.get("debug")
                if debug:
                    matched_text = ", ".join(debug["matched_columns"]) or "(none)"
                    missing_text = ", ".join(debug["missing_columns"]) or "(none)"
                    detected_text = ", ".join(str(c) for c in debug["detected_columns"]) or "(none)"
                    messagebox.showerror(
                        f"Invalid {division} {role} Work Allocation Report",
                        "Could not find a header row matching every required column.\n\n"
                        f"Worksheet checked: {debug['sheet_name']!r}\n"
                        f"Closest header row: {debug['header_row_number']}\n"
                        f"Columns matched there: {matched_text}\n"
                        f"Columns still missing: {missing_text}\n"
                        f"Every non-blank value seen in that row: {detected_text}\n\n"
                        f"Please confirm the uploaded file is the {division} {role} Work Allocation report.",
                    )
                else:
                    bullet_list = "\n".join(f"• {col}" for col in parse_result["missing_columns"])
                    messagebox.showerror(
                        f"Invalid {division} {role} Work Allocation Report",
                        f"The uploaded {division} report is invalid.\n"
                        f"Missing or unrecognized columns:\n{bullet_list}",
                    )
                self._update_mwa_run_button_state()
                return

            self._mwa_records[role][division] = parse_result["records"]
            self._mwa_file_labels[key].configure(text=Path(file_path).name, text_color=Color.TEXT_PRIMARY)
            record_upload(file_path, f"Manager Work Allocation ({role})", division=division)
            # Retain the source file locally and push it to the sync
            # manifest -- see app/work_distribution_sync_service.py.
            report_type = ABM if role == "ABM" else RBM
            sync_result = upload_and_sync(slot_id_for(report_type, division), file_path)
            if not sync_result.get("synced"):
                messagebox.showwarning("Not Synced", sync_result.get("sync_error") or "This file was not synced to the cloud.")

            loaded_count = sum(len(self._mwa_records[r]) for r in MWA_ROLES)
            total_slots = len(MWA_ROLES) * len(DIVISION_SLOTS)
            self.mwa_status_label.configure(
                text=(
                    f"{role} {division}: {len(parse_result['records']):,} record(s) loaded. "
                    f"{loaded_count} of {total_slots} division report(s) loaded."
                    + (" Ready to run analysis." if loaded_count == total_slots else "")
                ),
                text_color=Color.SUCCESS,
            )
            self.after(400, self.loading_overlay.hide)
            self._update_mwa_run_button_state()

        run_in_background(self, work, on_progress=on_progress, on_done=on_done)

    def _update_mwa_run_button_state(self) -> None:
        ready = all(len(self._mwa_records[role]) == len(DIVISION_SLOTS) for role in MWA_ROLES)
        self.mwa_run_button.configure(state="normal" if ready else "disabled")

    def _on_mwa_run_analysis_clicked(self) -> None:
        if not all(len(self._mwa_records[role]) == len(DIVISION_SLOTS) for role in MWA_ROLES):
            return

        combined_by_role = {
            role: [record for division in DIVISION_SLOTS for record in self._mwa_records[role][division]]
            for role in MWA_ROLES
        }
        # TEMPORARY debug aid -- see app.manager_work_allocation_shared.log_designation_filter_diagnostics
        # for the next stage of this trace. Remove once the "Run Analysis shows 0" root cause is fixed.
        for role in MWA_ROLES:
            per_division_counts = {d: len(self._mwa_records[role][d]) for d in DIVISION_SLOTS}
            logger.info(
                f"Manager Work Allocation ({role}) diagnostics: {per_division_counts} row(s) per division -> "
                f"{len(combined_by_role[role])} row(s) combined, about to enter the calculation engine"
            )

        for button in self._all_mwa_browse_buttons():
            button.configure(state="disabled")
        self.mwa_run_button.configure(state="disabled")
        self.loading_overlay.show()
        self.loading_overlay.update_progress(10, "Reading uploaded reports...")

        def work(report_progress):
            report_progress(30, "Calculating ABM engine...")
            abm_summary = process_manager_work_allocation_report(combined_by_role["ABM"])
            report_progress(65, "Calculating RBM engine...")
            rbm_summary = process_rbm_report(combined_by_role["RBM"])
            report_progress(100, "Done")
            return {"abm": abm_summary, "rbm": rbm_summary}

        def on_progress(percent, message):
            self.loading_overlay.update_progress(percent, message)

        def on_done(summary, error):
            for button in self._all_mwa_browse_buttons():
                button.configure(state="normal")

            if error is not None:
                logger.error(f"Unexpected error running Manager Work Allocation analysis: {error}")
                self.loading_overlay.hide()
                messagebox.showerror("Analysis Failed", f"Could not run the analysis.\n\n{error}")
                self._update_mwa_run_button_state()
                return

            abm, rbm = summary["abm"], summary["rbm"]
            self.mwa_status_label.configure(
                text=(
                    f"Analysis complete. ABM: {abm['abm_count']} ABM(s), {abm['flagged_count']} flagged, "
                    f"{abm['passed_count']} passed. "
                    f"RBM: {rbm['rbm_count']} RBM(s), {rbm['flagged_count']} flagged, "
                    f"{rbm['passed_count']} passed."
                ),
                text_color=Color.SUCCESS,
            )
            self.after(400, self.loading_overlay.hide)

            self._mwa_records = {role: {} for role in MWA_ROLES}
            for role in MWA_ROLES:
                for division in DIVISION_SLOTS:
                    self._mwa_file_labels[f"{role}_{division}"].configure(
                        text="No file selected", text_color=Color.TEXT_MUTED
                    )
            self._update_mwa_run_button_state()

        run_in_background(self, work, on_progress=on_progress, on_done=on_done)

    def _on_browse_clicked(self, division: str) -> None:
        file_path = filedialog.askopenfilename(
            title=f"Select {division} Coverage Report",
            filetypes=[
                ("Supported files", "*.xlsx *.xls *.xlsm *.csv"),
                ("Excel Workbook (*.xlsx)", "*.xlsx"),
                ("CSV files (*.csv)", "*.csv"),
                ("All files", "*.*"),
            ],
        )
        if not file_path:
            logger.info(f"{division} Work Distribution report upload cancelled by user")
            return

        if Path(file_path).suffix.lower() not in SUPPORTED_EXTENSIONS:
            messagebox.showerror(
                "Unsupported File Type",
                "Unsupported file type. Please upload an Excel (.xlsx, .xls, .xlsm) or CSV (.csv) file.",
            )
            return

        logger.info(f"Loading {division} Work Distribution report: {file_path}")
        self.browse_buttons[division].configure(state="disabled")
        self.run_button.configure(state="disabled")
        self.loading_overlay.show()

        def work(report_progress):
            return parse_work_distribution_report(file_path, progress_callback=report_progress)

        def on_progress(percent, message):
            self.loading_overlay.update_progress(percent, message)

        def on_done(parse_result, error):
            self.browse_buttons[division].configure(state="normal")

            if error is not None:
                logger.error(f"Unexpected error parsing {division} Work Distribution report '{file_path}': {error}")
                self.loading_overlay.hide()
                messagebox.showerror("Upload Failed", f"Could not process the selected file.\n\n{error}")
                return

            if parse_result["error"] is not None:
                self.loading_overlay.hide()
                messagebox.showerror("Upload Failed", f"Could not open the selected file.\n\n{parse_result['error']}")
                return

            if not parse_result["success"]:
                self.loading_overlay.hide()
                debug = parse_result.get("debug")
                if debug:
                    matched_text = ", ".join(debug["matched_columns"]) or "(none)"
                    missing_text = ", ".join(debug["missing_columns"]) or "(none)"
                    detected_text = ", ".join(str(c) for c in debug["detected_columns"]) or "(none)"
                    messagebox.showerror(
                        f"Invalid {division} Coverage Report",
                        "Could not find a header row matching every required column.\n\n"
                        f"Worksheet checked: {debug['sheet_name']!r}\n"
                        f"Closest header row: {debug['header_row_number']}\n"
                        f"Columns matched there: {matched_text}\n"
                        f"Columns still missing: {missing_text}\n"
                        f"Every non-blank value seen in that row: {detected_text}\n\n"
                        f"Please confirm the uploaded file is the {division} coverage report.",
                    )
                else:
                    bullet_list = "\n".join(f"• {col}" for col in parse_result["missing_columns"])
                    messagebox.showerror(
                        f"Invalid {division} Coverage Report",
                        f"The uploaded {division} report is invalid.\n"
                        f"Missing or unrecognized columns:\n{bullet_list}",
                    )
                self._update_run_button_state()
                return

            self._loaded_doctors[division] = parse_result["doctors"]
            self._loaded_file_names[division] = Path(file_path).name
            self.file_labels[division].configure(text=Path(file_path).name, text_color=Color.TEXT_PRIMARY)
            record_upload(file_path, "RGD Coverage", division=division)
            # Retain the source file locally and push it to the sync
            # manifest -- see app/work_distribution_sync_service.py.
            # upload_and_sync re-validates internally (harmless, same
            # file) and never auto-runs analysis on a LOCAL upload -- Run
            # Analysis below stays the only trigger for this machine's own
            # upload, exactly as before.
            sync_result = upload_and_sync(slot_id_for(RGD, division), file_path)
            if not sync_result.get("synced"):
                messagebox.showwarning("Not Synced", sync_result.get("sync_error") or "This file was not synced to the cloud.")

            loaded_count = len(self._loaded_doctors)
            total_slots = len(DIVISION_SLOTS)
            period_note = f" ({parse_result['period_label']})" if parse_result["period_label"] else ""
            self.status_label.configure(
                text=(
                    f"{division}{period_note}: {len(parse_result['doctors']):,} doctor row(s) loaded. "
                    f"{loaded_count} of {total_slots} division report(s) loaded."
                    + (" Ready to run analysis." if loaded_count == total_slots else "")
                ),
                text_color=Color.SUCCESS,
            )
            self.after(400, self.loading_overlay.hide)
            self._update_run_button_state()

        run_in_background(self, work, on_progress=on_progress, on_done=on_done)

    def _update_run_button_state(self) -> None:
        self.run_button.configure(
            state="normal" if len(self._loaded_doctors) == len(DIVISION_SLOTS) else "disabled"
        )

    def _on_run_analysis_clicked(self) -> None:
        if len(self._loaded_doctors) != len(DIVISION_SLOTS):
            return

        # The three division files are merged into exactly one doctor list
        # here -- process_work_distribution_report never knows or cares
        # that it came from three separate uploads instead of one combined
        # file, same as Operations page's own pd.concat before its own
        # unchanged pipeline runs.
        combined_doctors = []
        for division in DIVISION_SLOTS:
            combined_doctors.extend(self._loaded_doctors[division])

        for button in self.browse_buttons.values():
            button.configure(state="disabled")
        self.run_button.configure(state="disabled")
        self.loading_overlay.show()
        self.loading_overlay.update_progress(20, "Calculating KPIs...")

        def work(report_progress):
            report_progress(20, "Calculating KPIs...")
            summary = process_work_distribution_report(combined_doctors)
            report_progress(100, "Done")
            return summary

        def on_progress(percent, message):
            self.loading_overlay.update_progress(percent, message)

        def on_done(summary, error):
            for button in self.browse_buttons.values():
                button.configure(state="normal")

            if error is not None:
                logger.error(f"Unexpected error running Work Distribution analysis: {error}")
                self.loading_overlay.hide()
                messagebox.showerror("Analysis Failed", f"Could not run the analysis.\n\n{error}")
                self._update_run_button_state()
                return

            self.status_label.configure(
                text=(
                    f"Analysis complete: {summary['total_doctors']:,} doctor row(s) across "
                    f"{summary['total_employees']:,} employee(s) ({summary['bm_count']} BM, "
                    f"{summary['abm_count']} ABM) — {summary['flagged_count']:,} flagged, "
                    f"{summary['healthy_count']:,} healthy."
                ),
                text_color=Color.SUCCESS,
            )
            self.after(400, self.loading_overlay.hide)

            self._loaded_doctors = {}
            self._loaded_file_names = {}
            for division in DIVISION_SLOTS:
                self.file_labels[division].configure(text="No file selected", text_color=Color.TEXT_MUTED)
            self._update_run_button_state()

        run_in_background(self, work, on_progress=on_progress, on_done=on_done)

    # --- Sync check (banner) -- covers this page's 9 slots only; the
    # other 3 (hierarchy) are ui/work_distribution_email_center_page.py's
    # own banner, sharing the same underlying check_for_updates()/
    # apply_pending_updates() pair (see app/work_distribution_sync_service.py).

    def _run_check(self) -> None:
        if self._checking:
            return
        self._checking = True
        self._refresh_button.configure(state="disabled")

        def work(_report_progress):
            return check_for_updates()

        def on_done(result, error):
            self._checking = False
            if self._refresh_button.winfo_exists():
                self._refresh_button.configure(state="normal")
            if error is not None:
                logger.error(f"Work Distribution sync: check raised unexpectedly: {error!r}")
                result = {"ok": False, "reason": "error"}

            self._banner_dismissed = False
            self._render_sync_status(result)
            self._render_banner(result)
            if result.get("ok"):
                self.refresh_from_state()

        run_in_background(self, work, on_done=on_done)

    def _render_sync_status(self, result: dict) -> None:
        if not self._sync_status_label.winfo_exists():
            return
        if not result.get("ok"):
            reason = "Could not reach Supabase" if result.get("reason") == "offline" else "Last check failed"
            self._sync_status_label.configure(text=f"⚠ {reason} -- showing this machine's last known state.")
            return
        page_changed = [c for c in (result.get("changed") or []) if c["slot_id"] in _PAGE_SLOTS]
        self._sync_status_label.configure(
            text="Synced." if not page_changed else f"{len(page_changed)} update(s) available below."
        )

    def _clear_banner(self) -> None:
        for widget in self._banner_container.winfo_children():
            widget.destroy()

    def _render_banner(self, result: dict) -> None:
        self._clear_banner()
        if self._banner_dismissed or not result.get("ok"):
            return
        changed = [c for c in (result.get("changed") or []) if c["slot_id"] in _PAGE_SLOTS]
        if not changed:
            return

        replacements = [c for c in changed if c["is_replacement"]]
        first_fills = [c for c in changed if c["is_first_fill"]]

        lines = []
        for c in replacements:
            lines.append(f"{SLOT_LABELS[c['slot_id']]} was replaced by {c['uploader_name']}.")
        if first_fills:
            lines.append(f"{len(first_fills)} new file{'s' if len(first_fills) != 1 else ''} available.")

        banner = ctk.CTkFrame(self._banner_container, fg_color=Color.WARNING_SOFT, corner_radius=Radius.SM)
        banner.pack(fill="x")
        body = ctk.CTkFrame(banner, fg_color="transparent")
        body.pack(fill="x", padx=Spacing.MD, pady=Spacing.SM)

        text_col = ctk.CTkFrame(body, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True)
        for line in lines:
            ctk.CTkLabel(
                text_col, text=f"⚠  {line}", font=Font.BODY, text_color=Color.WARNING, anchor="w",
                wraplength=650, justify="left",
            ).pack(anchor="w")

        button_col = ctk.CTkFrame(body, fg_color="transparent")
        button_col.pack(side="right")
        PrimaryButton(
            button_col, text="Pull Updates", command=lambda: self._on_pull_clicked([c["slot_id"] for c in changed])
        ).pack(side="left", padx=(0, Spacing.SM))
        ctk.CTkButton(
            button_col, text="✕", width=28, height=28, fg_color="transparent",
            text_color=Color.WARNING, hover_color=Color.WARNING_SOFT,
            command=self._on_dismiss_banner,
        ).pack(side="left")

    def _on_dismiss_banner(self) -> None:
        self._banner_dismissed = True
        self._clear_banner()

    def _on_pull_clicked(self, slot_ids: list[str]) -> None:
        self._clear_banner()
        ctk.CTkLabel(
            self._banner_container, text="Pulling updates...", font=Font.BODY, text_color=Color.INFO, anchor="w"
        ).pack(anchor="w")

        def work(_report_progress):
            return apply_pending_updates(slot_ids)

        def on_done(result, error):
            self._clear_banner()
            if error is not None:
                logger.error(f"Work Distribution sync: apply raised unexpectedly: {error!r}")
                messagebox.showerror("Sync Failed", f"Could not apply updates.\n\n{error}")
                self._run_check()
                return

            self.refresh_from_state()

            problems = []
            for f in result["failed"]:
                problems.append(f"{SLOT_LABELS[f['slot_id']]}: {f['reason']}")
            for v in result["version_blocked"]:
                problems.append(
                    f"{SLOT_LABELS[v['slot_id']]}: uploaded by a newer app version "
                    f"({v['remote_version']}) than this one -- update the app to apply it."
                )
            if problems:
                messagebox.showerror(
                    "Some Updates Could Not Be Applied",
                    "\n\n".join(problems) + "\n\nThese slots will show again next time you check.",
                )

            # Re-check: a partial failure leaves the failed slots in
            # `changed` again on the next check, so the banner persists
            # for exactly those.
            self._run_check()

        run_in_background(self, work, on_done=on_done)
