"""Work Distribution Findings page: search/filter over the real, generated
WorkDistributionFinding table (see app/work_distribution_service.py).
Reloads from the database every time this page becomes visible (on_show),
matching the same convention every other Findings-style page in this app
already uses (see ui/inventory_replenishment_page.py).

Selecting a row enables the "View Employee Details" button below the table
(no in-row action column -- the table stays as clean as every other
Findings-style table in the app); clicking it is pure navigation (handed
to the module shell via `on_view_details`) -- the module shell then calls
app.work_distribution_service.get_employee_doctors() to populate the
Employee Details page, this page never looks that up itself.

Export (Version 2.0's Table Export Framework, see
app.table_export_service's own module docstring) exports
`self._current_display_rows` -- the EXACT list `_render_table()` just used
to populate the Treeview, post status-filter and search -- never
re-queried or independently re-filtered here, same contract every other
export button in the app follows (see ui/inventory_replenishment_page.py's
own Export button, the reference implementation).

Version 2.1 architecture update: this single page now serves BOTH of the
module's business processes via a TabBar (see ui.components.TabBar) rather
than a second Findings page -- "RGD Coverage" is this exact table,
UNCHANGED.

Phase 2/3 (ABM + RBM engines): "Manager Work Allocation" is now a real tab
with its OWN nested TabBar ("ABM" / "RBM") -- each showing an independent
table over its own engine's ManagerWorkAllocationFinding rows
(app.manager_work_allocation_service for ABM,
app.manager_work_allocation_rbm_service for RBM, each already scoped by
designation so neither ever shows the other's rows -- see each service
module's own docstring). Both have their own status filter ("All"/"Pass"/
"Flagged"), search, Export (same app.table_export_service framework,
filter-aware, exactly like RGD's own Export), and "View Employee Details"
wiring. Each displayed row carries `_engine: "mwa"` so the module shell's
shared `_on_view_employee_details` callback and the Employee Details
page's own `load_employee()` can tell which engine's row this is without a
second callback -- RGD rows carry no such key. The row's own
`designation` field ("ABM" or "RBM", already present on every Manager Work
Allocation finding) is what the Employee Details page uses to pick between
the ABM detail view and the RBM detail view within that tab -- no separate
sub-engine marker needed beyond what the finding already carries."""

import customtkinter as ctk

from app.manager_work_allocation_rbm_service import (
    FINDINGS_COLUMNS as RBM_COLUMNS,
    FINDINGS_HEADINGS as RBM_HEADINGS,
    get_all_findings as get_all_rbm_findings,
    get_current_cycle_label as get_rbm_cycle_label,
    has_data as rbm_has_data,
)
from app.manager_work_allocation_service import (
    FINDINGS_COLUMNS as MWA_COLUMNS,
    FINDINGS_HEADINGS as MWA_HEADINGS,
    get_all_findings as get_all_mwa_findings,
    get_current_cycle_label as get_mwa_cycle_label,
    has_data as mwa_has_data,
)
from app.table_export_service import default_export_filename, export_rows_with_ui
from app.work_distribution_service import (
    FINDINGS_COLUMNS as COLUMNS,
    FINDINGS_HEADINGS as HEADINGS,
    get_all_findings,
    has_data,
)
from ui.components import Card, EmptyState, PrimaryButton, SecondaryButton, SectionHeader, TabBar, styled_treeview
from ui.icons import get_icon
from ui.theme import Color, Font, Spacing

WIDTHS = {
    "employee_name": 150,
    "designation": 90,
    "division": 100,
    "total_doctors": 100,
    "total_calls": 90,
    "missed_doctors": 100,
    "poor_coverage_doctors": 130,
    "status": 90,
    "reason": 320,
}

MWA_WIDTHS = {
    "employee_name": 180,
    "division": 120,
    "total_bms": 100,
    "passed_bms": 100,
    "failed_bms": 100,
    "status": 100,
}

RBM_WIDTHS = {
    "employee_name": 180,
    "division": 120,
    "total_bms": 110,
    "passed_bms": 100,
    "failed_bms": 100,
    "coverage_percent": 100,
    "status": 100,
    "reason": 260,
}


class WorkDistributionFindingsPage(ctk.CTkFrame):
    """Search/filter over the real WorkDistributionFinding table."""

    def __init__(self, master, on_view_details=None) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._on_view_details = on_view_details
        self._status_filter = "All"
        self._selected_row: dict | None = None
        self._all_rows: list[dict] = []
        self._current_display_rows: list[dict] = []

        self._mwa_status_filter = "All"
        self._mwa_selected_row: dict | None = None
        self._mwa_all_rows: list[dict] = []
        self._mwa_current_display_rows: list[dict] = []

        self._rbm_status_filter = "All"
        self._rbm_selected_row: dict | None = None
        self._rbm_all_rows: list[dict] = []
        self._rbm_current_display_rows: list[dict] = []

        self._build_widgets()

    def on_show(self) -> None:
        self._all_rows = get_all_findings()
        self._render_table()
        self._mwa_all_rows = get_all_mwa_findings()
        self._render_mwa_table()
        self._rbm_all_rows = get_all_rbm_findings()
        self._render_rbm_table()

    def _build_widgets(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Findings", "Review doctor coverage findings for BMs and ABMs"
        ).pack(anchor="w", pady=(0, Spacing.LG))

        # TabBar (left) + a single shared "View Employee Details" button slot
        # (right) on the SAME line -- exactly one of the three buttons built
        # below (RGD / MWA-ABM / MWA-RBM) is ever packed into
        # view_details_button_container at a time, kept in sync with the
        # active tab/sub-tab by _sync_view_details_button_visibility(). This
        # replaces each section's own previous placement (below its table,
        # then briefly next to its own Export button) so the button sits in
        # one consistent, always-visible spot regardless of table height,
        # identical for both sections. TabBar is packed side="left" here
        # (not fill="x") specifically so it only claims its own natural
        # width, leaving room for the button container on the right.
        top_row = ctk.CTkFrame(outer, fg_color="transparent")
        top_row.pack(fill="x", pady=(0, Spacing.MD))

        self.tabs = TabBar(top_row, ["RGD Coverage", "Manager Work Allocation"], on_change=self._on_tab_changed)
        self.tabs.pack(side="left")

        self.view_details_button_container = ctk.CTkFrame(top_row, fg_color="transparent")
        self.view_details_button_container.pack(side="right")

        self.rgd_frame = ctk.CTkFrame(outer, fg_color="transparent")
        self.mwa_frame = ctk.CTkFrame(outer, fg_color="transparent")

        self.rgd_frame.pack(fill="both", expand=True)

        table_card = Card(self.rgd_frame)
        table_card.pack(fill="both", expand=True)

        table_body = ctk.CTkFrame(table_card, fg_color="transparent")
        table_body.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)

        filter_row = ctk.CTkFrame(table_body, fg_color="transparent")
        filter_row.pack(fill="x", pady=(0, Spacing.SM))

        self.export_button = PrimaryButton(
            filter_row,
            text="Export",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_export_clicked,
        )
        self.export_button.pack(side="right")

        ctk.CTkLabel(filter_row, text="Filter:", font=Font.BODY, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(0, Spacing.SM)
        )
        self.status_menu = ctk.CTkOptionMenu(
            filter_row,
            values=["All", "Healthy", "Flagged"],
            command=self._on_status_filter_changed,
            fg_color=Color.SURFACE,
            button_color=Color.PRIMARY,
            button_hover_color=Color.PRIMARY_HOVER,
            text_color=Color.TEXT_PRIMARY,
            width=150,
        )
        self.status_menu.pack(side="left", padx=(0, Spacing.MD))

        self.search_entry = ctk.CTkEntry(filter_row, placeholder_text="Search employee or division…")
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<KeyRelease>", lambda event: self._render_table())

        self.table_container = ctk.CTkFrame(table_body, fg_color="transparent")
        self.table_container.pack(fill="both", expand=True)

        # Built into the shared top-row container (see _build_widgets) --
        # not packed here; _sync_view_details_button_visibility() packs it
        # only while RGD Coverage is the active tab.
        self.view_details_button = SecondaryButton(
            self.view_details_button_container, text="View Employee Details",
            command=self._on_view_details_clicked, state="disabled",
        )

        self._build_mwa_tab(self.mwa_frame)

        # All three buttons now exist -- set correct initial visibility
        # (RGD Coverage is the default active tab).
        self._sync_view_details_button_visibility()

    def _build_mwa_tab(self, outer) -> None:
        """Manager Work Allocation's own nested TabBar -- ABM / RBM, each an
        independent table (see module docstring)."""
        self.mwa_sub_tabs = TabBar(outer, ["ABM", "RBM"], on_change=self._on_mwa_sub_tab_changed)
        self.mwa_sub_tabs.pack(fill="x", pady=(0, Spacing.MD))

        self.mwa_abm_frame = ctk.CTkFrame(outer, fg_color="transparent")
        self.mwa_rbm_frame = ctk.CTkFrame(outer, fg_color="transparent")
        self.mwa_abm_frame.pack(fill="both", expand=True)

        self._build_mwa_widgets(self.mwa_abm_frame)
        self._build_rbm_widgets(self.mwa_rbm_frame)

    def _on_mwa_sub_tab_changed(self, tab_name: str) -> None:
        if tab_name == "ABM":
            self.mwa_rbm_frame.pack_forget()
            self.mwa_abm_frame.pack(fill="both", expand=True)
        else:
            self.mwa_abm_frame.pack_forget()
            self.mwa_rbm_frame.pack(fill="both", expand=True)
        self._sync_view_details_button_visibility()

    def _sync_view_details_button_visibility(self) -> None:
        """Shows exactly one of the three View Employee Details buttons in
        the shared top-row container, per the current active tab (and, if
        Manager Work Allocation, active sub-tab) -- called whenever either
        changes, plus once at initial build. Each button's own enable/
        disable-on-row-selection state is untouched by this -- this only
        controls which button is packed into the shared slot at all."""
        self.view_details_button.pack_forget()
        self.mwa_view_details_button.pack_forget()
        self.rbm_view_details_button.pack_forget()

        if self.tabs.active_tab == "RGD Coverage":
            self.view_details_button.pack(side="right")
        elif self.mwa_sub_tabs.active_tab == "ABM":
            self.mwa_view_details_button.pack(side="right")
        else:
            self.rbm_view_details_button.pack(side="right")

    def _build_mwa_widgets(self, outer) -> None:
        table_card = Card(outer)
        table_card.pack(fill="both", expand=True)

        table_body = ctk.CTkFrame(table_card, fg_color="transparent")
        table_body.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)

        self.mwa_cycle_label = ctk.CTkLabel(
            table_body, text="", font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w",
        )
        self.mwa_cycle_label.pack(anchor="w", pady=(0, Spacing.SM))

        filter_row = ctk.CTkFrame(table_body, fg_color="transparent")
        filter_row.pack(fill="x", pady=(0, Spacing.SM))

        self.mwa_export_button = PrimaryButton(
            filter_row,
            text="Export",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_mwa_export_clicked,
        )
        self.mwa_export_button.pack(side="right")

        # Built into the shared top-row container (see _build_widgets) --
        # not packed here; _sync_view_details_button_visibility() packs it
        # only while Manager Work Allocation + ABM are both active.
        self.mwa_view_details_button = SecondaryButton(
            self.view_details_button_container, text="View Employee Details",
            command=self._on_mwa_view_details_clicked, state="disabled",
        )

        ctk.CTkLabel(filter_row, text="Filter:", font=Font.BODY, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(0, Spacing.SM)
        )
        self.mwa_status_menu = ctk.CTkOptionMenu(
            filter_row,
            values=["All", "Pass", "Flagged"],
            command=self._on_mwa_status_filter_changed,
            fg_color=Color.SURFACE,
            button_color=Color.PRIMARY,
            button_hover_color=Color.PRIMARY_HOVER,
            text_color=Color.TEXT_PRIMARY,
            width=150,
        )
        self.mwa_status_menu.pack(side="left", padx=(0, Spacing.MD))

        self.mwa_search_entry = ctk.CTkEntry(filter_row, placeholder_text="Search ABM or division…")
        self.mwa_search_entry.pack(side="left", fill="x", expand=True)
        self.mwa_search_entry.bind("<KeyRelease>", lambda event: self._render_mwa_table())

        self.mwa_table_container = ctk.CTkFrame(table_body, fg_color="transparent")
        self.mwa_table_container.pack(fill="both", expand=True)

    def _build_rbm_widgets(self, outer) -> None:
        table_card = Card(outer)
        table_card.pack(fill="both", expand=True)

        table_body = ctk.CTkFrame(table_card, fg_color="transparent")
        table_body.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)

        self.rbm_cycle_label = ctk.CTkLabel(
            table_body, text="", font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w",
        )
        self.rbm_cycle_label.pack(anchor="w", pady=(0, Spacing.SM))

        filter_row = ctk.CTkFrame(table_body, fg_color="transparent")
        filter_row.pack(fill="x", pady=(0, Spacing.SM))

        self.rbm_export_button = PrimaryButton(
            filter_row,
            text="Export",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_rbm_export_clicked,
        )
        self.rbm_export_button.pack(side="right")

        # Built into the shared top-row container (see _build_widgets) --
        # not packed here; _sync_view_details_button_visibility() packs it
        # only while Manager Work Allocation + RBM are both active.
        self.rbm_view_details_button = SecondaryButton(
            self.view_details_button_container, text="View Employee Details",
            command=self._on_rbm_view_details_clicked, state="disabled",
        )

        ctk.CTkLabel(filter_row, text="Filter:", font=Font.BODY, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(0, Spacing.SM)
        )
        self.rbm_status_menu = ctk.CTkOptionMenu(
            filter_row,
            values=["All", "Pass", "Flagged"],
            command=self._on_rbm_status_filter_changed,
            fg_color=Color.SURFACE,
            button_color=Color.PRIMARY,
            button_hover_color=Color.PRIMARY_HOVER,
            text_color=Color.TEXT_PRIMARY,
            width=150,
        )
        self.rbm_status_menu.pack(side="left", padx=(0, Spacing.MD))

        self.rbm_search_entry = ctk.CTkEntry(filter_row, placeholder_text="Search RBM or division…")
        self.rbm_search_entry.pack(side="left", fill="x", expand=True)
        self.rbm_search_entry.bind("<KeyRelease>", lambda event: self._render_rbm_table())

        self.rbm_table_container = ctk.CTkFrame(table_body, fg_color="transparent")
        self.rbm_table_container.pack(fill="both", expand=True)

    def _on_tab_changed(self, tab_name: str) -> None:
        if tab_name == "RGD Coverage":
            self.mwa_frame.pack_forget()
            self.rgd_frame.pack(fill="both", expand=True)
        else:
            self.rgd_frame.pack_forget()
            self.mwa_frame.pack(fill="both", expand=True)
        self._sync_view_details_button_visibility()

    def _on_status_filter_changed(self, value: str) -> None:
        self._status_filter = value
        self._render_table()

    def _on_view_details_clicked(self) -> None:
        if self._selected_row is not None and self._on_view_details is not None:
            self._on_view_details(self._selected_row)

    def _on_mwa_status_filter_changed(self, value: str) -> None:
        self._mwa_status_filter = value
        self._render_mwa_table()

    def _on_mwa_view_details_clicked(self) -> None:
        """Stamps `_engine: "mwa"` onto the row before handing it to the
        shared `on_view_details` callback -- the module shell's
        `_on_view_employee_details` is engine-agnostic, and the Employee
        Details page's own `load_employee()` uses this key to render the
        Manager Work Allocation tab (BM breakdown) instead of RGD
        Coverage's doctor list. RGD's own `_on_view_details_clicked` above
        never sets this key, so a plain RGD row is unambiguous."""
        if self._mwa_selected_row is not None and self._on_view_details is not None:
            self._on_view_details({**self._mwa_selected_row, "_engine": "mwa"})

    def _on_rbm_status_filter_changed(self, value: str) -> None:
        self._rbm_status_filter = value
        self._render_rbm_table()

    def _on_rbm_view_details_clicked(self) -> None:
        """Same `_engine: "mwa"` stamp as ABM's own
        `_on_mwa_view_details_clicked` -- the Employee Details page then
        picks the RBM detail view (vs. ABM's) off the row's own
        `designation` field ("RBM"), already present on every Manager Work
        Allocation finding, no separate sub-engine marker needed."""
        if self._rbm_selected_row is not None and self._on_view_details is not None:
            self._on_view_details({**self._rbm_selected_row, "_engine": "mwa"})

    def _on_export_clicked(self) -> None:
        """Exports `self._current_display_rows` -- the exact list
        `_render_table()` just used to populate the Treeview (post status
        filter and search), so the exported workbook always matches the
        current on-screen view."""
        suffix = "" if self._status_filter == "All" else self._status_filter
        export_rows_with_ui(
            self,
            rows=self._current_display_rows,
            columns=COLUMNS,
            headings=HEADINGS,
            suggested_filename=default_export_filename("WorkDistributionFindings", suffix=suffix),
            sheet_title="Findings",
        )

    def _render_table(self) -> None:
        for widget in self.table_container.winfo_children():
            widget.destroy()
        self._selected_row = None
        self.view_details_button.configure(state="disabled")
        self._current_display_rows = []

        if not has_data():
            EmptyState(
                self.table_container, "Upload a Work Distribution report to generate findings."
            ).pack(fill="both", expand=True)
            return

        query = self.search_entry.get().strip().lower()
        rows = self._all_rows
        if self._status_filter != "All":
            rows = [r for r in rows if r["status"] == self._status_filter]
        if query:
            rows = [
                r for r in rows
                if query in r["employee_name"].lower() or query in r["division"].lower()
            ]

        # Captured here, BEFORE the "no match" empty-state check below, so
        # Export always reflects reality -- an empty list when nothing
        # matches the current filter (export_rows_with_ui shows "Nothing
        # to Export" rather than silently doing nothing).
        self._current_display_rows = rows

        if not rows:
            EmptyState(self.table_container, "No employees match the current filter.").pack(fill="both", expand=True)
            return

        tree = styled_treeview(self.table_container, COLUMNS, HEADINGS, WIDTHS, height=14)
        tree.tag_configure("flagged", background=Color.WARNING_SOFT)

        rows_by_iid: dict[str, dict] = {}
        for i, row in enumerate(rows):
            iid = str(i)
            rows_by_iid[iid] = row
            tag = "flagged" if row["status"] == "Flagged" else None
            tree.insert(
                "", "end", iid=iid,
                tags=(tag,) if tag else (),
                values=tuple(row[col] for col in COLUMNS),
            )
        tree.pack(fill="both", expand=True)

        def on_select(event, tree=tree, rows_by_iid=rows_by_iid) -> None:
            selection = tree.selection()
            if not selection:
                self._selected_row = None
                self.view_details_button.configure(state="disabled")
                return
            self._selected_row = rows_by_iid.get(selection[0])
            self.view_details_button.configure(state="normal")

        tree.bind("<<TreeviewSelect>>", on_select)

    def _on_mwa_export_clicked(self) -> None:
        """Exports `self._mwa_current_display_rows` -- the exact list
        `_render_mwa_table()` just used to populate the Treeview (post
        status filter and search), same filter-aware contract as RGD
        Coverage's own Export."""
        suffix = "" if self._mwa_status_filter == "All" else self._mwa_status_filter
        export_rows_with_ui(
            self,
            rows=self._mwa_current_display_rows,
            columns=MWA_COLUMNS,
            headings=MWA_HEADINGS,
            suggested_filename=default_export_filename("ManagerWorkAllocationFindings", suffix=suffix),
            sheet_title="Findings",
        )

    def _render_mwa_table(self) -> None:
        for widget in self.mwa_table_container.winfo_children():
            widget.destroy()
        self._mwa_selected_row = None
        self.mwa_view_details_button.configure(state="disabled")
        self._mwa_current_display_rows = []

        cycle_label = get_mwa_cycle_label()
        self.mwa_cycle_label.configure(
            text=f"Current cycle: {cycle_label}" if cycle_label else ""
        )

        if not mwa_has_data():
            EmptyState(
                self.mwa_table_container, "No analysis has been run."
            ).pack(fill="both", expand=True)
            return

        query = self.mwa_search_entry.get().strip().lower()
        rows = self._mwa_all_rows
        if self._mwa_status_filter != "All":
            rows = [r for r in rows if r["status"] == self._mwa_status_filter]
        if query:
            rows = [
                r for r in rows
                if query in r["employee_name"].lower() or query in r["division"].lower()
            ]

        # Captured here, BEFORE the "no match" empty-state check below, so
        # Export always reflects reality, same convention as RGD's own
        # _render_table.
        self._mwa_current_display_rows = rows

        if not rows:
            EmptyState(self.mwa_table_container, "No ABMs match the current filter.").pack(fill="both", expand=True)
            return

        tree = styled_treeview(self.mwa_table_container, MWA_COLUMNS, MWA_HEADINGS, MWA_WIDTHS, height=14)
        tree.tag_configure("flagged", background=Color.WARNING_SOFT)

        rows_by_iid: dict[str, dict] = {}
        for i, row in enumerate(rows):
            iid = str(i)
            rows_by_iid[iid] = row
            tag = "flagged" if row["status"] == "Flagged" else None
            tree.insert(
                "", "end", iid=iid,
                tags=(tag,) if tag else (),
                values=tuple(row[col] for col in MWA_COLUMNS),
            )
        tree.pack(fill="both", expand=True)

        def on_select(event, tree=tree, rows_by_iid=rows_by_iid) -> None:
            selection = tree.selection()
            if not selection:
                self._mwa_selected_row = None
                self.mwa_view_details_button.configure(state="disabled")
                return
            self._mwa_selected_row = rows_by_iid.get(selection[0])
            self.mwa_view_details_button.configure(state="normal")

        tree.bind("<<TreeviewSelect>>", on_select)

    def _on_rbm_export_clicked(self) -> None:
        """Exports `self._rbm_current_display_rows` -- the exact list
        `_render_rbm_table()` just used to populate the Treeview (post
        status filter and search), same filter-aware contract as ABM's and
        RGD Coverage's own Export."""
        suffix = "" if self._rbm_status_filter == "All" else self._rbm_status_filter
        export_rows_with_ui(
            self,
            rows=self._rbm_current_display_rows,
            columns=RBM_COLUMNS,
            headings=RBM_HEADINGS,
            suggested_filename=default_export_filename("ManagerWorkAllocationRBMFindings", suffix=suffix),
            sheet_title="Findings",
        )

    def _render_rbm_table(self) -> None:
        for widget in self.rbm_table_container.winfo_children():
            widget.destroy()
        self._rbm_selected_row = None
        self.rbm_view_details_button.configure(state="disabled")
        self._rbm_current_display_rows = []

        cycle_label = get_rbm_cycle_label()
        self.rbm_cycle_label.configure(
            text=f"Current cycle: {cycle_label}" if cycle_label else ""
        )

        if not rbm_has_data():
            EmptyState(
                self.rbm_table_container, "No analysis has been run."
            ).pack(fill="both", expand=True)
            return

        query = self.rbm_search_entry.get().strip().lower()
        rows = self._rbm_all_rows
        if self._rbm_status_filter != "All":
            rows = [r for r in rows if r["status"] == self._rbm_status_filter]
        if query:
            rows = [
                r for r in rows
                if query in r["employee_name"].lower() or query in r["division"].lower()
            ]

        self._rbm_current_display_rows = rows

        if not rows:
            EmptyState(self.rbm_table_container, "No RBMs match the current filter.").pack(fill="both", expand=True)
            return

        tree = styled_treeview(self.rbm_table_container, RBM_COLUMNS, RBM_HEADINGS, RBM_WIDTHS, height=14)
        tree.tag_configure("flagged", background=Color.WARNING_SOFT)

        rows_by_iid: dict[str, dict] = {}
        for i, row in enumerate(rows):
            iid = str(i)
            rows_by_iid[iid] = row
            tag = "flagged" if row["status"] == "Flagged" else None
            tree.insert(
                "", "end", iid=iid,
                tags=(tag,) if tag else (),
                values=tuple(row[col] for col in RBM_COLUMNS),
            )
        tree.pack(fill="both", expand=True)

        def on_select(event, tree=tree, rows_by_iid=rows_by_iid) -> None:
            selection = tree.selection()
            if not selection:
                self._rbm_selected_row = None
                self.rbm_view_details_button.configure(state="disabled")
                return
            self._rbm_selected_row = rows_by_iid.get(selection[0])
            self.rbm_view_details_button.configure(state="normal")

        tree.bind("<<TreeviewSelect>>", on_select)
