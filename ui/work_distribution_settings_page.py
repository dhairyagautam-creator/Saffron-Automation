"""Work Distribution Settings page: BM/ABM KPI thresholds, backed by
app/work_distribution_parameters_service.py -- every value here is read
from and saved to that service, never hardcoded (see
app.work_distribution_service's own KPI evaluation, which reads the same
service). Saving does not retroactively re-flag any already-generated
finding -- it only takes effect on the next uploaded report, same
convention as ui/inventory_settings_page.py's own multiplier fields.

The old General Settings card (Report Cycle, a single Notification Email
field) was removed in the UI-refinement pass. The Email Configuration card
that briefly lived here afterward has since moved to its own Email Center
page (see ui/work_distribution_email_center_page.py) -- per the
architecture-update instruction, sender credentials sit alongside the
hierarchy uploads that will resolve recipients automatically, not on this
KPI-thresholds page.

Version 2.1 architecture update: this page is now split into two
collapsible sections (see ui.components.CollapsibleSection) -- RGD
Coverage (the BM/ABM Parameters above, UNCHANGED behavior) and Manager
Work Allocation.

Phase 2 (ABM engine): Manager Work Allocation's ABM Settings card is now
live -- Minimum Joint Working Days, backed by
app/manager_work_allocation_parameters_service.py (its own, independent
parameter store -- see that module's docstring for why).

Phase 3.1 (RBM thresholds configurable): RBM Settings is now live too --
"RBM Threshold Configuration", a fixed 3-row table (Minimum BM Count /
Maximum BM Count / Allowed Missed BMs) matching the module's own spec
example exactly. The last row's Maximum BM Count accepts "Unlimited" (or
blank) for "no upper limit". Saved as a single JSON-encoded parameter via
app.manager_work_allocation_parameters_service.get_rbm_flag_tiers/
set_rbm_flag_tiers -- see that module's own docstring for why a list of
tiers can't use the same float-typed get/set every other field here uses.
Validated with app.manager_work_allocation_rbm_service.validate_rbm_flag_tiers
BEFORE ever saving (non-negative integers, min <= max, "Unlimited" only on
the final row, no overlapping ranges) -- a failing validation shows every
error and refuses to save, same convention as every other field's
inline validation on this page.
"""

import customtkinter as ctk

from app.manager_work_allocation_parameters_service import (
    get_minimum_joint_working_days,
    get_rbm_flag_tiers,
    set_minimum_joint_working_days,
    set_rbm_flag_tiers,
)
from app.manager_work_allocation_rbm_service import validate_rbm_flag_tiers
from app.work_distribution_parameters_service import (
    get_abm_coverage_doctors,
    get_abm_missed_doctors,
    get_bm_coverage_percent,
    get_bm_minimum_calls,
    get_bm_missed_doctor_percent,
    get_bm_target_calls,
    set_abm_coverage_doctors,
    set_abm_missed_doctors,
    set_bm_coverage_percent,
    set_bm_minimum_calls,
    set_bm_missed_doctor_percent,
    set_bm_target_calls,
)
from ui.components import Card, CollapsibleSection, PrimaryButton, SectionHeader
from ui.theme import Color, Font, Spacing

# (label, get_fn, set_fn) -- each field is loaded from and saved straight
# to app.work_distribution_parameters_service, never a hardcoded default.
BM_FIELDS = [
    ("Minimum Calls", get_bm_minimum_calls, set_bm_minimum_calls),
    ("Target Calls", get_bm_target_calls, set_bm_target_calls),
    ("Missed Doctor %", get_bm_missed_doctor_percent, set_bm_missed_doctor_percent),
    ("Coverage %", get_bm_coverage_percent, set_bm_coverage_percent),
]
ABM_FIELDS = [
    ("Missed Doctors", get_abm_missed_doctors, set_abm_missed_doctors),
    ("Doctors with <2 Visits", get_abm_coverage_doctors, set_abm_coverage_doctors),
]

# Manager Work Allocation's own ABM engine settings -- backed by
# app.manager_work_allocation_parameters_service, a completely separate
# parameter store from RGD Coverage's WorkDistributionParameter above.
MWA_ABM_FIELDS = [
    ("Minimum Joint Working Days", get_minimum_joint_working_days, set_minimum_joint_working_days),
]


def _format_value(value: float) -> str:
    """'135' for a whole number, '4.5' otherwise -- avoids a trailing
    '.0' for the common whole-number case."""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


class WorkDistributionSettingsPage(ctk.CTkFrame):
    """BM Parameters / ABM Parameters cards."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._entries: dict[str, ctk.CTkEntry] = {}
        self._build_widgets()

    def on_show(self) -> None:
        """Reload every field from the database -- reflects a value saved
        elsewhere (or on a previous visit) rather than showing stale
        widget state."""
        self._load_all()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Work Distribution Settings", "KPI parameters for monthly doctor coverage"
        ).pack(anchor="w", pady=(0, Spacing.LG))

        rgd_section = CollapsibleSection(outer, "RGD Coverage", expanded=True)
        rgd_section.pack(fill="x", pady=(0, Spacing.LG))
        self._build_rgd_coverage_body(rgd_section.body)

        mwa_section = CollapsibleSection(outer, "Manager Work Allocation", expanded=False)
        mwa_section.pack(fill="x")
        self._build_manager_work_allocation_body(mwa_section.body)

        self._load_all()

    def _build_rgd_coverage_body(self, body) -> None:
        self._build_parameter_card(
            body,
            title="BM Parameters",
            description=(
                "Coverage thresholds applied to Branch Managers (Minimum/Target Calls compare "
                "against a BM's total visit count; Missed Doctor %/Coverage % compare against "
                "the percentage of their book)."
            ),
            fields=BM_FIELDS,
        )
        self._build_parameter_card(
            body,
            title="ABM Parameters",
            description=(
                "Coverage thresholds applied to Area Business Managers, evaluated only against "
                "doctors marked A-RGD in their book (raw doctor counts, not percentages)."
            ),
            fields=ABM_FIELDS,
        )
        self._build_save_row(body)

    def _build_manager_work_allocation_body(self, body) -> None:
        ctk.CTkLabel(
            body,
            text="Settings for the Manager Work Allocation engine's ABM phase. RBM has not been implemented yet.",
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        self._build_parameter_card(
            body,
            title="ABM Settings",
            description=(
                "Each BM under an ABM must have at least this many total joint working days "
                "(after merging any duplicate manager-subordinate rows) or that ABM is flagged."
            ),
            fields=MWA_ABM_FIELDS,
        )
        self._build_mwa_save_row(body)

        self._build_rbm_threshold_card(body)

    def _build_mwa_save_row(self, outer) -> None:
        row = ctk.CTkFrame(outer, fg_color="transparent")
        row.pack(fill="x", pady=(0, Spacing.LG))

        PrimaryButton(row, text="Save", command=self._on_mwa_save_clicked).pack(side="left", padx=(0, Spacing.SM))

        self.mwa_save_status_label = ctk.CTkLabel(
            row, text="", font=Font.SMALL_BOLD, text_color=Color.SUCCESS, anchor="w",
            wraplength=650, justify="left",
        )
        self.mwa_save_status_label.pack(side="left")

    def _build_rbm_threshold_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(0, Spacing.LG))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="RBM Threshold Configuration", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "An RBM is flagged when at least the \"Allowed Missed BMs\" count of their unique "
                "BMs were not worked with even once this month, based on which row their total "
                "unique BM count falls into. The final row's Maximum BM Count may be left as "
                "\"Unlimited\" for no upper limit."
            ),
            font=Font.BODY, text_color=Color.TEXT_SECONDARY, anchor="w", wraplength=700, justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        header_row = ctk.CTkFrame(body, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.SM))
        for text, width in (
            ("Minimum BM Count", 160), ("Maximum BM Count", 160), ("Allowed Missed BMs ≥", 170),
        ):
            ctk.CTkLabel(
                header_row, text=text, font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w", width=width
            ).pack(side="left", padx=(0, Spacing.SM))

        self._rbm_tier_rows: list[dict] = []
        for _ in range(3):
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", pady=(0, Spacing.SM))
            min_entry = ctk.CTkEntry(row, width=150)
            min_entry.pack(side="left", padx=(0, Spacing.SM))
            max_entry = ctk.CTkEntry(row, width=150)
            max_entry.pack(side="left", padx=(0, Spacing.SM))
            missed_entry = ctk.CTkEntry(row, width=150)
            missed_entry.pack(side="left", padx=(0, Spacing.SM))
            self._rbm_tier_rows.append({"min": min_entry, "max": max_entry, "missed": missed_entry})

        row_action = ctk.CTkFrame(body, fg_color="transparent")
        row_action.pack(fill="x", pady=(Spacing.SM, 0))
        PrimaryButton(row_action, text="Save", command=self._on_rbm_tiers_save_clicked).pack(
            side="left", padx=(0, Spacing.SM)
        )
        self.rbm_tiers_status_label = ctk.CTkLabel(
            row_action, text="", font=Font.SMALL_BOLD, text_color=Color.SUCCESS, anchor="w",
            wraplength=650, justify="left",
        )
        self.rbm_tiers_status_label.pack(side="left")

    def _build_parameter_card(self, outer, title: str, description: str, fields: list) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(0, Spacing.LG))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text=title, font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body, text=description, font=Font.BODY, text_color=Color.TEXT_SECONDARY, anchor="w",
            wraplength=700, justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        for label, get_fn, _set_fn in fields:
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", pady=(0, Spacing.SM))
            ctk.CTkLabel(
                row, text=label, font=Font.BODY, text_color=Color.TEXT_SECONDARY, anchor="w", width=220
            ).pack(side="left")
            entry = ctk.CTkEntry(row, width=100)
            entry.pack(side="left")
            self._entries[label] = entry

    def _build_save_row(self, outer) -> None:
        row = ctk.CTkFrame(outer, fg_color="transparent")
        row.pack(fill="x")

        PrimaryButton(row, text="Save", command=self._on_save_clicked).pack(side="left", padx=(0, Spacing.SM))

        self.save_status_label = ctk.CTkLabel(
            row, text="", font=Font.SMALL_BOLD, text_color=Color.SUCCESS, anchor="w",
            wraplength=650, justify="left",
        )
        self.save_status_label.pack(side="left")

    # --- Load / Save -------------------------------------------------------

    def _load_all(self) -> None:
        for label, get_fn, _set_fn in BM_FIELDS + ABM_FIELDS + MWA_ABM_FIELDS:
            entry = self._entries[label]
            entry.delete(0, "end")
            entry.insert(0, _format_value(get_fn()))
        self.save_status_label.configure(text="")
        self.mwa_save_status_label.configure(text="")
        self._load_rbm_tiers()

    def _load_rbm_tiers(self) -> None:
        tiers = get_rbm_flag_tiers()
        for i, row in enumerate(self._rbm_tier_rows):
            tier = tiers[i] if i < len(tiers) else {"min": "", "max": "", "missed": ""}
            row["min"].delete(0, "end")
            row["min"].insert(0, str(tier.get("min", "")))
            row["max"].delete(0, "end")
            row["max"].insert(0, "Unlimited" if tier.get("max") is None else str(tier.get("max")))
            row["missed"].delete(0, "end")
            row["missed"].insert(0, str(tier.get("missed", "")))
        self.rbm_tiers_status_label.configure(text="")

    def _save_fields(self, fields: list, status_label, success_message: str) -> None:
        """Shared validate-then-save for one field group -- used
        separately by RGD Coverage's own Save (BM_FIELDS + ABM_FIELDS) and
        Manager Work Allocation ABM Settings' own Save (MWA_ABM_FIELDS), so
        clicking one section's Save never touches the other section's
        values, per explicit instruction not to modify RGD Coverage."""
        parsed: dict = {}
        for label, _get_fn, set_fn in fields:
            raw = self._entries[label].get().strip()
            try:
                value = float(raw)
                if value < 0:
                    raise ValueError("must not be negative")
            except ValueError:
                status_label.configure(
                    text=f"'{raw}' is not a valid value for {label} -- enter a non-negative number.",
                    text_color=Color.ERROR,
                )
                return
            parsed[label] = (set_fn, value)

        for set_fn, value in parsed.values():
            set_fn(_format_value(value))

        self._load_all()
        status_label.configure(text=success_message, text_color=Color.SUCCESS)

    def _on_save_clicked(self) -> None:
        self._save_fields(
            BM_FIELDS + ABM_FIELDS, self.save_status_label,
            "Saved. Takes effect the next time a Work Distribution report is uploaded.",
        )

    def _on_mwa_save_clicked(self) -> None:
        self._save_fields(
            MWA_ABM_FIELDS, self.mwa_save_status_label,
            "Saved. Takes effect the next time a Manager Work Allocation report is uploaded.",
        )

    def _on_rbm_tiers_save_clicked(self) -> None:
        """Parses all 3 rows, validates with
        app.manager_work_allocation_rbm_service.validate_rbm_flag_tiers
        (non-negative integers, min <= max, "Unlimited" only on the final
        row, no overlapping ranges), and refuses to save -- showing every
        validation error -- if any check fails."""
        parsed_tiers = []
        for i, row in enumerate(self._rbm_tier_rows, start=1):
            min_text = row["min"].get().strip()
            max_text = row["max"].get().strip()
            missed_text = row["missed"].get().strip()

            try:
                min_value = int(min_text)
            except ValueError:
                self.rbm_tiers_status_label.configure(
                    text=f"Row {i}: '{min_text}' is not a valid Minimum BM Count -- enter a whole number.",
                    text_color=Color.ERROR,
                )
                return

            if max_text.lower() in ("unlimited", "no upper limit", "no limit", ""):
                max_value = None
            else:
                try:
                    max_value = int(max_text)
                except ValueError:
                    self.rbm_tiers_status_label.configure(
                        text=(
                            f"Row {i}: '{max_text}' is not a valid Maximum BM Count -- enter a whole "
                            "number, or \"Unlimited\"."
                        ),
                        text_color=Color.ERROR,
                    )
                    return

            try:
                missed_value = int(missed_text)
            except ValueError:
                self.rbm_tiers_status_label.configure(
                    text=f"Row {i}: '{missed_text}' is not a valid Allowed Missed BMs value -- enter a whole number.",
                    text_color=Color.ERROR,
                )
                return

            parsed_tiers.append({"min": min_value, "max": max_value, "missed": missed_value})

        errors = validate_rbm_flag_tiers(parsed_tiers)
        if errors:
            self.rbm_tiers_status_label.configure(text=" ".join(errors), text_color=Color.ERROR)
            return

        set_rbm_flag_tiers(parsed_tiers)
        self._load_rbm_tiers()
        self.rbm_tiers_status_label.configure(
            text="Saved. Takes effect the next time a Manager Work Allocation report is uploaded.",
            text_color=Color.SUCCESS,
        )
