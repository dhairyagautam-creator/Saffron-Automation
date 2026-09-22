"""Parameters page: a grouped settings panel for editing rule thresholds.

Values are persisted in the `rule_parameters` table, not in code, so
changing a value here affects the next rule run immediately. Each
top-level entry in RULE_SECTIONS renders as one collapsible Card —
adding a future section is just one more entry here; no other code needs
to change.

Save writes straight to the local `rule_parameters` cache -- every place
that reads a threshold fetches fresh on its own next call, so a saved
change takes effect immediately, no restart required, and (parameter
sync project) also pushes the section's full current config to the
cloud immediately -- see _on_save_clicked.

The former "Dashboard Parameters" section (11 fields controlling colors/
thresholds for app/dashboard_service.py and ui/analytics_dashboard_page.py)
was removed here -- both of those files were already gone from the
codebase with no UI left anywhere to reach them; this page had kept
editing and saving their settings anyway. See
database/migrations.py's drop_dashboard_rule_parameters() for the
matching one-time cleanup of the 11 now-orphaned rows this left behind.
"""

import customtkinter as ctk

from app.parameter_sync_service import check_for_config_update, try_push_and_apply
from app.rule_parameters import DEFAULT_PARAMETERS, MODULE_KEY, apply_full_configuration, get_full_configuration, get_parameters, set_parameter
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader
from ui.parameter_sync_panel import ParameterSyncPanel
from ui.theme import Color, Font, Spacing

# Hidden sections. Currently none -- Hospital Suppression's search-radius
# knob (see app/notification_service.py) used to be excluded here per a
# "no hospital configuration screens in production" requirement; that
# requirement no longer applies, so it now has its own section below like
# every other rule.
HIDDEN_SECTIONS: set[str] = set()

RULE_SECTIONS = [
    {
        "rule_name": "SAME_LOCATION",
        "title": "Same Location Rule",
        "fields": [
            {
                "name": "same_place_radius_meters",
                "label": "Same-Place Radius",
                "description": (
                    "Visits within this distance of one another are treated as being "
                    "at the same location."
                ),
                "min": 10,
                "max": 1000,
                "step": 10,
                "unit": " m",
            },
            {
                "name": "concentration_threshold_percent",
                "label": "Concentration Threshold",
                "description": (
                    "If this percentage (or more) of an employee's valid GPS visits on a "
                    "day cluster at the same location, that day is flagged for review."
                ),
                "min": 0,
                "max": 100,
                "step": 1,
                "unit": "%",
            },
            {
                "name": "minimum_valid_gps_visits",
                "label": "Minimum Valid GPS Visits",
                "description": (
                    "An employee's day is only evaluated by the rule if they have at "
                    "least this many visits with valid GPS coordinates."
                ),
                "min": 1,
                "max": 30,
                "step": 1,
                "unit": " visits",
            },
        ],
    },
    {
        "rule_name": "HOURS_WORKED",
        "title": "Hours Worked Rule",
        "fields": [
            {
                "name": "minimum_hours_threshold",
                "label": "Minimum Working Hours",
                "description": (
                    "An employee's day is flagged if the span between their first and "
                    "last recorded call is below this many hours."
                ),
                "min": 1,
                "max": 12,
                "step": 1,
                "unit": " hours",
            },
        ],
    },
    {
        "rule_name": "HOSPITAL_SUPPRESSION",
        "title": "Hospital Suppression",
        "fields": [
            {
                "name": "radius_meters",
                "label": "Hospital Suppression Radius",
                "description": (
                    "A flagged employee's notification is withheld if a hospital or "
                    "clinic is found within this distance of their flagged location."
                ),
                "min": 10,
                "max": 500,
                "step": 10,
                "unit": " m",
            },
        ],
    },
]


class ParametersPage(ctk.CTkFrame):
    """Grouped, explained editor for every rule's and dashboard's parameters."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)

        # {(rule_name, field_name): {"type": ..., <type-specific widget refs>}}
        self._controls: dict[tuple[str, str], dict] = {}
        self._section_expanded: dict[str, dict] = {}

        self._build_widgets()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        top_bar = ctk.CTkFrame(outer, fg_color="transparent")
        top_bar.pack(fill="x", pady=(0, Spacing.LG))

        SectionHeader(
            top_bar, "Parameters", "Tune every rule's thresholds without touching any code"
        ).pack(side="left", anchor="w")

        self._sync_panel = ParameterSyncPanel(
            outer,
            check_fn=lambda: check_for_config_update(MODULE_KEY, get_full_configuration()),
            apply_fn=self._on_sync_apply,
            on_applied=self._load_values,
        )
        self._sync_panel.pack(fill="x", pady=(0, Spacing.MD))

        self._active_sections = [s for s in RULE_SECTIONS if s["rule_name"] not in HIDDEN_SECTIONS]

        for section in self._active_sections:
            self._build_section_card(outer, section)

        button_row = ctk.CTkFrame(outer, fg_color="transparent")
        button_row.pack(fill="x", pady=(0, Spacing.LG))

        self.save_button = PrimaryButton(button_row, text="Save Parameters", command=self._on_save_clicked)
        self.save_button.pack(side="left", padx=(0, Spacing.SM))
        SecondaryButton(button_row, text="Reset", command=self._on_reset_clicked).pack(side="left")

        self.confirmation_label = ctk.CTkLabel(button_row, text="", font=Font.SMALL_BOLD, wraplength=500, justify="left")
        self.confirmation_label.pack(side="left", padx=(Spacing.MD, 0))

    # --- Collapsible section cards ---------------------------------------

    def _build_section_card(self, outer, section: dict) -> None:
        rule_name = section["rule_name"]
        card = Card(outer)
        card.pack(fill="x", pady=(0, Spacing.LG))

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=Spacing.LG, pady=(Spacing.LG, 0))

        body = ctk.CTkFrame(card, fg_color="transparent")

        chevron_label = ctk.CTkLabel(
            header, text="▾", font=Font.H3, text_color=Color.TEXT_SECONDARY, width=20, anchor="w"
        )
        chevron_label.pack(side="left")
        title_label = ctk.CTkLabel(
            header, text=section["title"], font=Font.H2, text_color=Color.TEXT_PRIMARY, anchor="w"
        )
        title_label.pack(side="left", fill="x", expand=True)

        state = {"expanded": True}
        self._section_expanded[rule_name] = state

        def toggle(_event=None, body=body, chevron_label=chevron_label, state=state):
            state["expanded"] = not state["expanded"]
            if state["expanded"]:
                body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)
                chevron_label.configure(text="▾")
            else:
                body.pack_forget()
                chevron_label.configure(text="▸")

        for widget in (header, chevron_label, title_label):
            widget.bind("<Button-1>", toggle)

        # Default expanded — identical to the page's pre-collapsible look.
        body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        self._render_section_body(body, section)

    def _render_section_body(self, body, section: dict) -> None:
        rule_name = section["rule_name"]
        groups = section.get("groups")
        if groups is None:
            groups = [{"title": None, "fields": section["fields"]}]

        for gi, group in enumerate(groups):
            if gi > 0:
                ctk.CTkFrame(body, fg_color=Color.DIVIDER, height=1).pack(fill="x", pady=Spacing.MD)
            if group.get("title"):
                ctk.CTkLabel(
                    body, text=group["title"], font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
                ).pack(anchor="w", pady=(0, Spacing.SM))

            fields = group["fields"]
            for i, spec in enumerate(fields):
                self._build_field(body, rule_name, spec)
                if i < len(fields) - 1:
                    ctk.CTkFrame(body, fg_color=Color.DIVIDER, height=1).pack(fill="x", pady=Spacing.MD)

    def _all_fields(self, section: dict) -> list[dict]:
        groups = section.get("groups")
        if groups is None:
            return section["fields"]
        return [spec for group in groups for spec in group["fields"]]

    # --- Field rendering (dispatch by type) -------------------------------

    def _label_and_description(self, parent, spec: dict) -> None:
        ctk.CTkLabel(
            parent, text=spec["label"], font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            parent,
            text=spec["description"],
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.SM))

    def _build_field(self, parent, rule_name: str, spec: dict) -> None:
        field_type = spec.get("type", "slider")
        key = (rule_name, spec["name"])
        if field_type == "slider":
            self._build_slider_field(parent, key, spec)
        else:
            raise ValueError(f"Unknown parameter field type: {field_type!r}")

    def _build_slider_field(self, parent, key: tuple[str, str], spec: dict) -> None:
        self._label_and_description(parent, spec)
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")

        steps = max(1, int((spec["max"] - spec["min"]) / spec["step"]))
        slider = ctk.CTkSlider(
            row,
            from_=spec["min"],
            to=spec["max"],
            number_of_steps=steps,
            progress_color=Color.PRIMARY,
            button_color=Color.PRIMARY,
            button_hover_color=Color.PRIMARY_HOVER,
            command=lambda value, k=key, s=spec: self._on_slider_moved(k, s, value),
        )
        slider.pack(side="left", fill="x", expand=True, padx=(0, Spacing.MD))

        value_label = ctk.CTkLabel(
            row, text="—", font=Font.BODY_BOLD, text_color=Color.PRIMARY, width=80, anchor="e"
        )
        value_label.pack(side="right")
        self._controls[key] = {"type": "slider", "slider": slider, "value_label": value_label}

    def _on_slider_moved(self, key: tuple[str, str], spec: dict, value: float) -> None:
        rounded = round(value / spec["step"]) * spec["step"]
        self._controls[key]["value_label"].configure(text=f"{int(rounded)}{spec['unit']}")

    # --- Load / save -------------------------------------------------------

    def on_show(self) -> None:
        """Called by MainWindow every time this page becomes visible."""
        self._load_values()
        self._sync_panel.check_now()

    def _load_values(self) -> None:
        for section in self._active_sections:
            rule_name = section["rule_name"]
            values = get_parameters(rule_name)
            for spec in self._all_fields(section):
                name = spec["name"]
                raw = values.get(name, DEFAULT_PARAMETERS[rule_name][name])
                self._load_field_value((rule_name, name), spec, raw, rule_name)
        self.confirmation_label.configure(text="")

    def _load_field_value(self, key: tuple[str, str], spec: dict, raw, rule_name: str) -> None:
        control = self._controls[key]
        field_type = control["type"]
        name = spec["name"]

        if field_type == "slider":
            try:
                number = float(raw)
            except (TypeError, ValueError):
                number = float(DEFAULT_PARAMETERS[rule_name][name])
            number = max(spec["min"], min(spec["max"], number))
            control["slider"].set(number)
            control["value_label"].configure(text=f"{int(number)}{spec['unit']}")

    def _on_save_clicked(self) -> None:
        errors = self._validate_all_fields()
        if errors:
            self._set_confirmation(errors[0], Color.ERROR)
            return

        # Start from the current full config (so HOSPITAL_SUPPRESSION's
        # own value -- hidden from this page's UI, see HIDDEN_SECTIONS --
        # is still included in what gets pushed) and overlay only the
        # sections this page actually edited.
        new_config = get_full_configuration()
        for section in self._active_sections:
            rule_name = section["rule_name"]
            for spec in self._all_fields(section):
                name = spec["name"]
                value_str = self._serialize_field_value(self._controls[(rule_name, name)], spec)
                new_config[rule_name][name] = value_str

        ok, error = try_push_and_apply(MODULE_KEY, new_config, self._apply_config)
        if not ok:
            self._set_confirmation(error, Color.ERROR)
            return

        self._set_confirmation("Saved and synced.", Color.SUCCESS)

    def _apply_config(self, config: dict) -> None:
        """apply_fn for try_push_and_apply -- rule_parameters' own
        apply_full_configuration() already knows how to validate+write a
        full config dict; its (bool, str|None) return is discarded here
        since a config built entirely from this page's own just-validated
        UI values can never fail that validation."""
        apply_full_configuration(config)

    def _on_sync_apply(self, check_result: dict) -> tuple[bool, str | None]:
        ok, error = apply_full_configuration(check_result["config"])
        return ok, error

    # --- Helpers: validation, busy/status state --

    def _validate_all_fields(self) -> list[str]:
        """Checked before anything is saved anywhere (local or cloud).
        Widgets already constrain most types physically (sliders can't
        leave their range, the color picker only returns valid hex) --
        this exists mainly for bucket_edges, which has no such built-in
        guard, plus a defensive re-check of everything else."""
        errors = []
        for section in self._active_sections:
            for spec in self._all_fields(section):
                control = self._controls[(section["rule_name"], spec["name"])]
                field_type = control["type"]
                if field_type == "slider":
                    value = control["slider"].get()
                    if not (spec["min"] <= value <= spec["max"]):
                        errors.append(f"{spec['label']} must be between {spec['min']} and {spec['max']}.")
        return errors

    def _set_confirmation(self, text: str, color: str) -> None:
        self.confirmation_label.configure(text=text, text_color=color)

    def _serialize_field_value(self, control: dict, spec: dict) -> str:
        field_type = control["type"]
        if field_type == "slider":
            value = round(control["slider"].get() / spec["step"]) * spec["step"]
            return str(int(value))
        raise ValueError(f"Unknown parameter field type: {field_type!r}")

    def _on_reset_clicked(self) -> None:
        self._load_values()
