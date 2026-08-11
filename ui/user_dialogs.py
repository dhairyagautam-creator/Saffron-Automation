"""Add/Edit User and confirmation dialogs for User Management.

Validated with app/user_validation.py (the same rules the service layer
re-checks independently before touching Supabase -- see that module's
docstring). These dialogs no longer close immediately on submit: the
caller (ui/user_management_page.py) performs the real Supabase operation
in the background and must call `finish_saving()` / `finish()` with the
result -- success closes the dialog, failure re-enables it with the
server's error message so the user can fix input and retry without
re-opening/re-typing everything.

Accessible Modules replaces the old Role dropdown (module-based permission
redesign): the checklist is built directly from app.module_registry.
all_modules() -- plain Python, no network round trip, no `roles` param
for the caller to fetch and pass in -- so a module registered there shows
up here automatically. A Super Admin checkbox grants every module,
including ones added later, without checking each box (see
app.permissions.can_access).
"""

from typing import Callable, Optional

import customtkinter as ctk

from app import module_registry, user_validation
from app.user_management_service import UserRecord
from ui.components import Card, PrimaryButton, SecondaryButton
from ui.theme import Color, Font, Radius, Spacing


def _center_on_parent(dialog, parent) -> None:
    dialog.update_idletasks()
    x = parent.winfo_x() + (parent.winfo_width() // 2) - (dialog.winfo_width() // 2)
    y = parent.winfo_y() + (parent.winfo_height() // 2) - (dialog.winfo_height() // 2)
    dialog.geometry(f"+{x}+{y}")


class UserFormDialog(ctk.CTkToplevel):
    """Add User (`user=None`) or Edit User (`user` provided). Collects
    full name, email, accessible modules, super admin flag, and active
    status -- plus a temporary password field, Add only (editing a user's
    password/credentials isn't part of this dialog).

    `is_self=True` (editing your own account) locks the Active checkbox on
    -- a client-side mirror of the RLS policy that already blocks this at
    the database level, so the failure shows up as a disabled checkbox
    instead of a save-then-error round trip."""

    def __init__(
        self,
        parent,
        on_submit: Callable[[dict], None],
        user: Optional[UserRecord] = None,
        is_self: bool = False,
    ) -> None:
        super().__init__(parent)
        self._on_submit = on_submit
        self._user = user
        self._is_edit = user is not None
        self._is_self = is_self
        self._module_vars: dict[str, ctk.BooleanVar] = {}

        self.title("Edit User" if self._is_edit else "Add User")
        self.geometry("440x680" if not self._is_edit else "440x620")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(fg_color=Color.SURFACE)

        self._build_ui()
        _center_on_parent(self, parent)

    def _build_ui(self) -> None:
        card = Card(self)
        card.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)

        # Cancel/Save live OUTSIDE the scroll area, pinned to the bottom of
        # the card (packed first with side="bottom"), so they stay reachable
        # no matter how tall the form grows with more modules or how far
        # it's been scrolled -- the whole point of this change. The fields
        # above go in a CTkScrollableFrame that takes the remaining height
        # and scrolls (mouse wheel included, natively) only when they
        # overflow the fixed dialog size.
        button_row = ctk.CTkFrame(card, fg_color="transparent")
        button_row.pack(fill="x", side="bottom", padx=Spacing.LG, pady=(Spacing.SM, Spacing.LG))
        self._cancel_button = SecondaryButton(button_row, text="Cancel", command=self.destroy)
        self._cancel_button.pack(side="left")
        self._save_button = PrimaryButton(button_row, text="Save", command=self._handle_save)
        self._save_button.pack(side="right")

        body = ctk.CTkScrollableFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.MD, pady=(Spacing.LG, 0))

        ctk.CTkLabel(
            body, text=self.title(), font=Font.H2, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        self._error_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL, text_color=Color.ERROR, anchor="w", wraplength=340, justify="left"
        )
        self._error_label.pack(anchor="w", pady=(0, Spacing.SM))

        self._full_name_entry = self._labeled_entry(body, "Full Name", self._user.full_name if self._user else "")
        self._email_entry = self._labeled_entry(body, "Email", self._user.email if self._user else "")
        if self._is_edit:
            # Email is the Supabase Auth identity -- not something this
            # dialog changes; shown for reference only when editing.
            self._email_entry.configure(state="disabled")

        current_modules = set(self._user.modules) if self._user else set()
        current_is_super_admin = self._user.is_super_admin if self._user else False

        self._super_admin_var = ctk.BooleanVar(value=current_is_super_admin)
        ctk.CTkCheckBox(
            body, text="Super Admin (access to every module, including future ones)",
            variable=self._super_admin_var, command=self._on_super_admin_toggled,
        ).pack(anchor="w", pady=(0, Spacing.SM))

        ctk.CTkLabel(
            body, text="Accessible Modules", font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w"
        ).pack(fill="x", pady=(0, 4))
        # A plain frame, not its own CTkScrollableFrame: the whole dialog
        # now scrolls, so a second nested scroll region here would just
        # fight the outer one for the mouse wheel. The checkboxes stack at
        # their natural height and the dialog's own scroll handles overflow.
        modules_frame = ctk.CTkFrame(body, fg_color=Color.SURFACE, corner_radius=Radius.SM)
        modules_frame.pack(fill="x", pady=(0, Spacing.MD))
        self._module_checkboxes: list[ctk.CTkCheckBox] = []
        for module in module_registry.all_modules():
            var = ctk.BooleanVar(value=module.key in current_modules)
            self._module_vars[module.key] = var
            checkbox = ctk.CTkCheckBox(modules_frame, text=module.title, variable=var)
            checkbox.pack(anchor="w", pady=2, padx=Spacing.SM)
            self._module_checkboxes.append(checkbox)
        self._apply_super_admin_lock()

        self._active_var = ctk.BooleanVar(value=True if self._is_self else (self._user.active if self._user else True))
        active_checkbox = ctk.CTkCheckBox(body, text="Active", variable=self._active_var)
        if self._is_self:
            active_checkbox.configure(state="disabled")
        active_checkbox.pack(anchor="w", pady=(0, 2))
        if self._is_self:
            ctk.CTkLabel(
                body, text="You cannot disable your own account.", font=Font.SMALL, text_color=Color.TEXT_MUTED,
                anchor="w",
            ).pack(anchor="w", pady=(0, Spacing.MD))
        else:
            ctk.CTkFrame(body, fg_color="transparent", height=Spacing.MD).pack()

        self._password_entry = None
        if not self._is_edit:
            self._password_entry = self._labeled_entry(body, "Temporary Password", "", show="*")

    def _on_super_admin_toggled(self) -> None:
        self._apply_super_admin_lock()

    def _apply_super_admin_lock(self) -> None:
        """While Super Admin is checked, individual module checkboxes are
        disabled (not hidden) -- a super admin has every module regardless
        of which boxes are checked (see app.permissions.can_access), so
        leaving them interactive would show a misleading "unchecked = no
        access" state that isn't true. Their checked/unchecked values are
        preserved underneath -- unticking Super Admin later restores
        whatever selection was already there rather than resetting it."""
        state = "disabled" if self._super_admin_var.get() else "normal"
        for checkbox in self._module_checkboxes:
            checkbox.configure(state=state)

    def _labeled_entry(self, master, label: str, initial: str, show: Optional[str] = None) -> ctk.CTkEntry:
        ctk.CTkLabel(
            master, text=label, font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w"
        ).pack(fill="x", pady=(0, 4))
        entry = ctk.CTkEntry(master, height=36, corner_radius=Radius.SM, show=show)
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
        """Called by the page once the real Supabase operation this dialog
        triggered has completed. Closes the dialog on success; on failure,
        re-enables it with the server's error message so the user doesn't
        lose what they typed."""
        if success:
            self.destroy()
        else:
            self._set_saving(False)
            self._show_error(error_message or "Something went wrong. Please try again.")

    def _handle_save(self) -> None:
        full_name = self._full_name_entry.get().strip()
        email = self._email_entry.get().strip()
        is_super_admin = self._super_admin_var.get()
        module_keys = {key for key, var in self._module_vars.items() if var.get()}
        active = self._active_var.get()

        for error in (user_validation.validate_full_name(full_name), user_validation.validate_email(email)):
            if error:
                self._show_error(error)
                return

        password = None
        if not self._is_edit:
            password = self._password_entry.get()
            password_error = user_validation.validate_password(password)
            if password_error:
                self._show_error(password_error)
                return

        self._show_error("")
        data = {
            "full_name": full_name,
            "email": email,
            "is_super_admin": is_super_admin,
            "module_keys": module_keys,
            "active": active,
        }
        if self._is_edit:
            data["id"] = self._user.id
        if password is not None:
            data["password"] = password

        self._set_saving(True)
        self._on_submit(data)


class ConfirmDialog(ctk.CTkToplevel):
    """Generic Yes/Cancel confirmation -- used today for Enable/Disable,
    reusable for any future destructive-ish action that just needs an "are
    you sure" step rather than a form. Same finish()-driven pattern as
    UserFormDialog: `on_confirm()` starts the real operation, and the
    caller must call `finish(success, error_message)` once it's done."""

    def __init__(self, parent, title: str, message: str, confirm_text: str, on_confirm: Callable[[], None]) -> None:
        super().__init__(parent)
        self._on_confirm = on_confirm
        self._confirm_text = confirm_text

        self.title(title)
        self.geometry("380x200")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(fg_color=Color.SURFACE)

        card = Card(self)
        card.pack(fill="both", expand=True, padx=Spacing.MD, pady=Spacing.MD)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text=message, font=Font.BODY, text_color=Color.TEXT_PRIMARY, wraplength=300, justify="left"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        self._error_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL, text_color=Color.ERROR, wraplength=300, justify="left", anchor="w"
        )
        self._error_label.pack(anchor="w", pady=(0, Spacing.SM))

        button_row = ctk.CTkFrame(body, fg_color="transparent")
        button_row.pack(fill="x", side="bottom")
        self._cancel_button = SecondaryButton(button_row, text="Cancel", command=self.destroy)
        self._cancel_button.pack(side="left")
        self._confirm_button = PrimaryButton(button_row, text=confirm_text, command=self._handle_confirm)
        self._confirm_button.pack(side="right")

        _center_on_parent(self, parent)

    def _set_processing(self, processing: bool) -> None:
        self._confirm_button.configure(
            state="disabled" if processing else "normal", text="Please wait..." if processing else self._confirm_text
        )
        self._cancel_button.configure(state="disabled" if processing else "normal")

    def finish(self, success: bool, error_message: Optional[str] = None) -> None:
        if success:
            self.destroy()
        else:
            self._set_processing(False)
            self._error_label.configure(text=error_message or "Something went wrong. Please try again.")

    def _handle_confirm(self) -> None:
        self._set_processing(True)
        self._on_confirm()
