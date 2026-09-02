"""Review System -- Settings page.

Email Settings -- copied verbatim from ui/settings_page.py's own Email
Settings card (Path Validator's Settings page, 2026-08-19): same fields,
same app.email_settings_service/app.smtp_service backing, same Save/Test
behavior. There is exactly one email credential store in the application
(this is not Work Distribution's separate per-module credential set,
which was a deliberate one-off decision for that module) -- this page is
a second place to manage the same settings, not new email functionality.
"""

import customtkinter as ctk
from loguru import logger

from app.email_settings_service import get_settings, save_settings
from app.smtp_service import test_connection
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader
from ui.theme import Color, Font, Spacing


class ReviewSettingsPage(ctk.CTkFrame):
    """Email Settings for the Review System module."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._build_widgets()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(outer, "Settings", "Configure how Review System sends notifications").pack(
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
                "When enabled, notifications are sent automatically right after generation "
                "finishes. When off, send them manually instead."
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

    # --- Data ----------------------------------------------------------------

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

    def _on_save_clicked(self) -> None:
        save_settings(
            self.sender_entry.get().strip(),
            self.password_entry.get().strip(),
            bool(self.automatic_switch.get()),
        )
        self.result_label.configure(text="Email settings saved successfully.", text_color=Color.SUCCESS)

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
