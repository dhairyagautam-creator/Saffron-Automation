"""Path Validator Master Overview -- the founder's triage page.

Answers one question at a glance: "who needs my attention right now, and
why?". It is NOT an analytics dashboard and it re-derives nothing: every
number and row comes from the Phase 1 data layer
(app.master_attention_service.get_current_attention_records), which
consolidates the existing detectors' findings at the employee level. This
page only presents those EmployeeAttentionRecords -- KPI counts, a
severity-ranked table, filters, and an on-page evidence panel -- and never
queries raw_visits or re-runs Location / Low Working Hours logic itself.
"""

import customtkinter as ctk
from loguru import logger

from app.master_attention_service import (
    FindingType,
    Severity,
    get_current_attention_records,
)
from app.session_state import get_active_import
from ui.components import (
    Card,
    EmptyState,
    KPICard,
    SectionHeader,
    StatusBadge,
    render_error_banner,
    styled_treeview,
)
from ui.theme import Color, Font, Spacing

# Compact badge text per finding type (the table's "Issues" column) -- short
# by design so a founder scans them, not reads them. Future types already map.
ISSUE_SHORT = {
    FindingType.LOCATION: "LOCATION",
    FindingType.LOW_WORKING_HOURS: "LOW HOURS",
    FindingType.LOW_CALL_COUNT: "LOW CALLS",
    FindingType.REPEAT_OFFENDER: "REPEAT",
}

SEVERITY_BADGE_KIND = {
    Severity.CRITICAL: "error",
    Severity.ATTENTION: "warning",
    Severity.WATCH: "neutral",
}
# Row background tint per severity -- the same tag-coloring convention the
# Findings page uses to make rows scannable without reading every cell.
SEVERITY_ROW_FILL = {
    Severity.CRITICAL: Color.ERROR_SOFT,
    Severity.ATTENTION: Color.WARNING_SOFT,
    Severity.WATCH: Color.SURFACE,
}

COLUMNS = ("employee", "designation", "rbm", "issues", "summary")
HEADINGS = {
    "employee": "Employee",
    "designation": "Designation",
    "rbm": "RBM",
    "issues": "Issues",
    "summary": "Summary",
}
WIDTHS = {
    "employee": 190,
    "designation": 110,
    "rbm": 160,
    "issues": 180,
    "summary": 300,
}

ISSUE_FILTERS = ("All", "Location", "Low Working Hours", "Multiple")


def _issue_badges(record) -> str:
    return " + ".join(ISSUE_SHORT.get(af.finding_type, af.finding_type) for af in record.applicable_findings)


def kpi_counts(records: list) -> dict:
    """The four KPI numbers, derived purely from the Phase 1 records (no
    detection logic re-run). Pure/module-level so it's testable headless."""
    return {
        "flagged": len(records),
        "location": sum(1 for r in records if any(af.finding_type == FindingType.LOCATION for af in r.applicable_findings)),
        "low_hours": sum(1 for r in records if any(af.finding_type == FindingType.LOW_WORKING_HOURS for af in r.applicable_findings)),
        "multiple": sum(1 for r in records if len(r.applicable_findings) >= 2),
    }


def filter_records(records: list, severity: str, designation: str, issue: str, query: str) -> list:
    """Pure filter over EmployeeAttentionRecords -- the page passes its widget
    values in, so the filtering rules are testable without a Tk root. Records
    keep their incoming (severity-ranked) order."""
    query = (query or "").strip().lower()
    result = []
    for r in records:
        if severity != "All" and r.severity != severity:
            continue
        if designation != "All" and (r.designation or "").upper() != designation:
            continue
        types = {af.finding_type for af in r.applicable_findings}
        if issue == "Location" and FindingType.LOCATION not in types:
            continue
        if issue == "Low Working Hours" and FindingType.LOW_WORKING_HOURS not in types:
            continue
        if issue == "Multiple" and len(r.applicable_findings) < 2:
            continue
        if query and query not in r.employee_name.lower() and query not in r.employee_code.lower():
            continue
        result.append(r)
    return result


class MasterPage(ctk.CTkFrame):
    """Founder-facing overview: KPI cards, a severity-ranked employee table,
    simple filters, and an on-page evidence panel. All data is the Phase 1
    EmployeeAttentionRecord list -- nothing is recalculated here."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)

        self._records: list = []          # every EmployeeAttentionRecord (unfiltered)
        self._by_code: dict = {}          # employee_code -> record (for row selection)
        self._error: Exception | None = None

        self._severity_filter = "All"
        self._designation_filter = "All"
        self._issue_filter = "All"

        self._build_widgets()

    # --- Layout ------------------------------------------------------------

    def _build_widgets(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(outer, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.MD))
        SectionHeader(
            header_row, "Master Overview", "Path Validator — who needs your attention right now"
        ).pack(side="left", anchor="w")
        self.upload_label = ctk.CTkLabel(
            header_row, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="e", justify="right"
        )
        self.upload_label.pack(side="right", anchor="e")

        self._build_kpis(outer)
        self._build_filters(outer)

        content_row = ctk.CTkFrame(outer, fg_color="transparent")
        content_row.pack(fill="both", expand=True)

        table_card = Card(content_row)
        table_card.pack(side="left", fill="both", expand=True, padx=(0, Spacing.MD))
        table_body = ctk.CTkFrame(table_card, fg_color="transparent")
        table_body.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)
        self.table_container = ctk.CTkFrame(table_body, fg_color="transparent")
        self.table_container.pack(fill="both", expand=True)

        self.detail_card = Card(content_row, width=340)
        self.detail_card.pack(side="right", fill="y")
        self.detail_card.pack_propagate(False)
        self.detail_container = ctk.CTkScrollableFrame(self.detail_card, fg_color="transparent")
        self.detail_container.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)
        self._render_detail(None)

    def _build_kpis(self, parent) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, Spacing.MD))
        # (label, accent, filter-on-click) -- clicking a card applies the same
        # filter a founder would reach for.
        specs = [
            ("Employees Flagged", Color.PRIMARY, ("issue", "All")),
            ("Location Issues", Color.INFO, ("issue", "Location")),
            ("Low Working Hours", Color.WARNING, ("issue", "Low Working Hours")),
            ("Multiple Issues", Color.ERROR, ("issue", "Multiple")),
        ]
        self.kpi_cards: dict[str, KPICard] = {}
        for i, (label, accent, action) in enumerate(specs):
            row.grid_columnconfigure(i, weight=1)
            card = KPICard(row, label=label, value="—", accent=accent)
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else Spacing.SM, 0))
            self.kpi_cards[label] = card
            self._bind_click(card, lambda a=action: self._on_kpi_clicked(a))

    def _build_filters(self, parent) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, Spacing.SM))

        def menu(values, command, width=140):
            m = ctk.CTkOptionMenu(
                row, values=values, command=command, width=width,
                fg_color=Color.SURFACE, button_color=Color.PRIMARY,
                button_hover_color=Color.PRIMARY_HOVER, text_color=Color.TEXT_PRIMARY,
            )
            m.pack(side="left", padx=(0, Spacing.SM))
            return m

        ctk.CTkLabel(row, text="Severity:", font=Font.SMALL, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(0, 4)
        )
        self.severity_menu = menu(["All", Severity.CRITICAL, Severity.ATTENTION, Severity.WATCH],
                                  self._on_severity_filter, width=120)
        ctk.CTkLabel(row, text="Designation:", font=Font.SMALL, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(0, 4)
        )
        self.designation_menu = menu(["All", "BM", "ABM"], self._on_designation_filter, width=90)
        ctk.CTkLabel(row, text="Issue:", font=Font.SMALL, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(0, 4)
        )
        self.issue_menu = menu(list(ISSUE_FILTERS), self._on_issue_filter, width=150)

        # RBM filter removed (Phase 3 cleanup); the freed width goes to a long,
        # genuinely useful employee search box.
        ctk.CTkLabel(row, text="Search:", font=Font.SMALL, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(Spacing.MD, 4)
        )
        self.search_entry = ctk.CTkEntry(row, placeholder_text="Search employee name or code…", height=32)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 0))
        self.search_entry.bind("<KeyRelease>", lambda event: self._render_table())

    def _bind_click(self, widget, command) -> None:
        widget.configure(cursor="hand2")
        widget.bind("<Button-1>", lambda event: command())
        for child in widget.winfo_children():
            self._bind_click(child, command)

    # --- Load ---------------------------------------------------------------

    def on_show(self) -> None:
        """Reload the Phase 1 attention records every time the page appears.
        Loaded synchronously (a findings read + hierarchy lookups -- fast, and
        the same convention the Findings page uses), guarded so an unexpected
        data error becomes an on-page error state instead of crashing."""
        try:
            records = get_current_attention_records()
            upload = get_active_import()
            error = None
        except Exception as exc:
            logger.exception("Master page failed to load attention records")
            records, upload, error = [], None, exc
        self._apply_loaded(records, upload, error)

    def _apply_loaded(self, records, upload, error) -> None:
        self._records = records
        self._by_code = {r.employee_code: r for r in records}
        self._error = error

        # Latest processed dataset context (no date selector -- history isn't
        # stored yet, so this is simply the active upload).
        if error is not None:
            self.upload_label.configure(text="")
        elif upload is not None and upload.imported_at is not None:
            self.upload_label.configure(
                text=f"Latest processed upload: {upload.imported_at.strftime('%d %b %Y, %I:%M %p')}"
            )
        else:
            self.upload_label.configure(text="No upload processed yet")

        self._update_kpis()
        self._render_detail(None)
        self._render_table()

    def _update_kpis(self) -> None:
        counts = kpi_counts(self._records)
        self.kpi_cards["Employees Flagged"].set_value(str(counts["flagged"]))
        self.kpi_cards["Location Issues"].set_value(str(counts["location"]))
        self.kpi_cards["Low Working Hours"].set_value(str(counts["low_hours"]))
        self.kpi_cards["Multiple Issues"].set_value(str(counts["multiple"]))

    # --- Filters -----------------------------------------------------------

    def _on_kpi_clicked(self, action) -> None:
        kind, value = action
        if kind == "issue":
            # A KPI click is a shortcut: reset the other filters, set this one.
            self._severity_filter = self._designation_filter = "All"
            self.severity_menu.set("All")
            self.designation_menu.set("All")
            self._issue_filter = value
            self.issue_menu.set(value)
            self.search_entry.delete(0, "end")
        self._render_table()

    def _on_severity_filter(self, value: str) -> None:
        self._severity_filter = value
        self._render_table()

    def _on_designation_filter(self, value: str) -> None:
        self._designation_filter = value
        self._render_table()

    def _on_issue_filter(self, value: str) -> None:
        self._issue_filter = value
        self._render_table()

    def _filtered_records(self) -> list:
        return filter_records(
            self._records,
            self._severity_filter,
            self._designation_filter,
            self._issue_filter,
            self.search_entry.get(),
        )

    # --- Table -------------------------------------------------------------

    def _render_table(self) -> None:
        for widget in self.table_container.winfo_children():
            widget.destroy()

        if self._error is not None:
            render_error_banner(
                self.table_container,
                "Could not load the Master overview",
                [str(self._error), "Try Refresh, or re-open the page."],
            )
            return

        if get_active_import() is None:
            EmptyState(
                self.table_container,
                "No processed dataset yet — import a workbook on the Operations page.",
            ).pack(fill="both", expand=True)
            return

        if not self._records:
            EmptyState(
                self.table_container, "No employees currently require attention."
            ).pack(fill="both", expand=True)
            return

        rows = self._filtered_records()
        if not rows:
            EmptyState(self.table_container, "No employees match the current filters.").pack(fill="both", expand=True)
            return

        tree = styled_treeview(self.table_container, COLUMNS, HEADINGS, WIDTHS, height=16)
        for severity, fill in SEVERITY_ROW_FILL.items():
            tree.tag_configure(severity, background=fill)

        for record in rows:
            tree.insert(
                "",
                "end",
                iid=record.employee_code,
                tags=(record.severity,),
                values=(
                    # Name + code -- two different employees can share a name
                    # (e.g. two "Vishal Kumar", codes SF0565 and SF2080); the
                    # code makes their separate records unambiguous, matching
                    # the Findings pages' "Name (Code)" convention.
                    f"{record.employee_name} ({record.employee_code})",
                    record.designation or "—",
                    record.rbm_name or "Unresolved",
                    _issue_badges(record),
                    record.summary,
                ),
            )
        tree.pack(fill="both", expand=True)
        tree.bind("<<TreeviewSelect>>", lambda event, t=tree: self._on_row_selected(t))

    def _on_row_selected(self, tree) -> None:
        selection = tree.selection()
        self._render_detail(self._by_code.get(selection[0]) if selection else None)

    # --- Detail panel (on-page drill-down) ---------------------------------

    def _field(self, label, value) -> None:
        ctk.CTkLabel(
            self.detail_container, text=label, font=Font.SMALL_BOLD, text_color=Color.TEXT_MUTED, anchor="w"
        ).pack(anchor="w", pady=(Spacing.SM, 0))
        ctk.CTkLabel(
            self.detail_container, text=str(value), font=Font.BODY, text_color=Color.TEXT_PRIMARY,
            anchor="w", wraplength=290, justify="left",
        ).pack(anchor="w")

    def _subhead(self, text) -> None:
        ctk.CTkLabel(
            self.detail_container, text=text, font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(Spacing.MD, 2))

    def _bullets(self, lines) -> None:
        for line in lines:
            ctk.CTkLabel(
                self.detail_container, text=f"•  {line}", font=Font.BODY, text_color=Color.TEXT_PRIMARY,
                anchor="w", wraplength=290, justify="left",
            ).pack(anchor="w", pady=(1, 0))

    def _render_detail(self, record) -> None:
        for widget in self.detail_container.winfo_children():
            widget.destroy()

        ctk.CTkLabel(
            self.detail_container, text="Attention Detail", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        if record is None:
            EmptyState(self.detail_container, "Select an employee to see why they're flagged.").pack(
                fill="both", expand=True
            )
            return

        self._field("Employee", f"{record.employee_name} ({record.employee_code})")
        self._field("Designation", record.designation or "—")
        self._field("RBM", record.rbm_name or "Unresolved")

        ctk.CTkLabel(
            self.detail_container, text="Severity", font=Font.SMALL_BOLD, text_color=Color.TEXT_MUTED, anchor="w"
        ).pack(anchor="w", pady=(Spacing.SM, 4))
        StatusBadge(
            self.detail_container, record.severity, SEVERITY_BADGE_KIND.get(record.severity, "neutral")
        ).pack(anchor="w")

        types = {af.finding_type for af in record.applicable_findings}
        if FindingType.LOCATION in types and FindingType.LOW_WORKING_HOURS in types:
            ctk.CTkLabel(
                self.detail_container, text="Location + Low Working Hours", font=Font.SMALL_BOLD,
                text_color=Color.PRIMARY, anchor="w",
            ).pack(anchor="w", pady=(Spacing.SM, 0))

        # Only the applicable finding sections, straight from the record's
        # evidence (produced by the detectors in Phase 1 -- never recomputed).
        for af in record.applicable_findings:
            if af.finding_type == FindingType.LOCATION:
                self._subhead("Location")
                self._bullets(self._location_lines(af.evidence))
            elif af.finding_type == FindingType.LOW_WORKING_HOURS:
                self._subhead("Working Hours")
                self._render_hours(af.evidence)
            else:
                self._subhead(af.label)
                self._bullets([af.summary])

    def _location_lines(self, evidence) -> list[str]:
        lines = []
        for occ in evidence.get("occurrences", []):
            date_text = occ["visit_date"].strftime("%d %b %Y") if occ.get("visit_date") else ""
            if occ.get("concentration_percent") is not None and occ.get("valid_visit_count") is not None:
                lines.append(
                    f"{occ['concentration_percent']:g}% of {occ['valid_visit_count']} valid GPS calls within "
                    f"~{occ.get('radius_meters')} m (threshold {occ.get('threshold_percent'):g}%)"
                    f"{f' on {date_text}' if date_text else ''}."
                )
            else:
                lines.append(occ.get("message", ""))
        return lines

    def _render_hours(self, evidence) -> None:
        for occ in evidence.get("occurrences", []):
            if occ.get("first_call"):
                self._field("First call", occ["first_call"])
                self._field("Last call", occ["last_call"])
                self._field("Hours worked", f"{occ['hours_worked']:.1f} h")
                self._field("Required hours", f"{occ['minimum']:g} h")
                self._field("Hours short", f"{occ['hours_short']:.1f} h")
            else:
                self._field("Detail", occ.get("message", ""))
