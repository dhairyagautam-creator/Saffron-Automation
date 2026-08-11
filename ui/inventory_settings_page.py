"""Inventory Settings page: the replenishment threshold multiplier, the
CWH Threshold Multiplier, and the Threshold Display Mode
(app/threshold_service.py, app/cwh_service.py), all backed by
app/inventory_parameters_service.py.

Unlike every other editable parameter in this app, saving a new CFA or
CWH multiplier here deliberately does NOT retroactively recalculate any
existing InventoryThreshold/CwhStock row -- the CFA multiplier is only
read the next time a Previous Month Sales Report is uploaded (see
threshold_service.generate_thresholds_from_sales()); the CWH multiplier
is only read the next time an Inventory Report is uploaded (see
cwh_service.evaluate_cwh_stock()). Each save confirmation says so
explicitly, instead of the generic "Saved" every other Parameters page
shows, so neither is ever mistaken for an immediate, retroactive change.

The Threshold Display Mode is the opposite: a pure display preference
that takes effect immediately (next page reload), never touches any
stored threshold value -- so it gets its own ordinary "Saved" confirmation
and its own Save button, never conflated with either multiplier's
deferred behavior above.

CFA Threshold Multiplier input (Milestone 47 -- root cause fix): this
field used to be a `CTkSlider` quantized to fixed 0.1 steps across a
hardcoded 1.0-3.0 range (`MULTIPLIER_MIN/MAX/STEP` below, now removed).
That slider could ONLY ever land on one of 21 discrete values -- it was
physically incapable of representing 1.25, 2.25, or 2.75 (the nearest
reachable positions were 1.2/1.3 and 2.7/2.8), so ANY such value the user
tried to set was silently replaced by the nearest 0.1 multiple the moment
they moved the slider, before app.inventory_parameters_service.set_
threshold_multiplier() was ever called -- the backend calculation itself
(app.threshold_service.generate_thresholds_from_sales(), confirmed by
direct testing to correctly use whatever multiplier value it is given)
was never the problem. The CWH Threshold Multiplier's own field, right
below, was ALREADY a free-form `CTkEntry` for exactly this reason (see
its own code comment) -- it never had this bug. This field now uses the
identical free-form-entry pattern, so the CFA multiplier can represent
any exact positive decimal, same as CWH's always could. The CWH field
itself is completely unmodified.
"""

import threading

import customtkinter as ctk
from loguru import logger

from app import sync_service
from app.inventory_email_settings_service import get_settings as get_email_settings
from app.inventory_email_settings_service import save_settings as save_email_settings
from app.inventory_parameters_service import (
    DISPLAY_MODE_PACKS,
    DISPLAY_MODE_RAW,
    MODULE_KEY,
    get_cwh_threshold_multiplier,
    get_excess_transfer_candidate_multiplier,
    get_full_configuration,
    get_threshold_display_mode,
    get_threshold_multiplier,
    set_cwh_threshold_multiplier,
    set_excess_transfer_candidate_multiplier,
    set_threshold_display_mode,
    set_threshold_multiplier,
)
from app.smtp_service import test_connection
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader
from ui.theme import Color, Font, Spacing

MULTIPLIER_SAVE_CONFIRMATION = (
    "Changes will apply only to future analyses. Please re-upload and re-run the "
    "Monthly Sales Report and Inventory Report to generate new replenishment results "
    "using the updated threshold."
)

CWH_MULTIPLIER_SAVE_CONFIRMATION = (
    "Changes will apply only to future analyses. Please re-upload and re-run the "
    "Inventory Report to regenerate the Central Warehouse overview using the updated "
    "multiplier."
)

# Unlike the two confirmations above, the Excess Inventory Transfer
# Candidate multiplier is read live on every Excess Inventory page load
# (see app/excess_inventory_service.py), not just at upload time -- so this
# gets an ordinary immediate-effect confirmation, same as Threshold
# Display Mode below, not the "re-upload and re-run" caveat.
EXCESS_MULTIPLIER_SAVE_CONFIRMATION = "Saved. This takes effect immediately on the Excess Inventory page."

DISPLAY_MODE_LABELS = {DISPLAY_MODE_RAW: "Raw Quantity", DISPLAY_MODE_PACKS: "Packs"}
DISPLAY_MODE_VALUES = {label: value for value, label in DISPLAY_MODE_LABELS.items()}


def _format_multiplier(value: float) -> str:
    """'2' for a whole number, '2.25' otherwise -- so the entry never
    shows a trailing '.0' for the common case, but still shows exact
    decimals like the task's own '2.25' example."""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


class InventorySettingsPage(ctk.CTkFrame):
    """Inventory Threshold Multiplier and Threshold Display Mode -- the
    editable Inventory Monitoring parameters."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._build_widgets()

    def _build_widgets(self) -> None:
        # CTkScrollableFrame (Milestone 56) -- this page grew past the
        # available vertical space (Threshold Multiplier + CWH Multiplier +
        # Display Mode + Email Configuration below), compressing the Save
        # button and hiding the bottom of the page entirely with no way to
        # reach it. Same scrollable-container pattern already used
        # elsewhere for a tall settings-style page (see
        # ui/operations_page.py, ui/payment_collections_page.py) --
        # `fg_color`/padding unchanged, so this is a pure container swap,
        # not a restyle.
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Inventory Settings", "Tune the replenishment threshold calculation and display"
        ).pack(anchor="w", pady=(0, Spacing.LG))

        self._build_multiplier_card(outer)
        self._build_cwh_multiplier_card(outer)
        self._build_excess_multiplier_card(outer)
        self._build_display_mode_card(outer)
        self._build_email_settings_card(outer)

    # --- Inventory Threshold Multiplier -----------------------------------

    def _build_multiplier_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(0, Spacing.LG))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Inventory Threshold Multiplier", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "Replenishment threshold = Previous Month Sales × this multiplier. Changing "
                "this value does not affect any threshold or replenishment result already "
                "generated -- it only takes effect the next time the Previous Month Sales "
                "Report and Inventory Report are uploaded and re-run."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")

        self.multiplier_entry = ctk.CTkEntry(row, width=100)
        self.multiplier_entry.pack(side="left")
        ctk.CTkLabel(row, text="× Previous Month Sales", font=Font.BODY, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(Spacing.SM, 0)
        )

        action_row = ctk.CTkFrame(body, fg_color="transparent")
        action_row.pack(fill="x", pady=(Spacing.MD, 0))

        PrimaryButton(action_row, text="Save", command=self._on_multiplier_save_clicked).pack(
            side="left", padx=(0, Spacing.SM)
        )
        SecondaryButton(action_row, text="Reset", command=self._load_multiplier).pack(side="left")

        self.multiplier_status_label = ctk.CTkLabel(
            body,
            text="",
            font=Font.SMALL,
            text_color=Color.WARNING,
            anchor="w",
            wraplength=700,
            justify="left",
        )
        self.multiplier_status_label.pack(anchor="w", pady=(Spacing.SM, 0))

        self._load_multiplier()

    def _load_multiplier(self) -> None:
        value = get_threshold_multiplier()
        self.multiplier_entry.delete(0, "end")
        self.multiplier_entry.insert(0, _format_multiplier(value))
        self.multiplier_status_label.configure(text="", text_color=Color.WARNING)

    def _on_multiplier_save_clicked(self) -> None:
        raw = self.multiplier_entry.get().strip()
        try:
            value = float(raw)
            if value <= 0:
                raise ValueError("must be positive")
        except ValueError:
            self.multiplier_status_label.configure(
                text=f"'{raw}' is not a valid multiplier -- enter a positive number (e.g. 1.5 or 2.25).",
                text_color=Color.ERROR,
            )
            return

        set_threshold_multiplier(_format_multiplier(value))
        self.multiplier_entry.delete(0, "end")
        self.multiplier_entry.insert(0, _format_multiplier(value))
        self.multiplier_status_label.configure(text=MULTIPLIER_SAVE_CONFIRMATION, text_color=Color.WARNING)
        self._push_config_in_background()

    # --- CWH Threshold Multiplier ------------------------------------------
    # A plain numeric entry, not a slider like the CFA multiplier above --
    # the task's own examples (1.5, 2, 2.25, 3) include a value a 0.1-step
    # slider can't land on exactly, so free-form decimal entry is used
    # instead, with validation on save.

    def _build_cwh_multiplier_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(0, Spacing.LG))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="CWH Threshold Multiplier", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "CWH Threshold = Total Previous Month Sales (every CFA combined, excluding "
                "Ahmedabad CWH itself) × this multiplier. Changing this value does not affect "
                "any Central Warehouse result already generated -- it only takes effect the "
                "next time the Inventory Report is uploaded and re-run."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")

        self.cwh_multiplier_entry = ctk.CTkEntry(row, width=100)
        self.cwh_multiplier_entry.pack(side="left")
        ctk.CTkLabel(row, text="× Total Previous Month Sales", font=Font.BODY, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(Spacing.SM, 0)
        )

        action_row = ctk.CTkFrame(body, fg_color="transparent")
        action_row.pack(fill="x", pady=(Spacing.MD, 0))

        PrimaryButton(action_row, text="Save", command=self._on_cwh_multiplier_save_clicked).pack(
            side="left", padx=(0, Spacing.SM)
        )
        SecondaryButton(action_row, text="Reset", command=self._load_cwh_multiplier).pack(side="left")

        self.cwh_multiplier_status_label = ctk.CTkLabel(
            body,
            text="",
            font=Font.SMALL,
            text_color=Color.WARNING,
            anchor="w",
            wraplength=700,
            justify="left",
        )
        self.cwh_multiplier_status_label.pack(anchor="w", pady=(Spacing.SM, 0))

        self._load_cwh_multiplier()

    def _load_cwh_multiplier(self) -> None:
        value = get_cwh_threshold_multiplier()
        self.cwh_multiplier_entry.delete(0, "end")
        self.cwh_multiplier_entry.insert(0, _format_multiplier(value))
        self.cwh_multiplier_status_label.configure(text="", text_color=Color.WARNING)

    def _on_cwh_multiplier_save_clicked(self) -> None:
        raw = self.cwh_multiplier_entry.get().strip()
        try:
            value = float(raw)
            if value <= 0:
                raise ValueError("must be positive")
        except ValueError:
            self.cwh_multiplier_status_label.configure(
                text=f"'{raw}' is not a valid multiplier -- enter a positive number (e.g. 2 or 2.25).",
                text_color=Color.ERROR,
            )
            return

        set_cwh_threshold_multiplier(_format_multiplier(value))
        self.cwh_multiplier_entry.delete(0, "end")
        self.cwh_multiplier_entry.insert(0, _format_multiplier(value))
        self.cwh_multiplier_status_label.configure(text=CWH_MULTIPLIER_SAVE_CONFIRMATION, text_color=Color.WARNING)
        self._push_config_in_background()

    # --- Excess Inventory Settings ------------------------------------------
    # Same free-form-entry pattern as the CWH multiplier above (not a
    # slider -- see that field's own comment for why a slider can't
    # represent an exact decimal like 2.25).

    def _build_excess_multiplier_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(0, Spacing.LG))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Excess Inventory Settings", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "Transfer Candidate Multiplier -- on the Excess Inventory page, a product is "
                "highlighted as a strong transfer candidate when Current Stock ≥ this multiplier "
                "× Previous Month Sales. Unlike the two multipliers above, this is read live on "
                "every Excess Inventory page load, so a saved change applies immediately -- no "
                "re-upload needed."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")

        self.excess_multiplier_entry = ctk.CTkEntry(row, width=100)
        self.excess_multiplier_entry.pack(side="left")
        ctk.CTkLabel(row, text="× Previous Month Sales", font=Font.BODY, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(Spacing.SM, 0)
        )

        action_row = ctk.CTkFrame(body, fg_color="transparent")
        action_row.pack(fill="x", pady=(Spacing.MD, 0))

        PrimaryButton(action_row, text="Save", command=self._on_excess_multiplier_save_clicked).pack(
            side="left", padx=(0, Spacing.SM)
        )
        SecondaryButton(action_row, text="Reset", command=self._load_excess_multiplier).pack(side="left")

        self.excess_multiplier_status_label = ctk.CTkLabel(
            body,
            text="",
            font=Font.SMALL_BOLD,
            text_color=Color.SUCCESS,
            anchor="w",
            wraplength=700,
            justify="left",
        )
        self.excess_multiplier_status_label.pack(anchor="w", pady=(Spacing.SM, 0))

        self._load_excess_multiplier()

    def _load_excess_multiplier(self) -> None:
        value = get_excess_transfer_candidate_multiplier()
        self.excess_multiplier_entry.delete(0, "end")
        self.excess_multiplier_entry.insert(0, _format_multiplier(value))
        self.excess_multiplier_status_label.configure(text="")

    def _on_excess_multiplier_save_clicked(self) -> None:
        raw = self.excess_multiplier_entry.get().strip()
        try:
            value = float(raw)
            if value <= 0:
                raise ValueError("must be positive")
        except ValueError:
            self.excess_multiplier_status_label.configure(
                text=f"'{raw}' is not a valid multiplier -- enter a positive number (e.g. 2 or 2.25).",
                text_color=Color.ERROR,
            )
            return

        set_excess_transfer_candidate_multiplier(_format_multiplier(value))
        self.excess_multiplier_entry.delete(0, "end")
        self.excess_multiplier_entry.insert(0, _format_multiplier(value))
        self.excess_multiplier_status_label.configure(text=EXCESS_MULTIPLIER_SAVE_CONFIRMATION, text_color=Color.SUCCESS)
        self._push_config_in_background()

    # --- Threshold Display Mode --------------------------------------------

    def _build_display_mode_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(0, Spacing.LG))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Threshold Display Mode", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "How replenishment thresholds are shown on the Thresholds and Replenishment "
                "pages. Raw Quantity shows the threshold as a plain quantity (e.g. \"180\"); "
                "Packs shows how many complete packs that represents (e.g. \"180 (6 Packs)\"). "
                "This only changes how thresholds are displayed -- replenishment decisions "
                "always use the same underlying calculated threshold either way."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        self.display_mode_menu = ctk.CTkOptionMenu(
            body,
            values=[DISPLAY_MODE_LABELS[DISPLAY_MODE_RAW], DISPLAY_MODE_LABELS[DISPLAY_MODE_PACKS]],
            fg_color=Color.SURFACE,
            button_color=Color.PRIMARY,
            button_hover_color=Color.PRIMARY_HOVER,
            text_color=Color.TEXT_PRIMARY,
            width=170,
        )
        self.display_mode_menu.pack(anchor="w")

        action_row = ctk.CTkFrame(body, fg_color="transparent")
        action_row.pack(fill="x", pady=(Spacing.MD, 0))

        PrimaryButton(action_row, text="Save", command=self._on_display_mode_save_clicked).pack(
            side="left", padx=(0, Spacing.SM)
        )
        SecondaryButton(action_row, text="Reset", command=self._load_display_mode).pack(side="left")

        self.display_mode_status_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL_BOLD, text_color=Color.SUCCESS, anchor="w"
        )
        self.display_mode_status_label.pack(anchor="w", pady=(Spacing.SM, 0))

        self._load_display_mode()

    def _load_display_mode(self) -> None:
        mode = get_threshold_display_mode()
        self.display_mode_menu.set(DISPLAY_MODE_LABELS[mode])
        self.display_mode_status_label.configure(text="")

    def _on_display_mode_save_clicked(self) -> None:
        mode = DISPLAY_MODE_VALUES[self.display_mode_menu.get()]
        set_threshold_display_mode(mode)
        self.display_mode_status_label.configure(text="Saved")
        self._push_config_in_background()

    # --- Email Configuration ------------------------------------------------
    # Mirrors ui/settings_page.py's own "Email Settings" card as closely as
    # possible (Milestone 56) -- this is a deliberately SEPARATE credential
    # set from Path Validator's (see app/inventory_email_settings_service.py's
    # own docstring for why), reusing the exact same app/smtp_service.py
    # connection/send code via that module's optional sender_email/
    # app_password override parameters. No Master Report Email field here --
    # Inventory has no hierarchy-fallback "consolidated rollup" concept;
    # every configured recipient (see the new Automated Emails page) already
    # gets exactly the report they're configured for.

    def _build_email_settings_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="x")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Email Configuration", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "Used by the Automated Emails page to send Replenishment reports to "
                "configured recipients. This is a separate Gmail account from Path "
                "Validator's own Email Settings."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        ctk.CTkLabel(
            body, text="Sender Gmail Address", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        self.email_sender_entry = ctk.CTkEntry(body, placeholder_text="you@gmail.com")
        self.email_sender_entry.pack(fill="x", pady=(4, Spacing.MD))

        ctk.CTkLabel(
            body, text="Gmail App Password", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        self.email_password_entry = ctk.CTkEntry(body, placeholder_text="16-character app password", show="*")
        self.email_password_entry.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(
            body,
            text=(
                "Generate this from your Google Account's App Passwords page -- not your regular "
                "Gmail password."
            ),
            font=Font.SMALL,
            text_color=Color.TEXT_MUTED,
            anchor="w",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        self.email_automatic_switch = ctk.CTkSwitch(
            body,
            text="Enable Automatic Email Sending",
            font=Font.BODY,
            text_color=Color.TEXT_PRIMARY,
            progress_color=Color.PRIMARY,
        )
        self.email_automatic_switch.pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            body,
            text=(
                "When enabled, each configured recipient's Replenishment report is sent "
                "automatically right after an Inventory Report finishes processing. When off, "
                "recipients can still be configured here but no emails are sent automatically."
            ),
            font=Font.SMALL,
            text_color=Color.TEXT_MUTED,
            anchor="w",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        button_row = ctk.CTkFrame(body, fg_color="transparent")
        button_row.pack(fill="x", pady=(0, Spacing.SM))

        self.email_save_button = PrimaryButton(
            button_row, text="Save Settings", command=self._on_email_settings_save_clicked
        )
        self.email_save_button.pack(side="left", padx=(0, Spacing.SM))

        self.email_test_button = SecondaryButton(
            button_row, text="Test Connection", command=self._on_email_settings_test_clicked
        )
        self.email_test_button.pack(side="left")

        self.email_settings_result_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL_BOLD, anchor="w", wraplength=650, justify="left"
        )
        self.email_settings_result_label.pack(anchor="w", pady=(Spacing.SM, 0))

        self._load_email_settings()

    def _load_email_settings(self) -> None:
        settings = get_email_settings()
        self.email_sender_entry.delete(0, "end")
        self.email_sender_entry.insert(0, settings["sender_email"])
        self.email_password_entry.delete(0, "end")
        self.email_password_entry.insert(0, settings["app_password"])
        if settings["automatic_sending_enabled"]:
            self.email_automatic_switch.select()
        else:
            self.email_automatic_switch.deselect()
        self.email_settings_result_label.configure(text="")

    def _on_email_settings_save_clicked(self) -> None:
        save_email_settings(
            self.email_sender_entry.get().strip(),
            self.email_password_entry.get().strip(),
            bool(self.email_automatic_switch.get()),
        )
        self.email_settings_result_label.configure(text="Email settings saved successfully.", text_color=Color.SUCCESS)

    def _on_email_settings_test_clicked(self) -> None:
        # Mirrors ui/settings_page.py's own _on_test_clicked() exactly: save
        # first so the test reflects what's currently typed, then send a
        # real test email synchronously (test_connection() is a single quick
        # SMTP round trip, same as Path Validator's own Test Connection).
        self._on_email_settings_save_clicked()
        self.email_test_button.configure(state="disabled")
        self.email_save_button.configure(state="disabled")
        self.email_settings_result_label.configure(text="Sending test email…", text_color=Color.TEXT_SECONDARY)
        self.update_idletasks()

        sender_email = self.email_sender_entry.get().strip()
        app_password = self.email_password_entry.get().strip()

        try:
            success, message = test_connection(sender_email, app_password)
        except Exception as exc:
            logger.error(f"Inventory test connection failed unexpectedly: {exc}")
            success, message = False, f"Unexpected error: {exc}"

        self.email_settings_result_label.configure(text=message, text_color=Color.SUCCESS if success else Color.ERROR)
        self.email_test_button.configure(state="normal")
        self.email_save_button.configure(state="normal")

    # --- Cloud sync ------------------------------------------------------

    def _push_config_in_background(self) -> None:
        """Pushes Inventory's entire cloud-synced configuration (both
        parameters together, since they share one cloud row -- see
        app/inventory_parameters_service.py) after either Save button is
        clicked. Runs in the background so neither save handler blocks
        the UI on network I/O."""

        def worker() -> None:
            try:
                result = sync_service.push_config(MODULE_KEY, get_full_configuration())
                if not result.success:
                    logger.warning(f"Failed to sync Inventory parameters to the cloud: {result.error_message}")
            except Exception as exc:
                logger.error(f"Failed to sync Inventory parameters to the cloud: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    # --- Page lifecycle ------------------------------------------------------

    def on_show(self) -> None:
        """Called by InventoryModule whenever this page becomes visible --
        reload from the DB so a value saved elsewhere (or in a previous
        visit) is always reflected."""
        self._load_multiplier()
        self._load_cwh_multiplier()
        self._load_excess_multiplier()
        self._load_display_mode()
        self._load_email_settings()
