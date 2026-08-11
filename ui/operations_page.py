"""Operations page: import the daily call workbook and run the analysis pipeline.

Only one workbook is ever "active" at a time (see app/session_state.py).
Importing a new one replaces it — analysis, findings, and email generation
only ever operate on the active session file. Previously imported files
stay in the database but don't affect anything shown here. There is no
manual "send" step anywhere in the app: if automatic sending is enabled
(Settings page), manager emails go out on their own right after rule
evaluation finishes, and results show up on the Email Center monitoring
page with no further action needed.

The Processing Progress Center below replaces a plain "frozen-looking"
status line with a live progress bar per pipeline stage — binary stages
(file selected, columns validated, workbook loaded, ...) jump from 0% to
100%, item-based stages (hospital suppression, reverse geocoding, email
generation, sending) show a live "N / M" count as
app.notification_service's structured progress_callback reports each item
completing.
"""

import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import pandas as pd
from loguru import logger

from app.config import REQUIRED_COLUMNS
from app.coordinates import parse_coordinates
from app.email_settings_service import is_automatic_sending_enabled
from app.feature_flags_service import is_feature_enabled
from app.findings_service import get_summary_counts
from app.findings_sync_service import sync_findings_for_import
from app.import_sync_service import push_active_session, push_import
from app.metrics import calculate_metrics
from app.notification_service import preview_email_batch, send_all_emails
from app.send_state import finish_sending, start_sending
from app.session_state import get_active_import, set_active_import
from app.timing import get_current_report, start_new_report
from database.connection import get_session
from database.import_service import save_import
from database.models import EmailNotification, ImportHistory
from rules.hours_worked import evaluate as evaluate_hours_worked
from rules.same_location import evaluate as evaluate_same_location
from ui.components import Card, EmptyState, KPICard, PrimaryButton, SectionHeader, StatusBadge, styled_treeview
from ui.icons import get_icon
from app.table_export_service import default_export_filename, export_rows_with_ui
from ui.theme import Color, Font, Spacing

# Import History table (see _render_history) -- module-level so
# _on_export_history_clicked can reference the same columns/headings the
# Treeview itself uses, matching every other export-framework page.
HISTORY_COLUMNS = ("file_name", "imported_at", "rows_imported", "duplicates_removed")
HISTORY_HEADINGS = {
    "file_name": "File",
    "imported_at": "Imported At",
    "rows_imported": "Rows",
    "duplicates_removed": "Duplicates Removed",
}
HISTORY_WIDTHS = {"file_name": 260, "imported_at": 150, "rows_imported": 90, "duplicates_removed": 130}

# Each stage: (key, display label, kind, verb). "binary" stages just jump
# 0% -> 100% (success) or show an error tint (fail); "count" stages show a
# live "N / M <verb>" as items complete. Keys line up with the `stage`
# strings app.notification_service.build_email_batch/send_all_emails pass
# to progress_callback, so the two pre-analysis (browse-time) and three
# analysis-time stages set below use their own local keys while the rest
# are driven directly by that callback.
PIPELINE_STAGES = [
    ("file_selected", "File Selected", "binary", None),
    ("columns_validated", "Columns Validated", "binary", None),
    ("workbook_loading", "Loading Workbook", "binary", None),
    ("coordinate_parsing", "Parsing Employees", "binary", None),
    ("data_saved", "Saving Data", "binary", None),
    ("metrics_calculated", "Calculating Metrics", "binary", None),
    ("validation_clustering", "Validating Calls", "binary", None),
    ("hierarchy", "Resolving Hierarchy", "binary", None),
    ("hospital_suppression", "Hospital Suppression", "count", "checked"),
    ("geocoding", "Reverse Geocoding", "count", "completed"),
    ("email_generation", "Generating Emails", "count", "completed"),
    ("sending", "Sending Emails", "count", "sent"),
    ("finalizing", "Finalizing", "binary", None),
]
STAGE_ORDER = [key for key, *_ in PIPELINE_STAGES]
STAGE_KIND = {key: kind for key, _, kind, _ in PIPELINE_STAGES}
STAGE_VERB = {key: verb for key, _, _, verb in PIPELINE_STAGES}

# The daily call export comes as one file per division -- each with
# identical columns and its own correct "Division" value already in every
# row (confirmed against real Onyx/Guardians/Xandra exports). All three
# must be loaded before Run Analysis enables; they're concatenated into
# one DataFrame right before the existing, unchanged save/metrics/rule
# pipeline runs -- see _on_run_analysis_clicked.
DIVISION_SLOTS = ["Xandra", "Onyx", "Guardians"]


class ProgressRow(ctk.CTkFrame):
    """One pipeline stage: a label, a determinate progress bar, and a
    trailing status/count. `state` is "pending"/"active"/"success"/"fail"
    for binary stages; item-based stages instead call `set_progress`."""

    def __init__(self, master, label: str) -> None:
        super().__init__(master, fg_color="transparent")
        self._label_text = label
        self.state = "pending"

        self.label = ctk.CTkLabel(
            self, text=label, font=Font.BODY, text_color=Color.TEXT_MUTED, anchor="w", width=170
        )
        self.label.pack(side="left")

        self.bar = ctk.CTkProgressBar(self, height=8, progress_color=Color.TEXT_MUTED)
        self.bar.set(0)
        self.bar.pack(side="left", fill="x", expand=True, padx=(Spacing.SM, Spacing.SM))

        self.trailing = ctk.CTkLabel(
            self, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED, width=130, anchor="e"
        )
        self.trailing.pack(side="left")

    def reset(self) -> None:
        self.state = "pending"
        self.bar.configure(progress_color=Color.TEXT_MUTED)
        self.bar.set(0)
        self.label.configure(text_color=Color.TEXT_MUTED)
        self.trailing.configure(text="")

    def set_binary(self, state: str) -> None:
        self.state = state
        if state == "active":
            self.bar.configure(progress_color=Color.PRIMARY)
            self.bar.set(0.5)
            self.label.configure(text_color=Color.PRIMARY)
            self.trailing.configure(text="Working…")
        elif state == "success":
            self.bar.configure(progress_color=Color.SUCCESS)
            self.bar.set(1.0)
            self.label.configure(text_color=Color.TEXT_PRIMARY)
            self.trailing.configure(text="100%")
        elif state == "fail":
            self.bar.configure(progress_color=Color.ERROR)
            self.bar.set(1.0)
            self.label.configure(text_color=Color.ERROR)
            self.trailing.configure(text="Failed")

    def set_progress(self, completed: int, total: int, verb: str) -> None:
        fraction = (completed / total) if total else 0.0
        done = total > 0 and completed >= total
        self.state = "success" if done else "active"
        self.bar.configure(progress_color=Color.SUCCESS if done else Color.PRIMARY)
        self.bar.set(fraction)
        self.label.configure(text_color=Color.TEXT_PRIMARY if done else Color.PRIMARY)
        self.trailing.configure(text=f"{completed} / {total} {verb}")


class OperationsPage(ctk.CTkFrame):
    """Select a daily workbook, validate/prepare it, then run the full
    save + metrics + rule-evaluation pipeline against it as the active
    session file."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)

        # The Hospital Suppression progress row only exists when that
        # experimental feature is on for the current mode — hidden from
        # production entirely. `_stage_order` drives both the rows and the
        # retroactive-completion sweep, so they stay consistent.
        self._stages = [
            s for s in PIPELINE_STAGES
            if s[0] != "hospital_suppression" or is_feature_enabled("hospital_suppression")
        ]
        self._stage_order = [key for key, *_ in self._stages]

        self.progress_rows: dict[str, ProgressRow] = {}
        self.session_cards: dict[str, KPICard] = {}
        self.browse_buttons: dict[str, PrimaryButton] = {}
        self.file_labels: dict[str, ctk.CTkLabel] = {}
        self._loaded_dfs: dict[str, pd.DataFrame] = {}
        self._loaded_file_names: dict[str, str] = {}
        self._loaded_file_paths: dict[str, str] = {}
        self._coord_stats: dict[str, dict] = {}
        self._current_display_rows: list[dict] = []

        self._build_widgets()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(outer, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.LG))

        SectionHeader(
            header_row,
            "Operations",
            "Import the daily call workbook and run the analysis pipeline",
        ).pack(side="left")

        self.overall_status_badge = StatusBadge(header_row, "Ready", "neutral")
        self.overall_status_badge.pack(side="right", anchor="e", pady=(10, 0))

        # --- Active session summary --------------------------------------------
        session_row = ctk.CTkFrame(outer, fg_color="transparent")
        session_row.pack(fill="x", pady=(0, Spacing.LG))
        session_specs = [
            ("Active File", Color.PRIMARY),
            ("Records Loaded", Color.INFO),
            ("Findings Generated", Color.WARNING),
            ("Email Batch", Color.TEXT_MUTED),
        ]
        for i, (label, accent) in enumerate(session_specs):
            session_row.grid_columnconfigure(i, weight=1)
            card = KPICard(session_row, label=label, value="—", accent=accent)
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else Spacing.SM, 0))
            self.session_cards[label] = card

        # --- Import + Run Analysis -------------------------------------------------
        import_card = Card(outer)
        import_card.pack(fill="x", pady=(0, Spacing.LG))

        import_body = ctk.CTkFrame(import_card, fg_color="transparent")
        import_body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            import_body,
            text="Import Daily Reports",
            font=Font.H2,
            text_color=Color.TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            import_body,
            text=(
                "Select each division's daily EQbit call-report Excel file. Once all "
                "three are loaded, they're automatically combined into one dataset "
                "before the analysis pipeline runs — exactly as if it were a single "
                "file. This becomes the active session, replacing whatever was active "
                "before."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        for division in DIVISION_SLOTS:
            slot_row = ctk.CTkFrame(import_body, fg_color="transparent")
            slot_row.pack(fill="x", pady=(0, Spacing.SM))

            browse_button = PrimaryButton(
                slot_row,
                text=f"Browse {division} Report",
                image=get_icon("upload", size=16, color=Color.TEXT_ON_PRIMARY),
                command=lambda d=division: self._on_browse_clicked(d),
            )
            browse_button.pack(side="left")
            self.browse_buttons[division] = browse_button

            file_label = ctk.CTkLabel(
                slot_row,
                text="No file selected",
                font=Font.BODY,
                text_color=Color.TEXT_MUTED,
                anchor="w",
            )
            file_label.pack(side="left", padx=(Spacing.MD, 0))
            self.file_labels[division] = file_label

        self.run_button = PrimaryButton(
            import_body,
            text="Run Analysis",
            height=44,
            font=Font.H3,
            state="disabled",
            command=self._on_run_analysis_clicked,
        )
        self.run_button.pack(fill="x", pady=(Spacing.SM, 0))

        # --- Processing Progress Center -----------------------------------------
        progress_card = Card(outer)
        progress_card.pack(fill="x", pady=(0, Spacing.LG))

        progress_body = ctk.CTkFrame(progress_card, fg_color="transparent")
        progress_body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            progress_body,
            text="Processing Progress",
            font=Font.H3,
            text_color=Color.TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w", pady=(0, Spacing.SM))

        for key, label, _kind, _verb in self._stages:
            row = ProgressRow(progress_body, label)
            row.pack(fill="x", pady=3)
            self.progress_rows[key] = row

        self.status_label = ctk.CTkLabel(
            progress_body,
            text="Ready",
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=760,
            justify="left",
        )
        self.status_label.pack(anchor="w", pady=(Spacing.SM, 0))

        # --- Import history ------------------------------------------------------
        history_card = Card(outer)
        history_card.pack(fill="both", expand=True)

        history_body = ctk.CTkFrame(history_card, fg_color="transparent")
        history_body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        history_header = ctk.CTkFrame(history_body, fg_color="transparent")
        history_header.pack(fill="x", pady=(0, Spacing.SM))
        ctk.CTkLabel(
            history_header,
            text="Import History",
            font=Font.H3,
            text_color=Color.TEXT_PRIMARY,
            anchor="w",
        ).pack(side="left")

        # Export sits at the far right of the section header.
        self.export_history_button = PrimaryButton(
            history_header,
            text="Export",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_export_history_clicked,
        )
        self.export_history_button.pack(side="right")

        self.history_container = ctk.CTkFrame(history_body, fg_color="transparent")
        self.history_container.pack(fill="both", expand=True)

        self.on_show()

    def on_show(self) -> None:
        """Called every time this page becomes visible — refresh live data
        from local SQLite. Pulling from the cloud is no longer this page's
        own job -- see the module-wide Refresh button/background poller in
        ui/path_validator_module.py, which call this page's on_show() again
        after a successful sync (see app/path_validator_refresh.py)."""
        self._render_history()
        self._refresh_session_summary()

    def _reset_progress(self) -> None:
        for row in self.progress_rows.values():
            row.reset()

    def _set_stage(self, key: str, state: str) -> None:
        """Directly set a binary stage's state — used for the pre-analysis
        (browse-time) and analysis-time stages that aren't driven by
        notification_service's progress_callback."""
        self.progress_rows[key].set_binary(state)

    def _apply_stage_update(self, stage: str, label: str | None = None, completed: int | None = None, total: int | None = None) -> None:
        """Handle one progress_callback(stage, label, completed, total) call
        from app.notification_service — used for every stage from "hierarchy"
        onward.

        Every stage strictly before this one gets swept and completed if it
        isn't already — checked directly against each row's own state rather
        than a separate "last completed index" counter. An earlier version
        tracked that counter and advanced it to include the *current* stage
        as soon as its own update arrived (before it had a chance to reach
        "success"), which meant the very next call's sweep started one
        stage too late and permanently skipped completing it — that's
        exactly why "Resolving Hierarchy" got stuck at "active" forever
        even though hierarchy resolution (and everything after it) had
        genuinely finished. Re-checking state instead of a monotonic index
        makes this self-correcting regardless of call order."""
        if stage not in self.progress_rows:
            return  # e.g. a hospital_suppression update in a mode where the row is hidden
        idx = self._stage_order.index(stage)
        for prior_key in self._stage_order[:idx]:
            row = self.progress_rows[prior_key]
            if row.state == "success":
                continue
            if STAGE_KIND[prior_key] == "binary":
                row.set_binary("success")
            else:
                row.set_progress(1, 1, STAGE_VERB[prior_key])

        row = self.progress_rows[stage]
        if STAGE_KIND[stage] == "binary":
            row.set_binary("success" if stage == "finalizing" else "active")
        else:
            row.set_progress(completed or 0, total or 0, STAGE_VERB[stage])

        if label:
            self.status_label.configure(text=label)

    def _refresh_session_summary(self) -> None:
        """Refresh the Active File / Records Loaded / Findings Generated /
        Email Batch cards and the Overall Status badge from the active
        session file only."""
        active_import = get_active_import()

        if active_import is None:
            self.session_cards["Active File"].set_value("None")
            self.session_cards["Records Loaded"].set_value("0")
            self.session_cards["Findings Generated"].set_value("0")
            self.session_cards["Email Batch"].set_value("Not generated")
            self.overall_status_badge.set_status("No active session", "neutral")
            return

        counts = get_summary_counts(active_import.id)

        session = get_session()
        try:
            sent_count = (
                session.query(EmailNotification)
                .filter_by(import_id=active_import.id, status="Sent")
                .count()
            )
        finally:
            session.close()

        self.session_cards["Active File"].set_value(active_import.file_name)
        self.session_cards["Records Loaded"].set_value(f"{active_import.rows_imported:,}")
        self.session_cards["Findings Generated"].set_value(f"{counts['Total']:,}")
        self.session_cards["Email Batch"].set_value(f"{sent_count:,} sent" if sent_count else "Not sent")

        if counts["Open"]:
            self.overall_status_badge.set_status(f"{counts['Open']} finding(s) need review", "warning")
        else:
            self.overall_status_badge.set_status("All clear", "success")

    def _on_export_history_clicked(self) -> None:
        """Exports `self._current_display_rows` -- the exact list
        `_render_history()` just used to populate the Treeview."""
        export_rows_with_ui(
            self,
            rows=self._current_display_rows,
            columns=HISTORY_COLUMNS,
            headings=HISTORY_HEADINGS,
            suggested_filename=default_export_filename("ImportHistory"),
            sheet_title="Import History",
        )

    def _render_history(self) -> None:
        for widget in self.history_container.winfo_children():
            widget.destroy()
        self._current_display_rows = []

        session = get_session()
        try:
            rows = (
                session.query(ImportHistory)
                .order_by(ImportHistory.imported_at.desc())
                .limit(20)
                .all()
            )
            data = [
                {
                    "file_name": row.file_name,
                    "imported_at": row.imported_at.strftime("%Y-%m-%d %H:%M"),
                    "rows_imported": f"{row.rows_imported:,}",
                    "duplicates_removed": f"{row.duplicates_removed:,}",
                }
                for row in rows
            ]
        finally:
            session.close()

        if not data:
            EmptyState(self.history_container, "No imports yet — import a workbook to get started.").pack(
                fill="both", expand=True
            )
            return

        self._current_display_rows = data

        tree = styled_treeview(self.history_container, HISTORY_COLUMNS, HISTORY_HEADINGS, HISTORY_WIDTHS, height=8)
        for row in data:
            tree.insert("", "end", values=tuple(row[col] for col in HISTORY_COLUMNS))
        tree.pack(fill="both", expand=True)

    def _on_browse_clicked(self, division: str) -> None:
        # Only reset the pipeline report/progress rows on the very first
        # slot of a fresh set of three -- resetting on every slot would
        # discard the previous slots' own "workbook loading"/"coordinate
        # parsing" timing entries before they ever reach the Timing
        # Summary. Re-browsing an already-loaded slot still gets its own
        # fresh validation below regardless.
        if not self._loaded_dfs:
            start_new_report()
            self._reset_progress()
            self.status_label.configure(text="Ready", text_color=Color.TEXT_SECONDARY)
        self.run_button.configure(state="disabled")

        file_path = filedialog.askopenfilename(
            title=f"Select {division} Daily Excel File",
            filetypes=[("Excel files", "*.xlsx")],
        )

        if not file_path:
            logger.info(f"Import {division} Daily Excel cancelled by user")
            return

        self._set_stage("file_selected", "success")
        self.browse_buttons[division].configure(state="disabled")
        try:
            self._set_stage("columns_validated", "active")
            self.status_label.configure(text=f"Loading {division} file…")
            self.update_idletasks()

            logger.info(f"Loading {division} Excel file: {file_path}")

            report = get_current_report()
            try:
                with report.timed(f"{division} workbook loading"):
                    df = pd.read_excel(file_path)
            except Exception as exc:
                logger.error(f"Failed to load {division} Excel file '{file_path}': {exc}")
                self._set_stage("columns_validated", "fail")
                messagebox.showerror("Import Failed", f"Could not open the selected {division} file.\n\n{exc}")
                self.status_label.configure(text="Ready")
                return
            self._set_stage("workbook_loading", "success")

            missing_columns = [col for col in REQUIRED_COLUMNS if col not in df.columns]

            if missing_columns:
                self._set_stage("columns_validated", "fail")
                logger.warning(f"{division} file missing required columns: {missing_columns}")
                messagebox.showerror(
                    "Missing Columns",
                    f"The uploaded {division} file is missing the following required columns:\n\n"
                    + "\n".join(missing_columns),
                )
                self.status_label.configure(text="Ready")
                return

            self._set_stage("columns_validated", "success")

            file_name = Path(file_path).name
            row_count = len(df)
            logger.info(f"Loaded {division} file '{file_name}' with {row_count} rows")

            self._set_stage("coordinate_parsing", "active")
            self.status_label.configure(text=f"Parsing {division} coordinates…")
            self.update_idletasks()

            try:
                with report.timed(f"{division} coordinate parsing"):
                    coord_stats = parse_coordinates(df)
            except Exception as exc:
                logger.error(f"Coordinate parsing failed for {division} file '{file_name}': {exc}")
                self._set_stage("coordinate_parsing", "fail")
                messagebox.showerror(
                    "Coordinate Parsing Failed", f"Could not parse coordinates in the {division} file.\n\n{exc}"
                )
                self.status_label.configure(text="Ready")
                return

            self._set_stage("coordinate_parsing", "success")

            self._loaded_dfs[division] = df
            self._loaded_file_names[division] = file_name
            self._loaded_file_paths[division] = file_path
            self._coord_stats[division] = coord_stats
            self.file_labels[division].configure(text=file_name, text_color=Color.TEXT_PRIMARY)

            loaded_count = len(self._loaded_dfs)
            total_slots = len(DIVISION_SLOTS)
            self.status_label.configure(
                text=(
                    f"{division}: {coord_stats['valid_count']:,} valid coordinates, "
                    f"{coord_stats['invalid_count']:,} invalid. "
                    f"{loaded_count} of {total_slots} division report(s) loaded."
                )
            )

            if loaded_count == total_slots:
                self.status_label.configure(
                    text=self.status_label.cget("text") + " Ready to run analysis."
                )
                self.run_button.configure(state="normal")
        finally:
            self.browse_buttons[division].configure(state="normal")

    def _on_run_analysis_clicked(self) -> None:
        if len(self._loaded_dfs) != len(DIVISION_SLOTS):
            return

        # The three division files are merged into exactly one DataFrame
        # here -- everything from this point on (save_import, metrics,
        # rule evaluation, automatic send/preview) is completely unchanged
        # and only ever sees this one combined `df`, same as when a single
        # pre-combined file was uploaded before this change.
        df = pd.concat([self._loaded_dfs[d] for d in DIVISION_SLOTS], ignore_index=True)
        file_name = f"Combined ({', '.join(self._loaded_file_names[d] for d in DIVISION_SLOTS)})"

        for button in self.browse_buttons.values():
            button.configure(state="disabled")
        self.run_button.configure(state="disabled")
        try:
            self.status_label.configure(text="Running analysis...")
            self.update_idletasks()

            self._set_stage("data_saved", "active")
            self.status_label.configure(text="Saving to database…")
            self.update_idletasks()

            try:
                stats = save_import(df, file_name)
            except Exception as exc:
                logger.error(f"Failed to save '{file_name}' to database: {exc}")
                self._set_stage("data_saved", "fail")
                messagebox.showerror(
                    "Database Save Failed",
                    f"The file was loaded and validated, but saving it to the database failed.\n\n{exc}",
                )
                self.status_label.configure(text="Ready")
                return

            # This import becomes the active session file, replacing whatever
            # was active before — everything below only ever looks at it.
            import_id = stats["import_id"]
            set_active_import(import_id)

            self._set_stage("data_saved", "success")
            self._set_stage("metrics_calculated", "active")
            self.status_label.configure(text="Calculating movement metrics…")
            self.update_idletasks()

            try:
                metric_stats = calculate_metrics(import_id)
            except Exception as exc:
                logger.error(f"Metrics calculation failed: {exc}")
                self._set_stage("metrics_calculated", "fail")
                messagebox.showerror(
                    "Metrics Calculation Failed",
                    f"The file was imported and saved, but calculating employee movement metrics failed.\n\n{exc}",
                )
                self.status_label.configure(text="Ready")
                return

            self._set_stage("metrics_calculated", "success")
            self._set_stage("validation_clustering", "active")
            self.status_label.configure(text="Evaluating investigation rules…")
            self.update_idletasks()

            try:
                rule_stats = evaluate_same_location(import_id)
                # Independent detector: same stage, its own findings, does not
                # touch Same Location's results.
                hours_stats = evaluate_hours_worked(import_id)
            except Exception as exc:
                logger.error(f"Rule evaluation failed: {exc}")
                self._set_stage("validation_clustering", "fail")
                messagebox.showerror(
                    "Rule Evaluation Failed",
                    f"The file was imported and metrics were calculated, but evaluating investigation rules failed.\n\n{exc}",
                )
                self.status_label.configure(
                    text=(
                        f"{metric_stats['total_visits']:,} visits processed, "
                        f"{metric_stats['total_employees']:,} employees analyzed, "
                        f"average distance {metric_stats['average_distance_km']:.1f} km. "
                        "Rule evaluation failed."
                    )
                )
                return

            self._set_stage("validation_clustering", "success")
            total_findings = rule_stats["findings_count"] + hours_stats["findings_count"]
            summary_text = (
                f"{metric_stats['total_visits']:,} visits processed, "
                f"{metric_stats['total_employees']:,} employees analyzed, "
                f"average distance {metric_stats['average_distance_km']:.1f} km. "
                f"{total_findings:,} finding(s) generated."
            )
            self.status_label.configure(text=summary_text)

            self._start_cloud_push(import_id, dict(self._loaded_file_paths))

            self._loaded_dfs = {}
            self._loaded_file_names = {}
            self._loaded_file_paths = {}
            self._coord_stats = {}
            for division in DIVISION_SLOTS:
                self.file_labels[division].configure(text="No file selected", text_color=Color.TEXT_MUTED)
            self._render_history()
            self._refresh_session_summary()

            # Automatic sending involves real network calls (reverse
            # geocoding + Gmail SMTP) that can take a long time. Analysis
            # itself is already done at this point, so hand the sending off
            # to a background thread instead of blocking the UI — this is
            # what previously made the whole app look frozen/crashed on a
            # real import once automatic sending was enabled.
            if is_automatic_sending_enabled():
                self._start_automatic_send(import_id, summary_text)
            else:
                self._start_preview_only(import_id, summary_text)
        finally:
            for button in self.browse_buttons.values():
                button.configure(state="normal")

    def _start_cloud_push(self, import_id: int, division_file_paths: dict) -> None:
        """Uploads this import's original division files + metadata, its
        findings, and the new active session pointer to the cloud, in the
        background -- never blocks the pipeline on network I/O. No-op in
        Developer Mode (see app/import_sync_service.py)."""

        def worker() -> None:
            try:
                pushed = push_import(import_id, division_file_paths)
                if pushed:
                    sync_findings_for_import(import_id)
                    push_active_session(import_id)
            except Exception as exc:
                logger.error(f"Cloud sync failed for import_id={import_id}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _start_automatic_send(self, import_id: int, base_summary_text: str) -> None:
        self.status_label.configure(text=base_summary_text + " Sending manager emails in the background…")
        start_sending(import_id)

        def report_progress(stage: str, label: str | None = None, completed: int | None = None, total: int | None = None) -> None:
            def update() -> None:
                if self.winfo_exists():
                    self._apply_stage_update(stage, label=label, completed=completed, total=total)

            self.after(0, update)

        def worker() -> None:
            try:
                result = send_all_emails(import_id, progress_callback=report_progress)
            except Exception as exc:
                logger.error(f"Automatic email sending failed: {exc}")
                self.after(0, lambda: self._on_automatic_send_done(import_id, base_summary_text, error=exc))
            else:
                self.after(0, lambda: self._on_automatic_send_done(import_id, base_summary_text, result=result))

        threading.Thread(target=worker, daemon=True).start()

    def _start_preview_only(self, import_id: int, base_summary_text: str) -> None:
        """Automatic Email Sending is OFF -- build the exact same email
        batch (hierarchy resolution, Hospital Suppression, Region
        Suppression, reverse geocoding, draft generation) as a real send
        would, so it shows up in the Email Center for review, but never
        opens an SMTP connection or transmits anything. Still threaded
        (reverse geocoding is a real network call) so the UI doesn't
        block, same as _start_automatic_send."""
        self.status_label.configure(text=base_summary_text + " Generating email previews in the background (Automatic Email Sending is off — nothing will be sent)…")

        def report_progress(stage: str, label: str | None = None, completed: int | None = None, total: int | None = None) -> None:
            def update() -> None:
                if self.winfo_exists():
                    self._apply_stage_update(stage, label=label, completed=completed, total=total)

            self.after(0, update)

        def worker() -> None:
            try:
                result = preview_email_batch(import_id, progress_callback=report_progress)
            except Exception as exc:
                logger.error(f"Email preview generation failed: {exc}")
                self.after(0, lambda: self._on_preview_done(import_id, base_summary_text, error=exc))
            else:
                self.after(0, lambda: self._on_preview_done(import_id, base_summary_text, result=result))

        threading.Thread(target=worker, daemon=True).start()

    def _on_preview_done(
        self, import_id: int, base_summary_text: str, result: dict | None = None, error: Exception | None = None
    ) -> None:
        still_active = get_active_import() is not None and get_active_import().id == import_id
        if still_active:
            if error is not None:
                self._set_stage("finalizing", "fail")
                self.status_label.configure(text=base_summary_text + f" Email preview generation failed: {error}")
            else:
                self._set_stage("finalizing", "success")
                self.status_label.configure(
                    text=(
                        base_summary_text
                        + f" {result['draft_count']:,} email draft(s) generated for review in the Email Center"
                        + (f", {result['unresolved_count']:,} unresolved" if result["unresolved_count"] else "")
                        + (f", {result['skipped_count']:,} master recipient(s) skipped (no data)" if result["skipped_count"] else "")
                        + ". Automatic Email Sending is off — nothing was sent."
                    )
                )
        self._render_history()
        self._refresh_session_summary()

    def _on_automatic_send_done(
        self, import_id: int, base_summary_text: str, result: dict | None = None, error: Exception | None = None
    ) -> None:
        finish_sending()
        # If the user has since imported a different file, this session is no
        # longer active — don't overwrite the newer status message with stale
        # info, but still refresh the history table and KPIs (always current).
        still_active = get_active_import() is not None and get_active_import().id == import_id
        if still_active:
            if error is not None:
                self._set_stage("finalizing", "fail")
                self.status_label.configure(text=base_summary_text + f" Automatic email sending failed: {error}")
            else:
                self._set_stage("finalizing", "success")
                self.status_label.configure(
                    text=(
                        base_summary_text
                        + f" {result['sent_count']:,} manager email(s) sent automatically"
                        + (f", {result['failed_count']:,} failed" if result["failed_count"] else "")
                        + (f", {result['unresolved_count']:,} unresolved" if result["unresolved_count"] else "")
                        + (f", {result['skipped_count']:,} master recipient(s) skipped (no data)" if result["skipped_count"] else "")
                        + "."
                    )
                )
        self._render_history()
        self._refresh_session_summary()
