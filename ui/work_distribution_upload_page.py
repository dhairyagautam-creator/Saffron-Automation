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

Automatic Email Sending (see app.work_distribution_email_settings_service):
when enabled from the Email Center's own switch, BOTH Run Analysis buttons
(RGD Coverage and Manager Work Allocation) automatically build and send
every currently flagged employee's notification right after their own
analysis completes -- see _maybe_auto_send_notifications(), called at the
end of each button's own on_done(). Mirrors ui/operations_page.py's own
_start_automatic_send: no manual Preview/Send click, no confirmation
dialog, since this IS the unsupervised automatic path the user explicitly
opted into. When the switch is off (the default), nothing is ever sent
from here -- the Email Center's manual Preview/Send controls are the only
way to send.
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
from app.work_distribution_email_settings_service import is_automatic_sending_enabled
from app.work_distribution_notification_service import build_notification_batch, send_notification_batch
from app.work_distribution_parser import SUPPORTED_EXTENSIONS, parse_work_distribution_report
from app.work_distribution_service import process_work_distribution_report
from app.work_distribution_upload_log_service import record_upload
from ui.background_task import run_in_background
from ui.components import CollapsibleSection, PrimaryButton, SectionHeader
from ui.icons import get_icon
from ui.loading_overlay import LoadingOverlay
from ui.theme import Color, Font, Spacing

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
        self._build_widgets()
        self.loading_overlay = LoadingOverlay(self)

    def on_show(self) -> None:
        pass

    def _maybe_auto_send_notifications(self, status_label: ctk.CTkLabel) -> None:
        """Called at the end of EITHER Run Analysis button's own on_done()
        -- see module docstring. No-op if Automatic Email Sending is off
        (the default)."""
        if not is_automatic_sending_enabled():
            return

        base_text = status_label.cget("text")
        status_label.configure(text=base_text + " Sending notifications automatically…")

        def work(report_progress):
            drafts = build_notification_batch()
            if not drafts:
                return {"sent_count": 0, "failed_count": 0, "drafts": []}
            return send_notification_batch(drafts, progress_callback=report_progress)

        def on_done(result, error):
            if error is not None:
                logger.error(f"Work Distribution automatic notification send failed: {error}")
                status_label.configure(
                    text=base_text + " Automatic notification send FAILED — see logs.", text_color=Color.ERROR,
                )
                return
            status_label.configure(
                text=(
                    base_text + f" Automatic notifications: {result['sent_count']} sent, "
                    f"{result['failed_count']} failed."
                ),
                text_color=Color.SUCCESS if result["failed_count"] == 0 else Color.WARNING,
            )

        run_in_background(self, work, on_done=on_done)

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Work Distribution Upload", "Upload each division's current month's reports"
        ).pack(anchor="w", pady=(0, Spacing.LG))

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
            self._maybe_auto_send_notifications(self.mwa_status_label)

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
            self._maybe_auto_send_notifications(self.status_label)

            self._loaded_doctors = {}
            self._loaded_file_names = {}
            for division in DIVISION_SLOTS:
                self.file_labels[division].configure(text="No file selected", text_color=Color.TEXT_MUTED)
            self._update_run_button_state()

        run_in_background(self, work, on_progress=on_progress, on_done=on_done)
