"""Settings page: Email Settings and the Location Services (Geoapify) key."""

from tkinter import messagebox

import customtkinter as ctk
from loguru import logger

from app.email_settings_service import get_settings, save_settings
from app.geoapify_settings_service import get_geoapify_api_key, save_geoapify_api_key
from app.smtp_service import test_connection
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader
from ui.theme import Color, Font, Spacing


class SettingsPage(ctk.CTkFrame):
    """Email Settings and the Location Services key."""

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

        # --- Location Services (Geoapify): powers the precise landmark
        #     addresses shown in every email, and Hospital Suppression.
        self._build_location_card(outer)

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

    # --- Data --------------------------------------------------------------

    def on_show(self) -> None:
        self._load_settings()

    def _load_settings(self) -> None:
        settings = get_settings()
        self.sender_entry.delete(0, "end")
        self.sender_entry.insert(0, settings["sender_email"])
        self.password_entry.delete(0, "end")
        self.password_entry.insert(0, settings["app_password"])
        self.result_label.configure(text="")

        self.geoapify_key_entry.delete(0, "end")
        self.geoapify_key_entry.insert(0, get_geoapify_api_key())
        self.geoapify_result_label.configure(text="")

    def _on_save_clicked(self) -> None:
        save_settings(
            self.sender_entry.get().strip(),
            self.password_entry.get().strip(),
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
