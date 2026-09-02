"""Work Distribution Employee Details page: Employee Information, Summary
Statistics, and Doctor List for whichever employee Findings' "View
Employee Details" last selected (see ui/work_distribution_module.py's
`_on_view_employee_details`). `load_employee()` hands over the Findings row
already on screen (employee_name/designation/division/KPI numbers/status/
reason -- no re-lookup needed for those) and separately queries
app.work_distribution_service.get_employee_doctors() for that employee's
real doctor list.

No Email field -- the uploaded report carries none for a BM/ABM, so
nothing here is fabricated to fill an "Employee Information" card that
used to show placeholder values in the UI-only phase. Employee Code IS
now available (2026-08 BM/ABM Code fix) and is used internally to fetch
the right doctor list (see get_employee_doctors's own real identity, not
name, argument) -- it is not itself added as a new displayed field here,
that being outside this fix's own scope.

Version 2.1 architecture update: a TabBar (see ui.components.TabBar) now
sits above the content -- "RGD Coverage" is this exact page, UNCHANGED.

Phase 2/3 (ABM + RBM engines): "Manager Work Allocation" shows the
selected manager's own Employee Information / Summary Statistics / BM
Monthly Trend. Within this ONE tab, `_refresh_mwa` dispatches on the
finding row's own `designation` field ("ABM" or "RBM", already present on
every Manager Work Allocation finding -- no separate sub-engine marker
needed).

Redesigned 2026-08-05 for the rolling six-month history architecture: the
BM list is no longer a single Joint-Days number -- it's a full month-by-
month trend table (BM Name, one column per month CURRENTLY retained in
the rolling window, then Average/Total, then Status), built dynamically
from app.manager_work_allocation_service.get_employee_bm_monthly_history()
(ABM) / app.manager_work_allocation_rbm_service.get_employee_bm_monthly_history()
(RBM) -- NOT a fixed column tuple, since which months are in the window
changes over time (see those functions' own docstrings for the exact
shape). A BM with no record for a given retained month shows a blank cell
for that month rather than a zero, per the module's own spec ("collect
every monthly record" -- never invented). A month that DOES have a
    record but is excluded by that BM's own DOJ (2026-08 presentation fix)
    shows the literal text NOT_YET_JOINED_LABEL instead -- both functions'
    "not_yet_joined_months" set already names exactly which months those
    are (see their own docstrings for why that's a separate key from
    "monthly"); this page just checks membership in that set, no separate
    DOJ check of its own.

`load_employee(row)` routes on `row.get("_engine")` -- "mwa" (stamped by
ui/work_distribution_findings_page.py's own `_on_mwa_view_details_clicked`
/`_on_rbm_view_details_clicked` just before handing the row over) selects
the Manager Work Allocation tab and renders the BM breakdown (ABM or RBM,
per the row's own designation); anything else (a plain RGD Coverage row,
which never carries this key) keeps the exact previous behavior and
selects the RGD Coverage tab. Either path also switches `self.tabs` to
match, so navigating here from any Findings tab always lands on the
matching Employee Details tab, not whichever tab happened to be showing
before.
"""

import customtkinter as ctk

from app.doj_eligibility_service import NOT_YET_JOINED_LABEL
from app.manager_work_allocation_rbm_service import (
    get_employee_bm_monthly_history as get_rbm_employee_bm_monthly_history,
)
from app.manager_work_allocation_service import (
    get_employee_bm_monthly_history,
)
from app.table_export_service import default_export_filename, export_rows_with_ui
from app.work_distribution_service import get_employee_doctors
from ui.components import Card, EmptyState, KPICard, PrimaryButton, SectionHeader, StatusBadge, TabBar, styled_treeview
from ui.icons import get_icon
from ui.theme import Color, Font, Spacing

DOCTOR_LIST_COLUMNS = ("doctor_code", "doctor_name", "division", "city", "visit_count", "status")
DOCTOR_LIST_HEADINGS = {
    "doctor_code": "Doctor Code",
    "doctor_name": "Doctor Name",
    "division": "Division",
    "city": "City",
    "visit_count": "Visit Count",
    "status": "Status",
}
DOCTOR_LIST_WIDTHS = {
    "doctor_code": 100,
    "doctor_name": 180,
    "division": 100,
    "city": 120,
    "visit_count": 90,
    "status": 190,
}

MONTH_COLUMN_WIDTH = 90
BM_NAME_COLUMN_WIDTH = 180
SUMMARY_COLUMN_WIDTH = 90
STATUS_COLUMN_WIDTH = 90

STATUS_BADGE_KIND = {"Healthy": "success", "Flagged": "warning", "Pass": "success"}
DOCTOR_STATUS_TAG = {"Not Visited": "not_visited"}
BM_STATUS_TAG = {"Fail": "not_visited"}
RBM_BM_STATUS_TAG = {"No": "not_visited"}


class WorkDistributionEmployeeDetailsPage(ctk.CTkFrame):
    """Detail page for a single employee's doctor coverage."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._employee: dict | None = None
        self._mwa_employee: dict | None = None
        self._doctor_list_rows: list[dict] = []
        self._monthly_trend_export: dict | None = None
        self._build_widgets()

    def on_show(self) -> None:
        pass

    def load_employee(self, row: dict) -> None:
        """Called by the module shell when Findings hands over a selected
        row. Routes on `row.get("_engine")` -- see module docstring."""
        if row.get("_engine") == "mwa":
            self._mwa_employee = row
            self._refresh_mwa()
            if self.tabs.active_tab != "Manager Work Allocation":
                self.tabs.select("Manager Work Allocation")
        else:
            self._employee = row
            self._refresh()
            if self.tabs.active_tab != "RGD Coverage":
                self.tabs.select("RGD Coverage")

    def _build_widgets(self) -> None:
        self.outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            self.outer, "Employee Details", "Doctor coverage detail for a selected employee"
        ).pack(anchor="w", pady=(0, Spacing.LG))

        self.tabs = TabBar(self.outer, ["RGD Coverage", "Manager Work Allocation"], on_change=self._on_tab_changed)
        self.tabs.pack(fill="x", pady=(0, Spacing.MD))

        self.rgd_frame = ctk.CTkFrame(self.outer, fg_color="transparent")
        self.mwa_frame = ctk.CTkFrame(self.outer, fg_color="transparent")

        self.rgd_frame.pack(fill="both", expand=True)

        self.content = ctk.CTkFrame(self.rgd_frame, fg_color="transparent")
        self.content.pack(fill="both", expand=True)
        self._render_empty()

        self.mwa_content = ctk.CTkFrame(self.mwa_frame, fg_color="transparent")
        self.mwa_content.pack(fill="both", expand=True)
        self._render_mwa_empty()

    def _on_tab_changed(self, tab_name: str) -> None:
        if tab_name == "RGD Coverage":
            self.mwa_frame.pack_forget()
            self.rgd_frame.pack(fill="both", expand=True)
        else:
            self.rgd_frame.pack_forget()
            self.mwa_frame.pack(fill="both", expand=True)

    def _render_empty(self) -> None:
        for widget in self.content.winfo_children():
            widget.destroy()
        EmptyState(
            self.content, "Select an employee from Findings and click \"View Employee Details\" to see their coverage."
        ).pack(fill="both", expand=True)

    def _refresh(self) -> None:
        for widget in self.content.winfo_children():
            widget.destroy()

        if self._employee is None:
            self._render_empty()
            return

        employee = self._employee
        self._build_employee_info_card(self.content, employee)
        self._build_summary_statistics(self.content, employee)
        self._build_doctor_list_card(self.content, employee)

    # --- Manager Work Allocation (ABM) --------------------------------------

    def _render_mwa_empty(self) -> None:
        for widget in self.mwa_content.winfo_children():
            widget.destroy()
        EmptyState(
            self.mwa_content,
            "Select an ABM from Findings and click \"View Employee Details\" to see their BMs.",
        ).pack(fill="both", expand=True)

    def _refresh_mwa(self) -> None:
        for widget in self.mwa_content.winfo_children():
            widget.destroy()

        if self._mwa_employee is None:
            self._render_mwa_empty()
            return

        employee = self._mwa_employee
        if employee.get("designation") == "RBM":
            self._build_rbm_info_card(self.mwa_content, employee)
            self._build_rbm_summary_statistics(self.mwa_content, employee)
            self._build_rbm_bm_list_card(self.mwa_content, employee)
        else:
            self._build_mwa_info_card(self.mwa_content, employee)
            self._build_mwa_summary_statistics(self.mwa_content, employee)
            self._build_mwa_bm_list_card(self.mwa_content, employee)

    def _build_mwa_info_card(self, outer, employee: dict) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(0, Spacing.LG))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Employee Information", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        self._info_row(body, "Employee", employee["employee_name"])
        self._info_row(body, "Division", employee["division"] or "—")

        status_row = ctk.CTkFrame(body, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, Spacing.SM))
        ctk.CTkLabel(
            status_row, text="Status", font=Font.BODY, text_color=Color.TEXT_SECONDARY, anchor="w", width=140
        ).pack(side="left")
        StatusBadge(status_row, employee["status"], STATUS_BADGE_KIND.get(employee["status"], "neutral")).pack(
            side="left"
        )

    def _build_mwa_summary_statistics(self, outer, employee: dict) -> None:
        ctk.CTkLabel(
            outer, text="Summary Statistics", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        specs = [
            ("Total BMs", f"{employee['total_bms']:,}", Color.PRIMARY),
            ("Passed BMs", f"{employee['passed_bms']:,}", Color.SUCCESS),
            ("Failed BMs", f"{employee['failed_bms']:,}", Color.ERROR),
        ]

        kpi_grid = ctk.CTkFrame(outer, fg_color="transparent")
        kpi_grid.pack(fill="x", pady=(0, Spacing.LG))
        for i in range(len(specs)):
            kpi_grid.grid_columnconfigure(i, weight=1)
        for i, (label, value, accent) in enumerate(specs):
            card = KPICard(kpi_grid, label=label, value=value, accent=accent)
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else Spacing.SM, 0))

    def _build_mwa_bm_list_card(self, outer, employee: dict) -> None:
        history = get_employee_bm_monthly_history(employee["employee_name"])
        self._build_monthly_trend_card(
            outer, title="BM Monthly Trend", history=history, summary_heading="Average",
            status_heading="Status", status_tag_map=BM_STATUS_TAG, empty_message="No BMs found for this ABM.",
        )

    # --- Manager Work Allocation (RBM) --------------------------------------

    def _build_rbm_info_card(self, outer, employee: dict) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(0, Spacing.LG))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Employee Information", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        self._info_row(body, "Employee", employee["employee_name"])
        self._info_row(body, "Division", employee["division"] or "—")

        status_row = ctk.CTkFrame(body, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, Spacing.SM))
        ctk.CTkLabel(
            status_row, text="Status", font=Font.BODY, text_color=Color.TEXT_SECONDARY, anchor="w", width=140
        ).pack(side="left")
        StatusBadge(status_row, employee["status"], STATUS_BADGE_KIND.get(employee["status"], "neutral")).pack(
            side="left"
        )

        self._info_row(body, "Reason", employee.get("reason", ""))

    def _build_rbm_summary_statistics(self, outer, employee: dict) -> None:
        ctk.CTkLabel(
            outer, text="Summary Statistics", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        specs = [
            ("Total Unique BMs", f"{employee['total_bms']:,}", Color.PRIMARY),
            ("Covered BMs", f"{employee['passed_bms']:,}", Color.SUCCESS),
            ("Missed BMs", f"{employee['failed_bms']:,}", Color.ERROR),
            ("Coverage %", str(employee.get("coverage_percent", "0.0%")), Color.INFO),
        ]

        kpi_grid = ctk.CTkFrame(outer, fg_color="transparent")
        kpi_grid.pack(fill="x", pady=(0, Spacing.LG))
        for i in range(len(specs)):
            kpi_grid.grid_columnconfigure(i, weight=1)
        for i, (label, value, accent) in enumerate(specs):
            card = KPICard(kpi_grid, label=label, value=value, accent=accent)
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else Spacing.SM, 0))

    def _build_rbm_bm_list_card(self, outer, employee: dict) -> None:
        history = get_rbm_employee_bm_monthly_history(employee["employee_name"])
        self._build_monthly_trend_card(
            outer, title="BM Monthly Trend", history=history, summary_heading="Average",
            status_heading="Covered", status_tag_map=RBM_BM_STATUS_TAG, empty_message="No BMs found for this RBM.",
        )

    # --- Shared monthly-trend table (both engines) --------------------------

    def _build_monthly_trend_card(
        self, outer, title: str, history: dict, summary_heading: str, status_heading: str,
        status_tag_map: dict, empty_message: str,
    ) -> None:
        """Builds a DYNAMIC-column month-by-month trend table -- BM Name,
        one column per month currently retained in the rolling window (see
        app.manager_work_allocation_service/_rbm_service's own
        get_employee_bm_monthly_history), then a summary column (Average
        for ABM, Total for RBM) and a status column. Columns are built
        fresh from `history["months"]` every render, never a fixed tuple,
        since which months are in the window changes over time. A BM with
        no record for a given retained month shows a blank cell for that
        month, never an invented zero. A month that DOES have a record but
        that BM's own DOJ excludes (per `bm["not_yet_joined_months"]`,
        2026-08 presentation fix) shows NOT_YET_JOINED_LABEL instead of a
        blank cell."""
        card = Card(outer)
        card.pack(fill="both", expand=True)

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(body, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.SM))

        ctk.CTkLabel(
            header_row, text=title, font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(side="left")

        self.monthly_trend_export_button = PrimaryButton(
            header_row,
            text="Export",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_monthly_trend_export_clicked,
        )
        self.monthly_trend_export_button.pack(side="right")

        months = history["months"]
        bms = history["bms"]
        if not bms:
            self._monthly_trend_export = None
            EmptyState(body, empty_message).pack(fill="both", expand=True)
            return

        columns = ("subordinate_name", *months, "average", "status")
        headings = {"subordinate_name": "BM Name", "average": summary_heading, "status": status_heading}
        headings.update({m: m for m in months})
        widths = {"subordinate_name": BM_NAME_COLUMN_WIDTH, "average": SUMMARY_COLUMN_WIDTH, "status": STATUS_COLUMN_WIDTH}
        widths.update({m: MONTH_COLUMN_WIDTH for m in months})

        tree = styled_treeview(body, columns, headings, widths, height=12)
        tree.tag_configure("not_visited", background=Color.WARNING_SOFT)
        export_rows = []
        for bm in bms:
            tag = status_tag_map.get(bm["status"])
            not_yet_joined_months = bm.get("not_yet_joined_months", ())
            row_values = (
                bm["subordinate_name"],
                *(
                    NOT_YET_JOINED_LABEL if m in not_yet_joined_months else bm["monthly"].get(m, "")
                    for m in months
                ),
                bm["average"],
                bm["status"],
            )
            tree.insert("", "end", tags=(tag,) if tag else (), values=row_values)
            export_rows.append(dict(zip(columns, row_values)))
        tree.pack(fill="both", expand=True)

        # Stored for _on_monthly_trend_export_clicked -- exactly the rows/
        # columns/headings just rendered above, including the dynamic
        # per-month columns (which vary with the current rolling window)
        # and the engine-specific summary/status headings (Average/Status
        # for ABM, Average/Covered for RBM).
        self._monthly_trend_export = {"rows": export_rows, "columns": columns, "headings": headings}

    def _on_monthly_trend_export_clicked(self) -> None:
        if not self._monthly_trend_export:
            return
        manager = self._mwa_employee or {}
        export_rows_with_ui(
            self,
            rows=self._monthly_trend_export["rows"],
            columns=self._monthly_trend_export["columns"],
            headings=self._monthly_trend_export["headings"],
            suggested_filename=default_export_filename(
                "BMMonthlyTrend", suffix=manager.get("employee_name", "")
            ),
            sheet_title="BM Monthly Trend",
        )

    # --- Employee Information --------------------------------------------

    def _build_employee_info_card(self, outer, employee: dict) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(0, Spacing.LG))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Employee Information", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        self._info_row(body, "Employee", employee["employee_name"])
        self._info_row(body, "Designation", employee["designation"])
        self._info_row(body, "Division", employee["division"] or "—")

        status_row = ctk.CTkFrame(body, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, Spacing.SM))
        ctk.CTkLabel(
            status_row, text="Status", font=Font.BODY, text_color=Color.TEXT_SECONDARY, anchor="w", width=140
        ).pack(side="left")
        StatusBadge(status_row, employee["status"], STATUS_BADGE_KIND.get(employee["status"], "neutral")).pack(
            side="left"
        )

        self._info_row(body, "Reason", employee["reason"])

    def _info_row(self, parent, label: str, value: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, Spacing.SM))
        ctk.CTkLabel(
            row, text=label, font=Font.BODY, text_color=Color.TEXT_SECONDARY, anchor="w", width=140
        ).pack(side="left")
        ctk.CTkLabel(
            row, text=str(value), font=Font.BODY, text_color=Color.TEXT_PRIMARY, anchor="w",
            wraplength=500, justify="left",
        ).pack(side="left", fill="x", expand=True)

    # --- Summary Statistics ------------------------------------------------

    def _build_summary_statistics(self, outer, employee: dict) -> None:
        ctk.CTkLabel(
            outer, text="Summary Statistics", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        specs = [
            ("Total Doctors", f"{employee['total_doctors']:,}", Color.PRIMARY),
            ("Total Calls", f"{employee['total_calls']:,}", Color.INFO),
            ("Missed Doctors", f"{employee['missed_doctors']:,}", Color.WARNING),
            ("Doctors with <2 Visits", f"{employee['poor_coverage_doctors']:,}", Color.ERROR),
        ]

        kpi_grid = ctk.CTkFrame(outer, fg_color="transparent")
        kpi_grid.pack(fill="x", pady=(0, Spacing.LG))
        for i in range(len(specs)):
            kpi_grid.grid_columnconfigure(i, weight=1)
        for i, (label, value, accent) in enumerate(specs):
            card = KPICard(kpi_grid, label=label, value=value, accent=accent)
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else Spacing.SM, 0))

    # --- Doctor List ---------------------------------------------------

    def _build_doctor_list_card(self, outer, employee: dict) -> None:
        card = Card(outer)
        card.pack(fill="both", expand=True)

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(body, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.SM))

        ctk.CTkLabel(
            header_row, text="Doctor List", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(side="left")

        self.doctor_list_export_button = PrimaryButton(
            header_row,
            text="Export",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_doctor_list_export_clicked,
        )
        self.doctor_list_export_button.pack(side="right")

        doctors = get_employee_doctors(employee["employee_code"], employee["designation"])
        self._doctor_list_rows = doctors
        if not doctors:
            EmptyState(body, "No doctors found for this employee.").pack(fill="both", expand=True)
            return

        tree = styled_treeview(body, DOCTOR_LIST_COLUMNS, DOCTOR_LIST_HEADINGS, DOCTOR_LIST_WIDTHS, height=12)
        tree.tag_configure("not_visited", background=Color.WARNING_SOFT)
        for doctor in doctors:
            tag = DOCTOR_STATUS_TAG.get(doctor["status"])
            tree.insert(
                "", "end",
                tags=(tag,) if tag else (),
                values=tuple(doctor[col] for col in DOCTOR_LIST_COLUMNS),
            )
        tree.pack(fill="both", expand=True)

    def _on_doctor_list_export_clicked(self) -> None:
        """Exports `self._doctor_list_rows` -- exactly the rows
        `_build_doctor_list_card` just rendered into the Treeview for the
        currently selected employee (no independent search/filter exists
        on this list today, so "currently shown" and "the full list" are
        the same set)."""
        employee_name = self._employee["employee_name"] if self._employee else ""
        export_rows_with_ui(
            self,
            rows=self._doctor_list_rows,
            columns=DOCTOR_LIST_COLUMNS,
            headings=DOCTOR_LIST_HEADINGS,
            suggested_filename=default_export_filename("DoctorList", suffix=employee_name),
            sheet_title="Doctor List",
        )
