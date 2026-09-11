"""Automated Emails page for Inventory Monitoring (Version 2.0, Milestone
56): manages the manually-configured recipients for the Inventory
Automated Email System (app/inventory_email_recipients_service.py,
app/inventory_notification_service.py) plus a read-only send log.

Path Validator has no equivalent recipient-management screen -- its own
recipients are resolved dynamically by walking the employee hierarchy, so
there's nothing to mirror for the recipients half of this page. Instead:
- The recipients list + Add/Edit dialog follows ui/user_management_page.py
  + ui/user_dialogs.py's own list/dialog shape, the closest existing
  precedent in this codebase for a manually-managed CRUD list. Recipient
  writes are small local SQLite calls (not network calls like User
  Management's Supabase ones), so they run synchronously on the UI thread
  -- no worker thread, no "Saving..." spinner delay.
- The Send Log section mirrors ui/email_center_page.py's own "Send Log"
  card as closely as possible: a styled_treeview of InventoryEmailNotification
  rows, an Export button that reuses app/table_export_service.py (the same
  ONE export implementation the Replenishment page's own Export button
  uses), and double-click on a row to view the full email body.
"""

import threading
from tkinter import messagebox

import customtkinter as ctk

from app.email_send_history_service import format_relative_time
from app.inventory_email_recipients_service import (
    create_recipient,
    delete_recipient,
    get_all_recipients,
    update_recipient,
)
from app.inventory_notification_service import (
    STATUS_SKIPPED_NO_DATA,
    build_inventory_replenishment_email_batch,
    replenishment_data_state,
    send_button_state,
    send_inventory_replenishment_emails,
)
from app.permissions import can_send_emails
from app.table_export_service import default_export_filename, export_rows_with_ui
from database.connection import get_config_session, to_local
from database.models import InventoryEmailNotification
from ui.components import (
    Card,
    EmptyState,
    PrimaryButton,
    SecondaryButton,
    SectionHeader,
    SendEmailsButton,
    render_error_banner,
    render_success_banner,
    styled_treeview,
)
from ui.icons import get_icon
from ui.inventory_email_recipient_dialog import RecipientFormDialog
from ui.send_emails_dialog import SendEmailsDialog
from ui.theme import Color, Font, Spacing
from ui.user_dialogs import ConfirmDialog

RECIPIENT_COLUMNS = ("name", "email", "divisions")
RECIPIENT_HEADINGS = {"name": "Name", "email": "Email Address", "divisions": "Divisions"}
RECIPIENT_WIDTHS = {"name": 160, "email": 260, "divisions": 220}

LOG_COLUMNS = ("recipient_name", "recipient_email", "divisions", "row_count", "status", "sent_at", "error")
LOG_HEADINGS = {
    "recipient_name": "Recipient",
    "recipient_email": "Email",
    "divisions": "Divisions",
    "row_count": "Rows",
    "status": "Status",
    "sent_at": "Sent At",
    "error": "Error",
}
LOG_WIDTHS = {
    "recipient_name": 140,
    "recipient_email": 200,
    "divisions": 160,
    "row_count": 60,
    "status": 130,
    "sent_at": 140,
    "error": 240,
}

_STATUS_MESSAGE_MS = 4000


class InventoryAutomatedEmailsPage(ctk.CTkFrame):
    """Recipient management (add/edit/delete, with Division filters) plus a
    read-only send log for the Inventory Automated Email System."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._recipients_by_id: dict[int, dict] = {}
        self._log_rows_by_id: dict[int, InventoryEmailNotification] = {}
        self._current_export_rows: list[dict] = []
        self._status_clear_job = None
        self._build_widgets()

    # --- Layout --------------------------------------------------------

    def _build_widgets(self) -> None:
        # CTkScrollableFrame -- Recipients + Send Log together can exceed a
        # smaller window's height, same reasoning as the Milestone 56 fix
        # applied to ui/inventory_settings_page.py.
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(outer, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.MD))
        SectionHeader(
            header_row,
            "Automated Emails",
            "Configure who receives the Inventory Replenishment report, and by which divisions",
        ).pack(side="left", anchor="w")
        self.send_emails_button = SendEmailsButton(header_row, command=self._on_send_emails_clicked)
        self.send_emails_button.pack(side="right", anchor="e")

        self._status_container = ctk.CTkFrame(outer, fg_color="transparent")

        self._build_recipients_card(outer)
        self._build_log_card(outer)

    def _build_recipients_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="both", expand=True, pady=(0, Spacing.LG))
        self._recipients_card = card

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(body, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.SM))
        ctk.CTkLabel(
            header_row, text="Recipients", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(side="left")
        PrimaryButton(header_row, text="+ Add Recipient", command=self._open_add_dialog).pack(side="right")

        ctk.CTkLabel(
            body,
            text=(
                "Each configured recipient receives an Excel copy of the Replenishment report "
                "filtered to only their selected divisions when Send Emails above is clicked. "
                "Double-click a recipient to edit them."
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

    def _build_log_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="both", expand=True)

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(body, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.SM))
        ctk.CTkLabel(
            header_row, text="Send Log", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(side="left")
        self.export_button = PrimaryButton(
            header_row,
            text="Export",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_export_clicked,
        )
        self.export_button.pack(side="right")

        self._log_container = ctk.CTkFrame(body, fg_color="transparent")
        self._log_container.pack(fill="both", expand=True)

    # --- Page lifecycle --------------------------------------------------

    def on_show(self) -> None:
        """Called by InventoryModule whenever this page becomes visible."""
        self._render_recipients()
        self._render_log()
        self._refresh_send_emails_button()

    # --- Send Emails ---------------------------------------------------------

    def _refresh_send_emails_button(self) -> None:
        if not can_send_emails("inventory_module"):
            self.send_emails_button.set_state(enabled=False, subtext="For authoritative users only.")
            self.send_emails_button.set_status_line("")
            return

        has_data, changed, last_send = replenishment_data_state()
        state = send_button_state(has_data=has_data, changed_since_last_send=changed)
        if state == "no_data":
            self.send_emails_button.set_state(enabled=False, subtext="No reports generated.")
        else:
            self.send_emails_button.set_state(enabled=True, subtext="")
        self._render_send_status_line(last_send)

    def _render_send_status_line(self, last_send: dict | None) -> None:
        if last_send is None:
            self.send_emails_button.set_status_line("")
            return
        self.send_emails_button.set_status_line(
            f"Last sent {format_relative_time(last_send['sent_at'])} by {last_send['sent_by_name']}"
        )

    def _on_send_emails_clicked(self) -> None:
        if not can_send_emails("inventory_module"):
            return  # defensive -- button should already be disabled

        self.send_emails_button.button.configure(state="disabled", text="Preparing...")

        def worker() -> None:
            drafts = build_inventory_replenishment_email_batch()
            self.after(0, self._on_batch_built, drafts)

        threading.Thread(target=worker, daemon=True).start()

    def _on_batch_built(self, drafts: list) -> None:
        self.send_emails_button.button.configure(text="Send Emails")
        self._refresh_send_emails_button()

        routable = [d for d in drafts if d["status"] != STATUS_SKIPPED_NO_DATA]
        _, changed, _ = replenishment_data_state()
        state = send_button_state(has_data=True, changed_since_last_send=changed)

        SendEmailsDialog(
            self.winfo_toplevel(),
            state=state,
            recipient_count=len(routable),
            drafts=drafts,
            send_fn=lambda d, progress_cb: send_inventory_replenishment_emails(progress_callback=progress_cb, drafts=d),
            describe_failure_fn=lambda d: f"{d.get('recipient_email', '?')}: {d.get('error_message', 'unknown error')}",
            on_complete=lambda result: self.on_show(),
        )

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
                values=(recipient["name"], recipient["email"], ", ".join(recipient["divisions"])),
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
        dialog = RecipientFormDialog(
            self.winfo_toplevel(), on_submit=lambda data: self._handle_add_submit(dialog, data)
        )

    def _open_edit_dialog(self, recipient: dict) -> None:
        dialog = RecipientFormDialog(
            self.winfo_toplevel(),
            recipient=recipient,
            on_submit=lambda data: self._handle_edit_submit(dialog, data),
        )

    def _handle_add_submit(self, dialog, data: dict) -> None:
        try:
            create_recipient(data["name"], data["email"], data["divisions"])
        except Exception as exc:
            dialog.finish_saving(False, str(exc))
            return
        dialog.finish_saving(True)
        self._show_status(f"Recipient {data['name']} added.", kind="success")
        self._render_recipients()

    def _handle_edit_submit(self, dialog, data: dict) -> None:
        try:
            found = update_recipient(data["id"], data["name"], data["email"], data["divisions"])
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

    # --- Send Log ------------------------------------------------------

    def _on_export_clicked(self) -> None:
        """Exports `self._current_export_rows` -- the exact list
        `_render_log()` just used to populate the Treeview -- via the same
        table_export_service used everywhere else (Milestone 54/55)."""
        export_rows_with_ui(
            self,
            rows=self._current_export_rows,
            columns=LOG_COLUMNS,
            headings=LOG_HEADINGS,
            suggested_filename=default_export_filename("InventoryEmailSendLog"),
            sheet_title="Send Log",
        )

    def _render_log(self) -> None:
        for widget in self._log_container.winfo_children():
            widget.destroy()
        self._current_export_rows = []

        session = get_config_session()
        try:
            rows = (
                session.query(InventoryEmailNotification)
                .order_by(InventoryEmailNotification.created_at.desc())
                .all()
            )
        finally:
            session.close()

        if not rows:
            EmptyState(self._log_container, "No email batches yet -- upload an Inventory Report to generate one.").pack(
                fill="both", expand=True
            )
            return

        self._log_rows_by_id = {row.id: row for row in rows}

        tree = styled_treeview(self._log_container, LOG_COLUMNS, LOG_HEADINGS, LOG_WIDTHS, height=10)
        for row in rows:
            sent_at_text = to_local(row.sent_at).strftime("%Y-%m-%d %H:%M") if row.sent_at else "-"
            # Same dict both feeds the Treeview's own `values=` tuple and the
            # exported row -- one source of truth, never two separately
            # maintained value lists (same pattern as ui/email_center_page.py).
            display_row = {
                "recipient_name": row.recipient_name or "-",
                "recipient_email": row.recipient_email or "-",
                "divisions": row.divisions or "-",
                "row_count": row.row_count,
                "status": row.status,
                "sent_at": sent_at_text,
                "error": row.error_message or "",
            }
            self._current_export_rows.append(display_row)
            tree.insert(
                "",
                "end",
                iid=str(row.id),
                values=tuple(display_row[col] for col in LOG_COLUMNS),
            )
        tree.pack(fill="both", expand=True)
        tree.bind("<Double-1>", lambda event, t=tree: self._on_log_row_double_clicked(t))

    def _on_log_row_double_clicked(self, tree) -> None:
        """Double-clicking a row just shows the full email that was sent (or
        attempted) -- a read-only inspection, mirroring
        ui/email_center_page.py's own identical behavior."""
        selection = tree.selection()
        if not selection:
            return
        row = self._log_rows_by_id.get(int(selection[0]))
        if row is None:
            return
        messagebox.showinfo(row.subject, row.body)
