"""Parameters page: a grouped settings panel for editing rule thresholds and
dashboard display settings.

Values are persisted in the `rule_parameters` table, not in code, so
changing a value here affects the next rule run / dashboard render
immediately. Each top-level entry in RULE_SECTIONS renders as one
collapsible Card — adding a future section (e.g. "Monthly Review
Parameters") is just one more entry here; no other code needs to change.

Version 2.0, Milestone 9: Save pushes CLOUD_SYNCED_RULE_NAMES (see
app/rule_parameters.py) to Supabase before writing the local cache --
cloud-first, matching "Supabase is the source of truth". All Supabase
calls go through the generic app/sync_service.py; this file only knows
"push a dict," never anything about how that dict reaches Supabase.

Version 2.0, Milestone 20: this page no longer has its own "Refresh"
button -- pulling the latest cloud config down is now the module-wide
Refresh action's job (see app/path_validator_refresh.py,
ui/path_validator_module.py), which re-renders this page via on_show()
exactly like every other Path Validator page, with no per-page pull logic
duplicated here.
"""

import re
import threading
from tkinter import colorchooser

import customtkinter as ctk

from app import rule_parameters, sync_service
from app.mode_state import is_developer_mode
from app.rule_parameters import DEFAULT_PARAMETERS, get_parameters, set_parameter
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader
from ui.theme import Color, Font, Spacing

# Sections shown only in Developer Mode, hidden from production. Hospital
# Suppression is an always-on production pipeline stage as of v1.3 (see
# app/notification_service.py), but per the "no hospital configuration
# screens in production" requirement its search-radius knob stays visible
# only in Developer Mode; production silently uses the 100m default from
# app/rule_parameters.DEFAULT_PARAMETERS["HOSPITAL_SUPPRESSION"].
DEVELOPER_ONLY_SECTIONS = {"HOSPITAL_SUPPRESSION"}

DIVISION_SORT_OPTIONS = [
    ("employees_flagged", "Employees Flagged"),
    ("flag_rate", "Flag Rate"),
    ("employees_analysed", "Employees Analysed"),
]

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
    {
        "rule_name": "DASHBOARD",
        "title": "Dashboard Parameters",
        "groups": [
            {
                "title": "Compliance Thresholds",
                "fields": [
                    {
                        "name": "compliance_high_threshold",
                        "label": "Compliance High Threshold",
                        "description": (
                            "Today's Compliance % badge on the Analytics Dashboard shows "
                            "green at or above this percentage."
                        ),
                        "type": "slider",
                        "min": 0,
                        "max": 100,
                        "step": 1,
                        "unit": "%",
                    },
                    {
                        "name": "compliance_mid_threshold",
                        "label": "Compliance Mid Threshold",
                        "description": (
                            "Below the high threshold but at or above this percentage, "
                            "the badge shows yellow. Below this, it shows red."
                        ),
                        "type": "slider",
                        "min": 0,
                        "max": 100,
                        "step": 1,
                        "unit": "%",
                    },
                ],
            },
            {
                "title": "Heat Map Colours",
                "fields": [
                    {
                        "name": "map_color_no_presence",
                        "label": "No Operational Presence",
                        "description": (
                            "Map color for a state with zero employees analysed today -- i.e. Saffron "
                            "has no branches or staff there. Kept distinct from 'No Findings' so the "
                            "map never implies a state was checked and found clean when it simply "
                            "wasn't part of today's run."
                        ),
                        "type": "color",
                    },
                    {
                        "name": "map_color_no_findings",
                        "label": "No Findings",
                        "description": (
                            "Map color for a state where employees WERE analysed today but none were "
                            "flagged."
                        ),
                        "type": "color",
                    },
                    {
                        "name": "map_color_low",
                        "label": "Low Findings",
                        "description": "Map color for a state at or below the Low Tier Max, below.",
                        "type": "color",
                    },
                    {
                        "name": "map_color_moderate",
                        "label": "Moderate Findings",
                        "description": (
                            "Map color for a state above the Low Tier Max and at or below "
                            "the Moderate Tier Max, below."
                        ),
                        "type": "color",
                    },
                    {
                        "name": "map_color_high",
                        "label": "High Findings",
                        "description": "Map color for a state above the Moderate Tier Max.",
                        "type": "color",
                    },
                    {
                        "name": "map_tier_low_max",
                        "label": "Low Tier Max",
                        "description": (
                            "States with this many flagged employees or fewer use the "
                            "Low Findings color."
                        ),
                        "type": "slider",
                        "min": 0,
                        "max": 50,
                        "step": 1,
                        "unit": " employees",
                    },
                    {
                        "name": "map_tier_moderate_max",
                        "label": "Moderate Tier Max",
                        "description": (
                            "States above the Low Tier Max with this many flagged employees "
                            "or fewer use the Moderate Findings color. Above this uses High."
                        ),
                        "type": "slider",
                        "min": 0,
                        "max": 50,
                        "step": 1,
                        "unit": " employees",
                    },
                ],
            },
            {
                "title": "Division Performance",
                "fields": [
                    {
                        "name": "division_sort_mode",
                        "label": "Sort Divisions By",
                        "description": (
                            "Controls the order divisions are shown in on the Division "
                            "Performance chart."
                        ),
                        "type": "choice",
                        "options": DIVISION_SORT_OPTIONS,
                    },
                ],
            },
            {
                "title": "Cluster Distribution Buckets",
                "fields": [
                    {
                        "name": "cluster_bucket_edges",
                        "label": "Bucket Boundaries",
                        "description": (
                            "Ascending percentage boundaries defining the buckets shown on "
                            "the Cluster Activity Distribution chart (e.g. 30, 40, 50 ... "
                            "creates buckets 30-40%, 40-50%, ...)."
                        ),
                        "type": "bucket_edges",
                        "count": 8,
                    },
                ],
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
            top_bar, "Parameters", "Tune every rule's thresholds and dashboard settings without touching any code"
        ).pack(side="left", anchor="w")

        # Developer-Mode-only sections (e.g. Hospital Suppression's radius
        # knob) are hidden from production — see DEVELOPER_ONLY_SECTIONS.
        self._active_sections = [
            s for s in RULE_SECTIONS
            if s["rule_name"] not in DEVELOPER_ONLY_SECTIONS or is_developer_mode()
        ]

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
        elif field_type == "color":
            self._build_color_field(parent, key, spec)
        elif field_type == "choice":
            self._build_choice_field(parent, key, spec)
        elif field_type == "bucket_edges":
            self._build_bucket_edges_field(parent, key, spec)
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

    def _build_color_field(self, parent, key: tuple[str, str], spec: dict) -> None:
        self._label_and_description(parent, spec)
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")

        swatch = ctk.CTkButton(
            row,
            text="",
            width=48,
            height=28,
            corner_radius=6,
            fg_color=Color.BORDER,
            hover_color=Color.BORDER,
            border_width=1,
            border_color=Color.BORDER,
            command=lambda k=key: self._on_color_swatch_clicked(k),
        )
        swatch.pack(side="left")

        hex_label = ctk.CTkLabel(row, text="", font=Font.BODY_BOLD, text_color=Color.TEXT_SECONDARY)
        hex_label.pack(side="left", padx=(Spacing.SM, 0))

        self._controls[key] = {
            "type": "color",
            "swatch": swatch,
            "hex_label": hex_label,
            "value": Color.BORDER,
        }

    def _on_color_swatch_clicked(self, key: tuple[str, str]) -> None:
        control = self._controls[key]
        _rgb, hex_value = colorchooser.askcolor(color=control["value"], title="Choose a color")
        if not hex_value:
            return
        control["value"] = hex_value
        control["swatch"].configure(fg_color=hex_value, hover_color=hex_value)
        control["hex_label"].configure(text=hex_value.upper())

    def _build_choice_field(self, parent, key: tuple[str, str], spec: dict) -> None:
        self._label_and_description(parent, spec)
        options = spec["options"]
        labels = [label for _value, label in options]

        menu = ctk.CTkOptionMenu(
            parent,
            values=labels,
            fg_color=Color.SURFACE,
            button_color=Color.PRIMARY,
            button_hover_color=Color.PRIMARY_HOVER,
            text_color=Color.TEXT_PRIMARY,
        )
        menu.pack(anchor="w")
        self._controls[key] = {"type": "choice", "widget": menu, "options": options}

    def _build_bucket_edges_field(self, parent, key: tuple[str, str], spec: dict) -> None:
        self._label_and_description(parent, spec)
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")

        entries = []
        count = spec.get("count", 8)
        for i in range(count):
            if i > 0:
                ctk.CTkLabel(row, text="–", text_color=Color.TEXT_MUTED, width=12).pack(side="left")
            entry = ctk.CTkEntry(row, width=55, justify="center")
            entry.pack(side="left")
            entries.append(entry)

        self._controls[key] = {"type": "bucket_edges", "entries": entries}

    # --- Load / save -------------------------------------------------------

    def on_show(self) -> None:
        """Called by MainWindow every time this page becomes visible."""
        self._load_values()

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
        elif field_type == "color":
            hex_value = raw or DEFAULT_PARAMETERS[rule_name][name]
            control["value"] = hex_value
            control["swatch"].configure(fg_color=hex_value, hover_color=hex_value)
            control["hex_label"].configure(text=hex_value.upper())
        elif field_type == "choice":
            value_to_label = dict(control["options"])
            control["widget"].set(value_to_label.get(raw, control["options"][0][1]))
        elif field_type == "bucket_edges":
            parts = [p.strip() for p in str(raw).split(",")] if raw else []
            for i, entry in enumerate(control["entries"]):
                entry.delete(0, "end")
                if i < len(parts):
                    entry.insert(0, parts[i])

    def _on_save_clicked(self) -> None:
        errors = self._validate_all_fields()
        if errors:
            self._set_confirmation(errors[0], Color.ERROR)
            return

        # Sections outside the cloud-synced set (Hospital Suppression,
        # Developer Mode only) save straight to the local cache exactly as
        # before -- Developer Mode data is deliberately never shared
        # across machines (see app/rule_parameters.py's module docstring),
        # so it never goes through the cloud-first save path below.
        for section in self._active_sections:
            rule_name = section["rule_name"]
            if rule_name in rule_parameters.CLOUD_SYNCED_RULE_NAMES:
                continue
            for spec in self._all_fields(section):
                name = spec["name"]
                value_str = self._serialize_field_value(self._controls[(rule_name, name)], spec)
                set_parameter(rule_name, name, value_str)

        cloud_config = self._collect_cloud_config()
        if not cloud_config:
            self._set_confirmation("Saved.", Color.SUCCESS)
            return

        self._set_busy(True, saving=True)

        def worker() -> None:
            result = sync_service.push_config(rule_parameters.MODULE_KEY, cloud_config)
            self.after(0, self._on_save_sync_complete, cloud_config, result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_save_sync_complete(self, cloud_config: dict, result) -> None:
        self._set_busy(False)
        if not result.success:
            self._set_confirmation(f"Could not save to the cloud: {result.error_message}", Color.ERROR)
            return
        # Step 3 (per the spec: validate -> save to Supabase -> update the
        # local cache): only write the cache once Supabase has confirmed
        # the save, so the cache never reflects an edit the cloud rejected.
        rule_parameters.apply_full_configuration(cloud_config)
        self._set_confirmation("Saved and synced to the cloud.", Color.SUCCESS)

    # --- Helpers: validation, cloud config collection, busy/status state --

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
                elif field_type == "color":
                    if not re.match(r"^#[0-9A-Fa-f]{6}$", control["value"] or ""):
                        errors.append(f"{spec['label']} must be a valid color.")
                elif field_type == "choice":
                    label_to_value = {label: value for value, label in control["options"]}
                    if control["widget"].get() not in label_to_value:
                        errors.append(f"{spec['label']} has an invalid selection.")
                elif field_type == "bucket_edges":
                    raw_values = [entry.get().strip() for entry in control["entries"]]
                    if any(not value for value in raw_values):
                        errors.append(f"{spec['label']}: all {len(raw_values)} boundary values are required.")
                    else:
                        try:
                            numbers = [float(value) for value in raw_values]
                        except ValueError:
                            errors.append(f"{spec['label']} values must all be numbers.")
                        else:
                            if numbers != sorted(numbers):
                                errors.append(f"{spec['label']} values must be in ascending order.")
        return errors

    def _collect_cloud_config(self) -> dict:
        """Assembles the current widget values for every cloud-synced
        section into {rule_name: {parameter_name: value_str}} -- exactly
        the shape app.rule_parameters.apply_full_configuration() expects
        back, and what app.sync_service.push_config() uploads as-is."""
        config = {}
        for section in self._active_sections:
            rule_name = section["rule_name"]
            if rule_name not in rule_parameters.CLOUD_SYNCED_RULE_NAMES:
                continue
            config[rule_name] = {
                spec["name"]: self._serialize_field_value(self._controls[(rule_name, spec["name"])], spec)
                for spec in self._all_fields(section)
            }
        return config

    def _set_busy(self, busy: bool, saving: bool = True) -> None:
        self.save_button.configure(
            state="disabled" if busy else "normal", text="Saving..." if busy and saving else "Save Parameters"
        )

    def _set_confirmation(self, text: str, color: str) -> None:
        self.confirmation_label.configure(text=text, text_color=color)

    def _serialize_field_value(self, control: dict, spec: dict) -> str:
        field_type = control["type"]
        if field_type == "slider":
            value = round(control["slider"].get() / spec["step"]) * spec["step"]
            return str(int(value))
        if field_type == "color":
            return control["value"]
        if field_type == "choice":
            label_to_value = {label: value for value, label in control["options"]}
            return label_to_value.get(control["widget"].get(), control["options"][0][0])
        if field_type == "bucket_edges":
            return ",".join(entry.get().strip() for entry in control["entries"])
        raise ValueError(f"Unknown parameter field type: {field_type!r}")

    def _on_reset_clicked(self) -> None:
        self._load_values()
