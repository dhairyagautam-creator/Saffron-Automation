"""Add/Edit Recipient dialog for the Path Validator Master Email
Recipients page -- mirrors ui/inventory_email_recipient_dialog.py as
closely as possible (title/geometry/centering, error label, Save/Cancel,
finish_saving()), per instruction to keep recipient management UI
consistent between Inventory and Path Validator.

One deliberate difference from the Inventory dialog: Division is a single
CTkOptionMenu here, not a list of checkboxes -- app.master_email_recipients_
service.DIVISION_OPTIONS is one recipient = one filter (see that module's
own docstring), unlike Inventory's comma-separated multi-division list.

Unlike UserFormDialog (which waits for its caller to finish a background
Supabase call before closing), recipient writes are small local SQLite
operations -- ui/master_email_recipients_section.py calls straight through
to app.master_email_recipients_service on the UI thread and reports the
result back via finish_saving() immediately, with no worker thread in
between."""

from typing import Callable, Optional

import customtkinter as ctk

from app.master_email_recipients_service import ALL_DIVISIONS, DIVISION_LABELS, DIVISION_OPTIONS
from ui.components import Card, PrimaryButton, SecondaryButton
from ui.theme import Color, Font, Radius, Spacing


def _center_on_parent(dialog, parent) -> None:
    dialog.update_idletasks()
    x = parent.winfo_x() + (parent.winfo_width() // 2) - (dialog.winfo_width() // 2)
    y = parent.winfo_y() + (parent.winfo_height() // 2) - (dialog.winfo_height() // 2)
    dialog.geometry(f"+{x}+{y}")


class MasterEmailRecipientFormDialog(ctk.CTkToplevel):
    """Add Recipient (`recipient=None`) or Edit Recipient (`recipient`
    given -- a dict shaped like app.master_email_recipients_service's own
    {'id', 'name', 'email', 'division', 'division_label'})."""

    def __init__(
        self,
        parent,
        on_submit: Callable[[dict], None],
        recipient: Optional[dict] = None,
    ) -> None:
        super().__init__(parent)
        self._on_submit = on_submit
        self._recipient = recipient
        self._is_edit = recipient is not None

        self.title("Edit Recipient" if self._is_edit else "Add Recipient")
        # Tall enough for every stacked widget (title, error label, Name,
        # Email, Division label + its description line + the OptionMenu,
        # button row) to actually be visible -- this is a plain fixed-size
        # CTkToplevel, not a CTkScrollableFrame, so anything that doesn't
        # fit in too-short a window is silently clipped at the boundary
        # rather than reachable by scrolling. The previous 420x360 was too
        # short by roughly the Division description line + OptionMenu's
        # own height combined -- confirmed by the reported bug (the
        # "Division" label itself was visible, right at the clipped edge,
        # but the dropdown below it was not).
        self.geometry("420x520")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(fg_color=Color.SURFACE)

        self._build_ui()
        _center_on_parent(self, parent)

    def _build_ui(self) -> None:
        card = Card(self)
        card.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text=self.title(), font=Font.H2, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        self._error_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL, text_color=Color.ERROR, anchor="w", wraplength=340, justify="left"
        )
        self._error_label.pack(anchor="w", pady=(0, Spacing.SM))

        self._name_entry = self._labeled_entry(body, "Name", self._recipient["name"] if self._recipient else "")
        self._email_entry = self._labeled_entry(
            body, "Email Address", self._recipient["email"] if self._recipient else ""
        )

        ctk.CTkLabel(
            body, text="Division", font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w"
        ).pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            body,
            text="All Divisions receives the complete report; a single division receives only its own employees.",
            font=Font.SMALL,
            text_color=Color.TEXT_MUTED,
            anchor="w",
            wraplength=340,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))

        current_division = self._recipient["division"] if self._recipient else ALL_DIVISIONS
        self._division_menu = ctk.CTkOptionMenu(
            body,
            values=[DIVISION_LABELS[d] for d in DIVISION_OPTIONS],
            fg_color=Color.SURFACE,
            button_color=Color.PRIMARY,
            button_hover_color=Color.PRIMARY_HOVER,
            text_color=Color.TEXT_PRIMARY,
        )
        self._division_menu.set(DIVISION_LABELS.get(current_division, DIVISION_LABELS[ALL_DIVISIONS]))
        self._division_menu.pack(fill="x", pady=(0, Spacing.MD))

        button_row = ctk.CTkFrame(body, fg_color="transparent")
        button_row.pack(fill="x", side="bottom", pady=(Spacing.MD, 0))
        self._cancel_button = SecondaryButton(button_row, text="Cancel", command=self.destroy)
        self._cancel_button.pack(side="left")
        self._save_button = PrimaryButton(button_row, text="Save", command=self._handle_save)
        self._save_button.pack(side="right")

    def _labeled_entry(self, master, label: str, initial: str) -> ctk.CTkEntry:
        ctk.CTkLabel(
            master, text=label, font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w"
        ).pack(fill="x", pady=(0, 4))
        entry = ctk.CTkEntry(master, height=36, corner_radius=Radius.SM)
        if initial:
            entry.insert(0, initial)
        entry.pack(fill="x", pady=(0, Spacing.MD))
        return entry

    def _show_error(self, message: str) -> None:
        self._error_label.configure(text=message)

    def _set_saving(self, saving: bool) -> None:
        self._save_button.configure(state="disabled" if saving else "normal", text="Saving..." if saving else "Save")
        self._cancel_button.configure(state="disabled" if saving else "normal")

    def finish_saving(self, success: bool, error_message: Optional[str] = None) -> None:
        """Called by ui/master_email_recipients_section.py once the real
        create_recipient()/update_recipient() call has returned. Closes the
        dialog on success; on failure, re-enables it with the error message
        so the user doesn't lose what they typed."""
        if success:
            self.destroy()
        else:
            self._set_saving(False)
            self._show_error(error_message or "Something went wrong. Please try again.")

    def _handle_save(self) -> None:
        name = self._name_entry.get().strip()
        email = self._email_entry.get().strip()
        selected_label = self._division_menu.get()
        division = next((d for d in DIVISION_OPTIONS if DIVISION_LABELS[d] == selected_label), ALL_DIVISIONS)

        if not name:
            self._show_error("Name is required.")
            return
        if not email or "@" not in email:
            self._show_error("Enter a valid email address.")
            return

        self._show_error("")
        data = {"name": name, "email": email, "division": division}
        if self._is_edit:
            data["id"] = self._recipient["id"]

        self._set_saving(True)
        self._on_submit(data)
