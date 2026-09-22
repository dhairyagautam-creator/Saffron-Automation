"""Operations page: import the daily call workbook and run the analysis pipeline.

Only one workbook is ever "active" at a time (see app/session_state.py).
Importing a new one replaces it — analysis and findings only ever operate
on the active session file. Previously imported files stay in the database
but don't affect anything shown here. Manager emails are no longer sent
automatically after analysis -- see ui/findings_page.py's "Send Emails"
button, the only place app.notification_service.send_all_emails is called
from.

The Processing Progress Center below replaces a plain "frozen-looking"
status line with a live progress bar per pipeline stage — binary stages
(file selected, columns validated, workbook loaded, ...) jump from 0% to
100%, item-based stages (hospital suppression, reverse geocoding) show a
live "N / M" count as app.notification_service's structured
progress_callback reports each item completing.
"""

import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import pandas as pd
from loguru import logger

from app.config import REQUIRED_COLUMNS
from app.coordinates import parse_coordinates
from app.findings_service import get_summary_counts
from app.metrics import calculate_metrics
from app.path_validator_sync_service import (
    DAILY_REPORT_SLOTS,
    SLOT_LABELS,
    apply_pending_updates,
    check_for_updates,
    hierarchy_gate_open,
    upload_and_sync,
)
from app.path_validator_upload_service import get_slot_state as get_pv_slot_state, slot_id_for as pv_slot_id_for
from app.session_state import get_active_import, set_active_import
from app.timing import get_current_report, start_new_report
from database.connection import get_session, to_local
from database.import_service import save_import
from database.models import EmailNotification, ImportHistory
from rules.hours_worked import evaluate as evaluate_hours_worked
from rules.same_location import evaluate as evaluate_same_location
from ui.background_task import run_in_background
from ui.components import (
    Card,
    EmptyState,
    KPICard,
    PrimaryButton,
    SecondaryButton,
    SectionHeader,
    StatusBadge,
    styled_treeview,
)
from ui.icons import get_icon
from app.table_export_service import default_export_filename, export_rows_with_ui
from ui.theme import Color, Font, Radius, Spacing

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

# Each stage: (key, display label). Every stage here is binary -- jumps
# 0% -> 100% (success) or shows an error tint (fail); there is no more
# item-based "N / M" stage now that email generation/sending happen from
# ui/findings_page.py's own Send Emails button, not from this pipeline.
PIPELINE_STAGES = [
    ("file_selected", "File Selected"),
    ("columns_validated", "Columns Validated"),
    ("workbook_loading", "Loading Workbook"),
    ("coordinate_parsing", "Parsing Employees"),
    ("data_saved", "Saving Data"),
    ("metrics_calculated", "Calculating Metrics"),
    ("validation_clustering", "Validating Calls"),
    ("finalizing", "Finalizing"),
]

# The daily call export comes as one file per division -- each with
# identical columns and its own correct "Division" value already in every
# row (confirmed against real Onyx/Guardians/Xandra exports). All three
# must be loaded before Run Analysis enables; they're concatenated into
# one DataFrame right before the existing, unchanged save/metrics/rule
# pipeline runs -- see _on_run_analysis_clicked.
DIVISION_SLOTS = ["Xandra", "Onyx", "Guardians"]


class ProgressRow(ctk.CTkFrame):
    """One pipeline stage: a label, a determinate progress bar, and a
    trailing status. `state` is "pending"/"active"/"success"/"fail"."""

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


class OperationsPage(ctk.CTkFrame):
    """Select a daily workbook, validate/prepare it, then run the full
    save + metrics + rule-evaluation pipeline against it as the active
    session file."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)

        self._stages = list(PIPELINE_STAGES)

        self.progress_rows: dict[str, ProgressRow] = {}
        self.session_cards: dict[str, KPICard] = {}
        self.browse_buttons: dict[str, PrimaryButton] = {}
        self.file_labels: dict[str, ctk.CTkLabel] = {}
        self._loaded_dfs: dict[str, pd.DataFrame] = {}
        self._loaded_file_names: dict[str, str] = {}
        self._loaded_file_paths: dict[str, str] = {}
        self._coord_stats: dict[str, dict] = {}
        self._current_display_rows: list[dict] = []
        self._checking = False
        self._banner_dismissed = False

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

        # --- Sync (see app/path_validator_sync_service.py) --------------------
        sync_row = ctk.CTkFrame(outer, fg_color="transparent")
        sync_row.pack(fill="x", pady=(0, Spacing.SM))
        self._sync_status_label = ctk.CTkLabel(
            sync_row, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w"
        )
        self._sync_status_label.pack(side="left")
        self._refresh_button = SecondaryButton(
            sync_row, text="Refresh", image=get_icon("refresh", size=14, color=Color.PRIMARY),
            command=self._run_check,
        )
        self._refresh_button.pack(side="right")

        self._banner_container = ctk.CTkFrame(outer, fg_color="transparent")
        self._banner_container.pack(fill="x", pady=(0, Spacing.SM))

        # Persistent, visible block message when the hard hierarchy gate
        # (see app/path_validator_sync_service.py::hierarchy_gate_open) is
        # closed -- NOT just a silently-disabled Run Analysis button. A
        # missing/empty employee_hierarchy_path_validator makes
        # rules/same_location.py silently produce zero findings for
        # everyone; this must be impossible to miss.
        self._gate_banner = ctk.CTkLabel(
            outer,
            text="",
            font=Font.SMALL_BOLD,
            text_color=Color.ERROR,
            anchor="w",
            wraplength=760,
            justify="left",
            fg_color=Color.ERROR_SOFT,
            corner_radius=Radius.SM,
        )
        self._gate_banner.pack(fill="x", pady=(0, Spacing.SM))

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

        for key, label in self._stages:
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
        from local SQLite.

        Deliberately does NOT call _run_check() (the sync check) -- by
        explicit design, checking the manifest only ever happens via an
        explicit click on the "Refresh" button below, never automatically
        on page visit/navigation. This also sidesteps a real incident this
        page hit during development: _build_widgets() itself unconditionally
        calls this method once, from inside __init__, and
        ui/path_validator_module.py eagerly constructs every one of its
        pages during ui/main_window.py's own MainWindow.__init__() --
        before main.py's mainloop() has even started. A network-touching
        call from here (even deferred via after_idle/after(0, ...), which
        does NOT actually wait for mainloop -- customtkinter's own widget
        construction calls update_idletasks() internally in several
        places, flushing queued idle/after callbacks immediately,
        synchronously, mid-construction) delayed the whole window from
        appearing at all on a slow network, confirmed with a real
        timestamped trace."""
        self._render_history()
        self._refresh_session_summary()
        self._update_gate_banner()
        self.refresh_from_state()

    def _update_gate_banner(self) -> None:
        # Cleared (not unpacked) when open -- an empty CTkLabel collapses
        # to negligible height, and toggling with pack()/pack_forget()
        # here would fight this widget's own fixed position among outer's
        # other packed children (established once in _build_widgets).
        if hierarchy_gate_open():
            self._gate_banner.configure(text="")
        else:
            self._gate_banner.configure(
                text=(
                    "⚠ Organization Data hierarchy is empty — Run Analysis is blocked until Path "
                    "Validator's Organization Data workbooks are connected and refreshed (or synced)."
                )
            )

    def refresh_from_state(self) -> None:
        """Renders every division's CURRENTLY PERSISTED filename -- same
        pull-to-render-parity pattern as every other sync-enabled upload
        page: a pulled file renders identically to a locally uploaded one.
        Never touches self._loaded_dfs or the Run Analysis button -- a
        sync pull auto-runs analysis itself once all 3 divisions' current
        files are retained and the hierarchy gate is open (see
        app/path_validator_sync_service.py); it never substitutes for a
        manual local upload's own in-memory state."""
        for division in DIVISION_SLOTS:
            state = get_pv_slot_state(division)
            if state["uploaded"]:
                self.file_labels[division].configure(text=state["filename"], text_color=Color.TEXT_PRIMARY)

    def _reset_progress(self) -> None:
        for row in self.progress_rows.values():
            row.reset()

    def _set_stage(self, key: str, state: str) -> None:
        """Directly set a binary stage's state — used for the pre-analysis
        (browse-time) and analysis-time stages that aren't driven by
        notification_service's progress_callback."""
        self.progress_rows[key].set_binary(state)

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
                    "imported_at": to_local(row.imported_at).strftime("%Y-%m-%d %H:%M"),
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

            # Retain the source file locally and push it to the sync
            # manifest -- see app/path_validator_sync_service.py.
            # upload_and_sync re-validates internally (harmless, same
            # file) and never auto-runs analysis on a LOCAL upload -- Run
            # Analysis below stays the only trigger for this machine's own
            # upload, exactly as before.
            sync_result = upload_and_sync(pv_slot_id_for(division), file_path)
            if not sync_result.get("synced"):
                messagebox.showwarning("Not Synced", sync_result.get("sync_error") or "This file was not synced to the cloud.")

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

        # Hard gate (see app/path_validator_sync_service.py::hierarchy_gate_open):
        # a missing/empty employee_hierarchy_path_validator makes
        # rules/same_location.py silently produce zero findings for every
        # employee -- checked here too (not just via the persistent
        # banner) so a stale banner state can never let this slip through.
        if not hierarchy_gate_open():
            messagebox.showerror(
                "Organization Data Required",
                "Run Analysis is blocked: Path Validator's Organization Data hierarchy has no "
                "employees loaded yet. Without it, Same Location analysis would silently produce "
                "zero findings for everyone.\n\n"
                "Connect and refresh (or sync) Path Validator's Organization Data workbooks first.",
            )
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
            self._set_stage("finalizing", "success")
            total_findings = rule_stats["findings_count"] + hours_stats["findings_count"]
            summary_text = (
                f"{metric_stats['total_visits']:,} visits processed, "
                f"{metric_stats['total_employees']:,} employees analyzed, "
                f"average distance {metric_stats['average_distance_km']:.1f} km. "
                f"{total_findings:,} finding(s) generated. Use Send Emails on the Findings page to notify managers."
            )
            self.status_label.configure(text=summary_text)

            self._loaded_dfs = {}
            self._loaded_file_names = {}
            self._loaded_file_paths = {}
            self._coord_stats = {}
            for division in DIVISION_SLOTS:
                self.file_labels[division].configure(text="No file selected", text_color=Color.TEXT_MUTED)
            self._render_history()
            self._refresh_session_summary()
        finally:
            for button in self.browse_buttons.values():
                button.configure(state="normal")

    # --- Sync check (banner) -- see app/path_validator_sync_service.py --
    # covers this page's 3 daily-report slots; the other 3 (hierarchy) are
    # ui/organization_data_page.py's own banner, sharing the same
    # underlying check_for_updates()/apply_pending_updates() pair.

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
                logger.error(f"Path Validator sync: check raised unexpectedly: {error!r}")
                result = {"ok": False, "reason": "error"}

            self._banner_dismissed = False
            self._render_sync_status(result)
            self._render_banner(result)
            if result.get("ok"):
                self.refresh_from_state()
                self._update_gate_banner()
                self._refresh_session_summary()
                self._render_history()

        run_in_background(self, work, on_done=on_done)

    def _render_sync_status(self, result: dict) -> None:
        if not self._sync_status_label.winfo_exists():
            return
        if not result.get("ok"):
            reason = "Could not reach Supabase" if result.get("reason") == "offline" else "Last check failed"
            self._sync_status_label.configure(text=f"⚠ {reason} -- showing this machine's last known state.")
            return
        page_changed = [c for c in (result.get("changed") or []) if c["slot_id"] in DAILY_REPORT_SLOTS]
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
        changed = [c for c in (result.get("changed") or []) if c["slot_id"] in DAILY_REPORT_SLOTS]
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
                logger.error(f"Path Validator sync: apply raised unexpectedly: {error!r}")
                messagebox.showerror("Sync Failed", f"Could not apply updates.\n\n{error}")
                self._run_check()
                return

            self.refresh_from_state()
            self._update_gate_banner()
            self._refresh_session_summary()
            self._render_history()

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
