"""Update notification dialog for Saffron Automation."""

import tkinter as tk
import webbrowser
from tkinter import ttk

import customtkinter as ctk


class UpdateNotificationDialog(ctk.CTkToplevel):
    """Dialog showing available update with options to install now, later, or view release notes."""

    def __init__(
        self,
        parent,
        current_version: str,
        new_version: str,
        release_url: str,
    ):
        super().__init__(parent)
        self.title("Update Available")
        self.geometry("450x280")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        # Center on parent
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")

        self.result = None
        self._build_ui(current_version, new_version, release_url)

    def _build_ui(self, current_version: str, new_version: str, release_url: str) -> None:
        """Build the dialog UI."""
        # Main frame
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        # Header
        header_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        header_frame.pack(fill=ctk.X, pady=(0, 15))

        title = ctk.CTkLabel(
            header_frame,
            text="Update Available",
            font=("Segoe UI", 16, "bold"),
        )
        title.pack(anchor="w")

        # Message
        message_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        message_frame.pack(fill=ctk.BOTH, expand=True, pady=(0, 20))

        message = ctk.CTkLabel(
            message_frame,
            text=(
                f"A new version of Saffron Automation is available.\n\n"
                f"Current version: {current_version}\n"
                f"New version: {new_version}\n\n"
                f"Would you like to install the update now?\n"
                f"Your data will be preserved."
            ),
            font=("Segoe UI", 11),
            justify="left",
            wraplength=400,
        )
        message.pack(anchor="w")

        # Buttons frame
        button_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        button_frame.pack(fill=ctk.X, pady=(0, 0))

        # Release notes button (left side)
        release_button = ctk.CTkButton(
            button_frame,
            text="View Release Notes",
            font=("Segoe UI", 10),
            fg_color="gray50",
            hover_color="gray40",
            command=lambda: self._on_release_notes(release_url),
        )
        release_button.pack(side=ctk.LEFT, padx=(0, 10))

        # Action buttons frame (right side)
        action_frame = ctk.CTkFrame(button_frame, fg_color="transparent")
        action_frame.pack(side=ctk.RIGHT, fill=ctk.X, expand=True)

        # Skip button
        skip_button = ctk.CTkButton(
            action_frame,
            text="Later",
            font=("Segoe UI", 10),
            fg_color="gray50",
            hover_color="gray40",
            command=self._on_skip,
        )
        skip_button.pack(side=ctk.LEFT, padx=(0, 10))

        # Install button (primary)
        install_button = ctk.CTkButton(
            action_frame,
            text="Install Now",
            font=("Segoe UI", 10),
            command=self._on_install,
        )
        install_button.pack(side=ctk.LEFT)

    def _on_install(self) -> None:
        """User chose to install update."""
        self.result = "install"
        self.destroy()

    def _on_skip(self) -> None:
        """User chose to skip this update."""
        self.result = "skip"
        self.destroy()

    def _on_release_notes(self, release_url: str) -> None:
        """Open release notes in browser."""
        try:
            webbrowser.open(release_url)
        except Exception:
            pass
