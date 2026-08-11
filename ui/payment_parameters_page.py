"""Payment Analytics Parameters page.

Two real, editable sections, both backed by
app/payment_parameters_service.py (never a hardcoded constant):

- Risk Scoring: Historical Payment Analytics' Green/Yellow/Orange/Red
  thresholds (app/payment_analytics_service.classify_risk()). All three
  boundaries are user-editable.
- Collections Action Center Parameters: the overdue-ageing buckets
  (app/collections_service.compute_days_and_status()). Green is
  deliberately NOT editable here -- it's the "Today <= Due Date"
  condition, not a day-count threshold, and that rule never changes. Only
  the Yellow/Orange maximum Days Over Due are editable; Red is always
  "anything past the Orange maximum."

Saving writes straight to the database via
app/payment_parameters_service.set_parameter() -- every place that reads
a threshold (get_dashboard_summary(), get_outstanding_invoices(), the
Customer Analytics/Critical Customers tables, ...) fetches fresh on its
own next on_show(), so a saved change takes effect the moment the user
navigates to any of those pages again. No restart, no re-upload.

Payment Terms and Notifications aren't used yet and were removed to avoid
implying settings that don't do anything. Both can come back in a future
release.
"""

import threading

import customtkinter as ctk
from loguru import logger

from app import sync_service
from app.payment_analytics_service import RISK_GREEN, RISK_ORANGE, RISK_RED, RISK_YELLOW
from app.payment_parameters_service import (
    COLLECTIONS_AGEING,
    HISTORICAL_RISK_SCORING,
    MODULE_KEY,
    get_full_configuration,
    get_parameters,
    set_parameter,
)
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader
from ui.theme import Color, Font, Spacing

HISTORICAL_SLIDERS = [
    ("green_max_days", "Green Maximum Days", "Average Payment Days below this value is Green.", 5, 90),
    ("yellow_max_days", "Yellow Maximum Days", "Average Payment Days below this value (and at/above Green) is Yellow.", 10, 120),
    ("orange_max_days", "Orange Maximum Days", "Average Payment Days below this value (and at/above Yellow) is Orange. At or above this is Red.", 15, 180),
]
COLLECTIONS_SLIDERS = [
    ("yellow_max_days", "Yellow Maximum Days Overdue", "Invoices this many days overdue (or fewer) are Yellow.", 1, 30),
    ("orange_max_days", "Orange Maximum Days Overdue", "Invoices this many days overdue (or fewer, and more than Yellow's maximum) are Orange. Beyond this is Red.", 2, 60),
]


class _ThresholdSection(Card):
    """One editable ageing/risk-scoring section: a slider per boundary, a
    live-updating resulting-ranges preview, and Save/Reset."""

    def __init__(self, master, title: str, description: str, rule_name: str, sliders: list[tuple], preview_fn, on_saved=None) -> None:
        super().__init__(master)
        self._rule_name = rule_name
        self._sliders = sliders
        self._preview_fn = preview_fn
        self._on_saved = on_saved
        self._controls: dict[str, dict] = {}

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(body, text=title, font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w").pack(anchor="w")
        ctk.CTkLabel(
            body, text=description, font=Font.BODY, text_color=Color.TEXT_SECONDARY, anchor="w",
            wraplength=800, justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        for i, (key, label, field_description, min_value, max_value) in enumerate(sliders):
            if i > 0:
                ctk.CTkFrame(body, fg_color=Color.DIVIDER, height=1).pack(fill="x", pady=Spacing.MD)
            self._build_slider(body, key, label, field_description, min_value, max_value)

        ctk.CTkFrame(body, fg_color=Color.DIVIDER, height=1).pack(fill="x", pady=Spacing.MD)

        ctk.CTkLabel(
            body, text="Resulting Classification", font=Font.SMALL_BOLD, text_color=Color.TEXT_MUTED, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.XS))
        self.preview_label = ctk.CTkLabel(
            body, text="", font=Font.BODY, text_color=Color.TEXT_PRIMARY, anchor="w", justify="left"
        )
        self.preview_label.pack(anchor="w", pady=(0, Spacing.MD))

        action_row = ctk.CTkFrame(body, fg_color="transparent")
        action_row.pack(fill="x")

        PrimaryButton(action_row, text="Save", command=self._on_save_clicked).pack(side="left", padx=(0, Spacing.SM))
        SecondaryButton(action_row, text="Reset", command=self._on_reset_clicked).pack(side="left")
        self.status_label = ctk.CTkLabel(action_row, text="", font=Font.SMALL_BOLD)
        self.status_label.pack(side="left", padx=(Spacing.MD, 0))

        self._load_values()

    def _build_slider(self, parent, key: str, label: str, description: str, min_value: int, max_value: int) -> None:
        ctk.CTkLabel(parent, text=label, font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, anchor="w").pack(anchor="w")
        ctk.CTkLabel(
            parent, text=description, font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w",
            wraplength=800, justify="left",
        ).pack(anchor="w", pady=(0, Spacing.SM))

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")

        slider = ctk.CTkSlider(
            row, from_=min_value, to=max_value, number_of_steps=max_value - min_value,
            progress_color=Color.PRIMARY, button_color=Color.PRIMARY, button_hover_color=Color.PRIMARY_HOVER,
            command=lambda value, k=key: self._on_slider_moved(k, value),
        )
        slider.pack(side="left", fill="x", expand=True, padx=(0, Spacing.MD))

        value_label = ctk.CTkLabel(row, text="—", font=Font.BODY_BOLD, text_color=Color.PRIMARY, width=90, anchor="e")
        value_label.pack(side="right")

        self._controls[key] = {"slider": slider, "value_label": value_label}

    def _on_slider_moved(self, key: str, value: float) -> None:
        rounded = round(value)
        self._controls[key]["value_label"].configure(text=f"{rounded} days")
        self._update_preview()

    def _current_values(self) -> dict[str, int]:
        return {key: round(control["slider"].get()) for key, control in self._controls.items()}

    def _update_preview(self) -> None:
        self.preview_label.configure(text=self._preview_fn(self._current_values()))

    def _load_values(self) -> None:
        stored = get_parameters(self._rule_name)
        for key, control in self._controls.items():
            value = round(float(stored[key]))
            control["slider"].set(value)
            control["value_label"].configure(text=f"{value} days")
        self._update_preview()
        self.status_label.configure(text="")

    def _validate(self, values: dict[str, int]) -> str | None:
        """Returns an error message if `values` isn't strictly ascending
        across the sliders (in the order they were declared), else None."""
        ordered = [values[key] for key, *_ in self._sliders]
        for earlier, later in zip(ordered, ordered[1:]):
            if later <= earlier:
                return "Each threshold must be greater than the one before it."
        return None

    def _on_save_clicked(self) -> None:
        values = self._current_values()
        error = self._validate(values)
        if error:
            self.status_label.configure(text=error, text_color=Color.ERROR)
            return
        for key, value in values.items():
            set_parameter(self._rule_name, key, str(value))
        self.status_label.configure(text="Saved", text_color=Color.SUCCESS)
        self._push_config_in_background()
        if self._on_saved is not None:
            self._on_saved()

    def _push_config_in_background(self) -> None:
        """Pushes Payment Analytics' entire cloud-synced configuration
        (both Risk Scoring and Collections Ageing sections together,
        since they share one cloud row -- see
        app/payment_parameters_service.py) after either section's Save
        button is clicked. Runs in the background so the save handler
        never blocks on network I/O."""

        def worker() -> None:
            try:
                result = sync_service.push_config(MODULE_KEY, get_full_configuration())
                if not result.success:
                    logger.warning(f"Failed to sync Payment Analytics parameters to the cloud: {result.error_message}")
            except Exception as exc:
                logger.error(f"Failed to sync Payment Analytics parameters to the cloud: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_reset_clicked(self) -> None:
        self._load_values()


def _historical_preview(values: dict[str, int]) -> str:
    green_max, yellow_max, orange_max = values["green_max_days"], values["yellow_max_days"], values["orange_max_days"]
    return (
        f"{RISK_GREEN} = below {green_max} days\n"
        f"{RISK_YELLOW} = {green_max}–{yellow_max - 1} days\n"
        f"{RISK_ORANGE} = {yellow_max}–{orange_max - 1} days\n"
        f"{RISK_RED} = {orange_max}+ days"
    )


def _collections_preview(values: dict[str, int]) -> str:
    yellow_max, orange_max = values["yellow_max_days"], values["orange_max_days"]
    return (
        "Green = not yet due\n"
        f"Yellow = 1–{yellow_max} days overdue\n"
        f"Orange = {yellow_max + 1}–{orange_max} days overdue\n"
        f"Red = {orange_max + 1}+ days overdue"
    )


class PaymentParametersPage(ctk.CTkFrame):
    """Risk Scoring (Historical Payment Analytics) and Collections Action
    Center Parameters -- both real, editable, and immediately effective."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._build_widgets()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Parameters", "Tune the ageing/risk thresholds used for colour coding across Payment Analytics"
        ).pack(anchor="w", pady=(0, Spacing.LG))

        _ThresholdSection(
            outer, "Risk Scoring",
            "Thresholds that determine a Historical Payment Analytics customer's risk category from their "
            "Average Payment Days. Each customer's risk category is calculated when a Historical or Monthly "
            "Report is processed, so a saved change here takes effect on the next upload -- it does not "
            "retroactively recolor customers already on screen.",
            HISTORICAL_RISK_SCORING, HISTORICAL_SLIDERS, _historical_preview,
        ).pack(fill="x", pady=(0, Spacing.LG))

        _ThresholdSection(
            outer, "Collections Action Center Parameters",
            "Thresholds for the Collections Action Center's overdue-ageing buckets. Green is not shown here -- "
            "it simply means today's date is on or before the invoice's Due Date, and that rule never changes. "
            "Only the Yellow and Orange maximums (in days overdue) are adjustable; Red is automatically "
            "everything past the Orange maximum.",
            COLLECTIONS_AGEING, COLLECTIONS_SLIDERS, _collections_preview,
        ).pack(fill="x")
