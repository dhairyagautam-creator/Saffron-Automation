"""Settings page: Email Settings, the (Developer-Mode-only) Hospital
Suppression key, and the Developer Mode unlock/entry point."""

from tkinter import messagebox

import customtkinter as ctk
from loguru import logger

from app.dev_auth_service import is_dev_password_set, set_dev_password, verify_dev_password
from app.email_settings_service import get_settings, save_settings
from app.geoapify_settings_service import get_geoapify_api_key, save_geoapify_api_key
from app.mode_state import is_developer_mode
from app.smtp_service import test_connection
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader
from ui.theme import Color, Font, Spacing


class SettingsPage(ctk.CTkFrame):
    """Email Settings, plus the Developer Mode gate. `main_window` is used to
    switch the whole app into/out of Developer Mode after unlocking."""

    def __init__(self, master, main_window=None) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self.main_window = main_window
        self._build_widgets()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(outer, "Settings", "Configure how Saffron Automation sends manager notifications").pack(
            anchor="w", pady=(0, Spacing.LG)
        )

        card = Card(outer)
        card.pack(fill="x")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Email Settings", font=Font.H2, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.MD))

        ctk.CTkLabel(
            body, text="Sender Gmail Address", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        self.sender_entry = ctk.CTkEntry(body, placeholder_text="you@gmail.com")
        self.sender_entry.pack(fill="x", pady=(4, Spacing.MD))

        ctk.CTkLabel(
            body, text="Gmail App Password", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        self.password_entry = ctk.CTkEntry(body, placeholder_text="16-character app password", show="*")
        self.password_entry.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(
            body,
            text=(
                "Generate this from your Google Account's App Passwords page — not your regular "
                "Gmail password."
            ),
            font=Font.SMALL,
            text_color=Color.TEXT_MUTED,
            anchor="w",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        self.automatic_switch = ctk.CTkSwitch(
            body,
            text="Enable Automatic Email Sending",
            font=Font.BODY,
            text_color=Color.TEXT_PRIMARY,
            progress_color=Color.PRIMARY,
        )
        self.automatic_switch.pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            body,
            text=(
                "When enabled, manager emails are sent automatically right after Run Analysis "
                "finishes. When off, use Send All Emails on the Email Center page instead."
            ),
            font=Font.SMALL,
            text_color=Color.TEXT_MUTED,
            anchor="w",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        ctk.CTkLabel(
            body,
            text=(
                "Master Report recipients (the consolidated email listing all flagged employees, in "
                "addition to each RBM's own email) are now managed on the Master Email Recipients page, "
                "not here — add, edit, or remove recipients there, each with their own Division filter."
            ),
            font=Font.SMALL,
            text_color=Color.TEXT_MUTED,
            anchor="w",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        button_row = ctk.CTkFrame(body, fg_color="transparent")
        button_row.pack(fill="x", pady=(0, Spacing.SM))

        self.save_button = PrimaryButton(button_row, text="Save Settings", command=self._on_save_clicked)
        self.save_button.pack(side="left", padx=(0, Spacing.SM))

        self.test_button = SecondaryButton(button_row, text="Test Connection", command=self._on_test_clicked)
        self.test_button.pack(side="left")

        self.result_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL_BOLD, anchor="w", wraplength=650, justify="left"
        )
        self.result_label.pack(anchor="w", pady=(Spacing.SM, 0))

        # --- Location Services (Geoapify) — always available, in both modes:
        #     it powers the precise landmark addresses shown in every email
        #     (and Hospital Suppression when that experimental feature is on).
        self._build_location_card(outer)

        # --- Developer Mode ------------------------------------------------
        self._build_developer_card(outer)

    def _build_location_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(Spacing.LG, 0))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Location Services", font=Font.H2, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.MD))

        ctk.CTkLabel(
            body, text="Geoapify API Key", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        self.geoapify_key_entry = ctk.CTkEntry(body, placeholder_text="Your Geoapify API key", show="*")
        self.geoapify_key_entry.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(
            body,
            text=(
                "Powers the precise, recognisable landmark addresses shown in every manager email "
                "(hospitals, malls, schools, petrol pumps, stations, named roads, and so on) — and "
                "Hospital Suppression when that feature is enabled. Get a free key at "
                "myprojects.geoapify.com. Without one, emails fall back to \"Address unavailable\"."
            ),
            font=Font.SMALL,
            text_color=Color.TEXT_MUTED,
            anchor="w",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        self.geoapify_save_button = PrimaryButton(
            body, text="Save Location Settings", command=self._on_geoapify_save_clicked
        )
        self.geoapify_save_button.pack(anchor="w", pady=(0, Spacing.SM))

        self.geoapify_result_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL_BOLD, anchor="w", wraplength=650, justify="left"
        )
        self.geoapify_result_label.pack(anchor="w")

    def _build_developer_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(Spacing.LG, 0))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Developer Mode", font=Font.H2, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            body,
            text=(
                "A separate, password-protected environment for testing experimental features "
                "(currently Hospital Suppression) without affecting production. Nothing done in "
                "Developer Mode reaches User Mode until you Publish."
            ),
            font=Font.SMALL,
            text_color=Color.TEXT_MUTED,
            anchor="w",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        self.dev_result_label = ctk.CTkLabel(body, text="", font=Font.SMALL_BOLD, anchor="w", wraplength=650, justify="left")

        if is_developer_mode():
            ctk.CTkLabel(
                body, text="Developer Mode is active.", font=Font.BODY_BOLD, text_color=Color.PRIMARY, anchor="w"
            ).pack(anchor="w", pady=(0, Spacing.SM))
            SecondaryButton(body, text="Exit Developer Mode", command=self._on_exit_clicked).pack(anchor="w")
        elif not is_dev_password_set():
            ctk.CTkLabel(
                body, text="Set a Developer Password", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, anchor="w"
            ).pack(anchor="w")
            self.dev_password_entry = ctk.CTkEntry(body, placeholder_text="Choose a password", show="*")
            self.dev_password_entry.pack(fill="x", pady=(4, Spacing.SM))
            PrimaryButton(body, text="Set Password & Enter Developer Mode", command=self._on_set_password_clicked).pack(anchor="w")
        else:
            ctk.CTkLabel(
                body, text="Enter Developer Password", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, anchor="w"
            ).pack(anchor="w")
            self.dev_password_entry = ctk.CTkEntry(body, placeholder_text="Developer password", show="*")
            self.dev_password_entry.pack(fill="x", pady=(4, Spacing.SM))
            PrimaryButton(body, text="Unlock Developer Mode", command=self._on_unlock_clicked).pack(anchor="w")

        self.dev_result_label.pack(anchor="w", pady=(Spacing.SM, 0))

    # --- Data --------------------------------------------------------------

    def on_show(self) -> None:
        self._load_settings()

    def _load_settings(self) -> None:
        settings = get_settings()
        self.sender_entry.delete(0, "end")
        self.sender_entry.insert(0, settings["sender_email"])
        self.password_entry.delete(0, "end")
        self.password_entry.insert(0, settings["app_password"])
        if settings["automatic_sending_enabled"]:
            self.automatic_switch.select()
        else:
            self.automatic_switch.deselect()
        self.result_label.configure(text="")

        self.geoapify_key_entry.delete(0, "end")
        self.geoapify_key_entry.insert(0, get_geoapify_api_key())
        self.geoapify_result_label.configure(text="")

    def _on_save_clicked(self) -> None:
        save_settings(
            self.sender_entry.get().strip(),
            self.password_entry.get().strip(),
            bool(self.automatic_switch.get()),
        )
        self.result_label.configure(text="Email settings saved successfully.", text_color=Color.SUCCESS)

    def _on_geoapify_save_clicked(self) -> None:
        save_geoapify_api_key(self.geoapify_key_entry.get().strip())
        self.geoapify_result_label.configure(
            text="Location settings saved successfully.", text_color=Color.SUCCESS
        )

    def _on_test_clicked(self) -> None:
        self._on_save_clicked()  # save first so the test reflects what's typed
        self.test_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        self.result_label.configure(text="Sending test email…", text_color=Color.TEXT_SECONDARY)
        self.update_idletasks()

        try:
            success, message = test_connection()
        except Exception as exc:
            logger.error(f"Test connection failed unexpectedly: {exc}")
            success, message = False, f"Unexpected error: {exc}"

        self.result_label.configure(text=message, text_color=Color.SUCCESS if success else Color.ERROR)
        self.test_button.configure(state="normal")
        self.save_button.configure(state="normal")

    # --- Developer Mode gate ----------------------------------------------

    def _enter_dev_mode(self) -> None:
        if self.main_window is not None:
            self.main_window.set_mode(True)  # rebuilds the whole shell

    def _on_set_password_clicked(self) -> None:
        password = self.dev_password_entry.get()
        if not password.strip():
            self.dev_result_label.configure(text="Please choose a password.", text_color=Color.ERROR)
            return
        set_dev_password(password)
        self._enter_dev_mode()

    def _on_unlock_clicked(self) -> None:
        if verify_dev_password(self.dev_password_entry.get()):
            self._enter_dev_mode()
        else:
            self.dev_result_label.configure(text="Incorrect password.", text_color=Color.ERROR)

    def _on_exit_clicked(self) -> None:
        if self.main_window is not None:
            self.main_window.set_mode(False)
