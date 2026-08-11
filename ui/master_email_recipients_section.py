"""Master Email Recipients section -- an embeddable ctk.CTkFrame, packed
at the bottom of ui/email_center_page.py (below the existing KPIs/Send
Log), rather than its own top-level nav page. Manages the manually-
configured recipients for the Master Email system
(app/master_email_recipients_service.py, app/notification_service.py).
Built using ui/inventory_automated_emails_page.py as its explicit
reference architecture, per instruction: the same recipients list +
Add/Edit dialog shape (following ui/user_management_page.py +
ui/user_dialogs.py's own list/dialog pattern, the closest existing
precedent in this codebase for a manually-managed CRUD list).

Consolidated into Email Center, per instruction, since Master Email
Recipients is rarely configured and doesn't warrant its own sidebar
entry -- everything email-related (KPIs, Send Log, and now recipient
management) now lives on one page. This is a pure UI relocation: the
CRUD calls (create_recipient/update_recipient/delete_recipient) and the
Add/Edit dialog (ui/master_email_recipient_dialog.py) are byte-for-byte
unchanged from when this was its own page -- only the container it packs
into, and the heading level (a card-internal H3 here instead of a
page-level SectionHeader, since ui/email_center_page.py already owns
that), are different.

No separate send log here -- Path Validator already has one shared send
log for every manager email, individual RBM and Master alike, immediately
above this section on the same page (see EmailCenterPage's own Send Log
card, app.models.EmailNotification).

Individual (RBM) email routing has no equivalent recipient-management UI
and is entirely unaffected by this section -- see
app/notification_service.py's own docstring."""

import customtkinter as ctk

from app.master_email_recipients_service import (
    create_recipient,
    delete_recipient,
    get_all_recipients,
    update_recipient,
)
from ui.components import Card, EmptyState, PrimaryButton, SecondaryButton, render_error_banner, render_success_banner, styled_treeview
from ui.master_email_recipient_dialog import MasterEmailRecipientFormDialog
from ui.theme import Color, Font, Spacing
from ui.user_dialogs import ConfirmDialog

RECIPIENT_COLUMNS = ("name", "email", "division_label")
RECIPIENT_HEADINGS = {"name": "Name", "email": "Email Address", "division_label": "Division"}
RECIPIENT_WIDTHS = {"name": 180, "email": 280, "division_label": 160}

_STATUS_MESSAGE_MS = 4000


class MasterEmailRecipientsSection(ctk.CTkFrame):
    """Recipient management (add/edit/delete, with a Division filter) for
    the Path Validator Master Email system -- embedded inside
    EmailCenterPage, not a standalone page."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color="transparent")
        self._recipients_by_id: dict[int, dict] = {}
        self._status_clear_job = None
        self._build_widgets()

    # --- Layout --------------------------------------------------------

    def _build_widgets(self) -> None:
        self._status_container = ctk.CTkFrame(self, fg_color="transparent")

        card = Card(self)
        card.pack(fill="both", expand=True)
        self._recipients_card = card

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(body, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.SM))
        ctk.CTkLabel(
            header_row, text="Master Email Recipients", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(side="left")
        PrimaryButton(header_row, text="+ Add Recipient", command=self._open_add_dialog).pack(side="right")

        ctk.CTkLabel(
            body,
            text=(
                "Each configured recipient receives ONE consolidated Master Report email right after "
                "Path Validator finishes processing, filtered to their selected division -- \"All "
                "Divisions\" receives every flagged employee, a specific division receives only that "
                "division's flagged employees. Individual manager (RBM) emails are unaffected. "
                "Double-click a recipient to edit them; their sent/failed history appears in the Send Log above."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        self._recipients_container = ctk.CTkFrame(body, fg_color="transparent")
        self._recipients_container.pack(fill="both", expand=True)

        action_row = ctk.CTkFrame(body, fg_color="transparent")
        action_row.pack(fill="x", pady=(Spacing.SM, 0))
        SecondaryButton(action_row, text="Delete Selected", command=self._on_delete_selected_clicked).pack(
            side="left"
        )

    # --- Lifecycle ---------------------------------------------------------

    def on_show(self) -> None:
        """Called by EmailCenterPage.on_show() whenever that page becomes
        visible."""
        self._render_recipients()

    # --- Status banner -----------------------------------------------------

    def _show_status(self, message: str, kind: str = "success") -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()
        self._status_container.pack(fill="x", pady=(0, Spacing.MD), before=self._recipients_card)
        if kind == "success":
            render_success_banner(self._status_container, message)
        else:
            render_error_banner(self._status_container, "Note", [message])

        if self._status_clear_job is not None:
            self.after_cancel(self._status_clear_job)
        self._status_clear_job = self.after(_STATUS_MESSAGE_MS, self._clear_status)

    def _clear_status(self) -> None:
        self._status_clear_job = None
        for widget in self._status_container.winfo_children():
            widget.destroy()
        self._status_container.pack_forget()

    # --- Recipients --------------------------------------------------------

    def _render_recipients(self) -> None:
        for widget in self._recipients_container.winfo_children():
            widget.destroy()

        recipients = get_all_recipients()
        self._recipients_by_id = {r["id"]: r for r in recipients}

        if not recipients:
            EmptyState(
                self._recipients_container, "No recipients configured yet -- click + Add Recipient to add one."
            ).pack(fill="both", expand=True)
            return

        tree = styled_treeview(
            self._recipients_container, RECIPIENT_COLUMNS, RECIPIENT_HEADINGS, RECIPIENT_WIDTHS, height=8
        )
        for recipient in recipients:
            tree.insert(
                "",
                "end",
                iid=str(recipient["id"]),
                values=(recipient["name"], recipient["email"], recipient["division_label"]),
            )
        tree.pack(fill="both", expand=True)
        tree.bind("<Double-1>", lambda event, t=tree: self._on_recipient_row_double_clicked(t))
        self._recipients_tree = tree

    def _on_recipient_row_double_clicked(self, tree) -> None:
        selection = tree.selection()
        if not selection:
            return
        recipient = self._recipients_by_id.get(int(selection[0]))
        if recipient is None:
            return
        self._open_edit_dialog(recipient)

    def _open_add_dialog(self) -> None:
        dialog = MasterEmailRecipientFormDialog(
            self.winfo_toplevel(), on_submit=lambda data: self._handle_add_submit(dialog, data)
        )

    def _open_edit_dialog(self, recipient: dict) -> None:
        dialog = MasterEmailRecipientFormDialog(
            self.winfo_toplevel(),
            recipient=recipient,
            on_submit=lambda data: self._handle_edit_submit(dialog, data),
        )

    def _handle_add_submit(self, dialog, data: dict) -> None:
        try:
            create_recipient(data["name"], data["email"], data["division"])
        except Exception as exc:
            dialog.finish_saving(False, str(exc))
            return
        dialog.finish_saving(True)
        self._show_status(f"Recipient {data['name']} added.", kind="success")
        self._render_recipients()

    def _handle_edit_submit(self, dialog, data: dict) -> None:
        try:
            found = update_recipient(data["id"], data["name"], data["email"], data["division"])
        except Exception as exc:
            dialog.finish_saving(False, str(exc))
            return
        if not found:
            dialog.finish_saving(False, "This recipient no longer exists -- it may have been deleted elsewhere.")
            self._render_recipients()
            return
        dialog.finish_saving(True)
        self._show_status(f"Recipient {data['name']} updated.", kind="success")
        self._render_recipients()

    def _on_delete_selected_clicked(self) -> None:
        tree = getattr(self, "_recipients_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        selection = tree.selection()
        if not selection:
            from tkinter import messagebox

            messagebox.showinfo("Delete Recipient", "Select a recipient first.")
            return
        recipient = self._recipients_by_id.get(int(selection[0]))
        if recipient is None:
            return

        dialog = ConfirmDialog(
            self.winfo_toplevel(),
            title="Delete Recipient",
            message=f"Are you sure you want to delete {recipient['name']} ({recipient['email']})?",
            confirm_text="Delete",
            on_confirm=lambda: self._handle_delete_confirm(dialog, recipient),
        )

    def _handle_delete_confirm(self, dialog, recipient: dict) -> None:
        try:
            delete_recipient(recipient["id"])
        except Exception as exc:
            dialog.finish(False, str(exc))
            return
        dialog.finish(True)
        self._show_status(f"Recipient {recipient['name']} deleted.", kind="success")
        self._render_recipients()
