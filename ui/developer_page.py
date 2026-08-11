"""Developer page — visible only in Developer Mode.

Holds the experimental feature toggles (currently Hospital Suppression),
the Change Developer Password control, and the Publish to User Mode button
that promotes the whole Developer environment into production. See
app/feature_flags_service.py, app/dev_auth_service.py, app/publish_service.py.
"""

from tkinter import messagebox

import customtkinter as ctk

from app.dev_auth_service import set_dev_password
from app.feature_flags_service import DEFAULT_FLAGS, get_flags, set_feature_enabled
from app.mode_state import DEVELOPER_ENVIRONMENT
from app.publish_service import publish_developer_to_user
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader
from ui.theme import Color, Font, Spacing

# Human-readable labels for feature flags shown on this page.
FLAG_LABELS = {
    "hospital_suppression": "Hospital Suppression",
}


class DeveloperPage(ctk.CTkFrame):
    """`main_window` is used to refresh the shell when a flag changes (so
    gated sections appear/disappear) and after publishing."""

    def __init__(self, master, main_window=None) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self.main_window = main_window
        self._flag_switches: dict[str, ctk.CTkSwitch] = {}
        self._build_widgets()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "Developer", "Experimental features and configuration — isolated from production"
        ).pack(anchor="w", pady=(0, Spacing.LG))

        # Warning banner reminder within the page.
        warn = ctk.CTkFrame(outer, fg_color=Color.WARNING_SOFT, corner_radius=8)
        warn.pack(fill="x", pady=(0, Spacing.LG))
        ctk.CTkLabel(
            warn,
            text=(
                "Experimental Mode — changes you make here (settings, parameters, feature flags) "
                "do not affect Production until you press Publish to User Mode."
            ),
            font=Font.SMALL_BOLD,
            text_color=Color.WARNING,
            anchor="w",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", padx=Spacing.MD, pady=Spacing.SM)

        # --- Feature flags -------------------------------------------------
        flags_card = Card(outer)
        flags_card.pack(fill="x", pady=(0, Spacing.LG))
        flags_body = ctk.CTkFrame(flags_card, fg_color="transparent")
        flags_body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            flags_body, text="Experimental Features", font=Font.H2, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))
        ctk.CTkLabel(
            flags_body,
            text="Toggle features on or off in Developer Mode. Production is unaffected until you publish.",
            font=Font.SMALL,
            text_color=Color.TEXT_MUTED,
            anchor="w",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        dev_flags = get_flags(DEVELOPER_ENVIRONMENT)
        for flag_name in DEFAULT_FLAGS:
            switch = ctk.CTkSwitch(
                flags_body,
                text=FLAG_LABELS.get(flag_name, flag_name),
                font=Font.BODY,
                text_color=Color.TEXT_PRIMARY,
                progress_color=Color.PRIMARY,
                command=lambda n=flag_name: self._on_flag_toggled(n),
            )
            if dev_flags.get(flag_name):
                switch.select()
            else:
                switch.deselect()
            switch.pack(anchor="w", pady=4)
            self._flag_switches[flag_name] = switch

        # --- Change password -----------------------------------------------
        pw_card = Card(outer)
        pw_card.pack(fill="x", pady=(0, Spacing.LG))
        pw_body = ctk.CTkFrame(pw_card, fg_color="transparent")
        pw_body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            pw_body, text="Change Developer Password", font=Font.H2, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))
        self.new_password_entry = ctk.CTkEntry(pw_body, placeholder_text="New password", show="*")
        self.new_password_entry.pack(fill="x", pady=(0, Spacing.SM))
        PrimaryButton(pw_body, text="Change Password", command=self._on_change_password_clicked).pack(anchor="w")
        self.pw_result_label = ctk.CTkLabel(pw_body, text="", font=Font.SMALL_BOLD, anchor="w")
        self.pw_result_label.pack(anchor="w", pady=(Spacing.SM, 0))

        # --- Publish -------------------------------------------------------
        publish_card = Card(outer)
        publish_card.pack(fill="x")
        publish_body = ctk.CTkFrame(publish_card, fg_color="transparent")
        publish_body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            publish_body, text="Publish to User Mode", font=Font.H2, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))
        ctk.CTkLabel(
            publish_body,
            text=(
                "Promote the Developer environment into Production. This overwrites the current "
                "User Mode settings, rule parameters, and feature flags with your Developer versions."
            ),
            font=Font.SMALL,
            text_color=Color.TEXT_MUTED,
            anchor="w",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(0, Spacing.MD))
        PrimaryButton(publish_body, text="Publish to User Mode", command=self._on_publish_clicked).pack(anchor="w")

    def on_show(self) -> None:
        pass  # nothing time-sensitive to reload

    # --- Handlers ----------------------------------------------------------

    def _on_flag_toggled(self, flag_name: str) -> None:
        enabled = bool(self._flag_switches[flag_name].get())
        set_feature_enabled(flag_name, enabled)
        # Rebuild the shell so gated sections (Parameters/Settings/Email
        # Center) show or hide immediately for this mode.
        if self.main_window is not None:
            self.main_window.rebuild_shell()

    def _on_change_password_clicked(self) -> None:
        password = self.new_password_entry.get()
        if not password.strip():
            self.pw_result_label.configure(text="Please enter a new password.", text_color=Color.ERROR)
            return
        set_dev_password(password)
        self.new_password_entry.delete(0, "end")
        self.pw_result_label.configure(text="Developer password changed.", text_color=Color.SUCCESS)

    def _on_publish_clicked(self) -> None:
        confirmed = messagebox.askyesno(
            "Publish to User Mode",
            "This will OVERWRITE the current production (User Mode) version.\n\n"
            "All User Mode settings (including email credentials), rule parameters, and feature "
            "flags will be replaced with your Developer Mode versions, and take effect immediately.\n\n"
            "Are you sure you want to publish?",
            icon="warning",
        )
        if not confirmed:
            return
        summary = publish_developer_to_user()
        messagebox.showinfo(
            "Published",
            f"Developer Mode has been promoted to Production.\n\n"
            f"{summary['parameters_promoted']} parameter(s) and {summary['flags_promoted']} "
            "feature flag(s) published, along with settings.",
        )
        if self.main_window is not None:
            self.main_window.rebuild_shell()
