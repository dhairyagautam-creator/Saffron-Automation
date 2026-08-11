"""Work Distribution Email Center page -- redesigned to match Path
Validator's own architecture, per explicit instruction: no manually
managed Recipient Table (removed completely -- this module will never let
a user hand-add a recipient). Instead:

- Sender Credentials: this module's own Gmail sender email/app password
  (app.work_distribution_email_settings_service) -- a separate credential
  set from Path Validator's and Inventory's own, same reasoning as
  Inventory's Email Configuration card.
- Hierarchy Workbooks: Onyx/Guardians/Xandra connections, reusing
  app.workbook_connections (the SAME WorkbookConnection table/rows Path
  Validator's own Organization Data page manages) and
  app.hierarchy_parser.refresh_hierarchy() (the SAME employee_hierarchy
  table) -- there is exactly one hierarchy dataset in the application;
  this page is a second, convenient place to manage it from within Work
  Distribution, not a separate parallel system.

These hierarchy files determine email recipients automatically (see
app.work_distribution_notification_service's own module docstring for the
full routing rules) -- the user never manually adds a recipient here or
anywhere else in this module.

UI consistency update: the KPI cards + searchable/exportable hierarchy
table shown after a refresh are ui.hierarchy_table_section.HierarchyTableSection
-- the EXACT SAME component ui/organization_data_page.py (Path Validator's
own Organization Data page) uses, so both pages show byte-for-byte
identical hierarchy data/behavior with zero duplicated logic. Only the
Browse/Connected-Workbooks UI above it (this page's own hierarchy card)
stays separate, since the two pages' surrounding context genuinely differs.

Phase 5: Notifications card added below -- Preview builds the exact same
recipient/grouping resolution a real send would, without opening an SMTP
connection or sending anything, so the recipient count can be sanity-checked
first. Send actually delivers every consolidated email and logs the
attempt (WorkDistributionEmailNotification) to the Send Log table below,
mirroring ui/inventory_automated_emails_page.py's own log-table shape as
closely as possible.
"""

import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk
from loguru import logger

from app.hierarchy_parser import refresh_hierarchy
from app.smtp_service import test_connection
from app.work_distribution_email_settings_service import get_settings, save_settings
from app.work_distribution_notification_service import (
    build_notification_batch,
    get_recent_notifications,
    send_notification_batch,
)
from app.work_distribution_upload_log_service import record_upload
from app.workbook_connections import WORKBOOK_NAMES, get_connections, get_status, set_connection
from ui.components import Card, EmptyState, PrimaryButton, SecondaryButton, SectionHeader, StatusBadge, styled_treeview
from ui.hierarchy_table_section import HierarchyTableSection
from ui.icons import get_icon
from ui.loading_overlay import LoadingOverlay
from ui.theme import Color, Font, Spacing

STATUS_BADGE_KIND = {"Connected": "success", "File Not Found": "error", "Not Configured": "neutral"}

LOG_COLUMNS = ("recipient_name", "recipient_email", "sections", "employee_count", "status", "created_at")
LOG_HEADINGS = {
    "recipient_name": "Recipient",
    "recipient_email": "Email",
    "sections": "Sections",
    "employee_count": "Employees",
    "status": "Status",
    "created_at": "Sent At",
}
LOG_WIDTHS = {
    "recipient_name": 150,
    "recipient_email": 210,
    "sections": 190,
    "employee_count": 80,
    "status": 90,
    "created_at": 150,
}


class WorkDistributionEmailCenterPage(ctk.CTkFrame):
    """Sender credentials + hierarchy workbook connections + Notifications
    (Preview/Send) + Send Log."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self.path_labels: dict[str, ctk.CTkLabel] = {}
        self.status_badges: dict[str, StatusBadge] = {}
        self._pending_drafts: list | None = None
        self._build_widgets()
        self.loading_overlay = LoadingOverlay(self)

    def on_show(self) -> None:
        self._load_sender_credentials()
        self._refresh_connection_labels()
        self.hierarchy_section.load_from_db()
        self._refresh_log()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer,
            "Email Center",
            "Sender credentials, hierarchy data, and automated coverage-report notifications",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        self._build_sender_credentials_card(outer)
        self._build_hierarchy_card(outer)

        self.hierarchy_section = HierarchyTableSection(outer, export_filename_prefix="WorkDistributionHierarchy")
        self.hierarchy_section.pack(fill="both", expand=True, pady=(0, Spacing.LG))

        self._build_notifications_card(outer)
        self._build_log_card(outer)

    # --- Sender Credentials --------------------------------------------

    def _build_sender_credentials_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(0, Spacing.LG))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Sender Credentials", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "The Gmail account Work Distribution will send coverage-report emails from, once "
                "automated sending is implemented. A separate account from Path Validator's and "
                "Inventory's own."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        ctk.CTkLabel(
            body, text="Sender Email", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        self.sender_email_entry = ctk.CTkEntry(body, placeholder_text="you@gmail.com")
        self.sender_email_entry.pack(fill="x", pady=(4, Spacing.MD))

        ctk.CTkLabel(
            body, text="Sender Password / App Password", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w")
        self.sender_password_entry = ctk.CTkEntry(body, placeholder_text="16-character app password", show="*")
        self.sender_password_entry.pack(fill="x", pady=(4, Spacing.MD))

        self.automatic_sending_switch = ctk.CTkSwitch(
            body,
            text="Enable Automatic Email Sending",
            font=Font.BODY,
            text_color=Color.TEXT_PRIMARY,
            progress_color=Color.PRIMARY,
            command=self._on_automatic_sending_toggled,
        )
        self.automatic_sending_switch.pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            body,
            text=(
                "When enabled, every currently flagged employee's notification is sent automatically "
                "right after RGD Coverage or Manager Work Allocation Run Analysis completes -- the "
                "manual Preview/Send controls below become unavailable while this is on. When off, "
                "nothing is ever sent automatically; use Preview/Send manually instead."
            ),
            font=Font.SMALL,
            text_color=Color.TEXT_MUTED,
            anchor="w",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        button_row = ctk.CTkFrame(body, fg_color="transparent")
        button_row.pack(fill="x", pady=(0, Spacing.SM))

        self.sender_save_button = PrimaryButton(
            button_row, text="Save Sender Credentials", command=self._on_save_sender_clicked
        )
        self.sender_save_button.pack(side="left", padx=(0, Spacing.SM))

        self.sender_test_button = SecondaryButton(
            button_row, text="Test Connection", command=self._on_test_connection_clicked
        )
        self.sender_test_button.pack(side="left")

        self.sender_status_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL_BOLD, anchor="w", wraplength=700, justify="left",
        )
        self.sender_status_label.pack(anchor="w", pady=(Spacing.SM, 0))

    def _load_sender_credentials(self) -> None:
        settings = get_settings()
        self.sender_email_entry.delete(0, "end")
        self.sender_email_entry.insert(0, settings["sender_email"])
        self.sender_password_entry.delete(0, "end")
        self.sender_password_entry.insert(0, settings["app_password"])
        if settings["automatic_sending_enabled"]:
            self.automatic_sending_switch.select()
        else:
            self.automatic_sending_switch.deselect()
        self.sender_status_label.configure(text="")
        self._apply_automatic_sending_state(settings["automatic_sending_enabled"])

    def _on_save_sender_clicked(self) -> None:
        save_settings(
            self.sender_email_entry.get().strip(),
            self.sender_password_entry.get().strip(),
            bool(self.automatic_sending_switch.get()),
        )
        self.sender_status_label.configure(text="Sender credentials saved.", text_color=Color.SUCCESS)

    def _on_automatic_sending_toggled(self) -> None:
        # Persist immediately -- the toggle itself IS the setting, same
        # convention as ui/inventory_settings_page.py's own switch (saved
        # together with Save Sender Credentials, but also takes effect
        # immediately here since it also drives this page's own manual
        # control visibility, not just a background sending decision).
        self._on_save_sender_clicked()
        self._apply_automatic_sending_state(bool(self.automatic_sending_switch.get()))

    def _on_test_connection_clicked(self) -> None:
        # Mirrors ui/inventory_settings_page.py's own
        # _on_email_settings_test_clicked() exactly: save first so the test
        # reflects what's currently typed, then send a real test email
        # synchronously (test_connection() is a single quick SMTP round
        # trip, same as Path Validator's own Test Connection).
        self._on_save_sender_clicked()
        self.sender_test_button.configure(state="disabled")
        self.sender_save_button.configure(state="disabled")
        self.sender_status_label.configure(text="Sending test email…", text_color=Color.TEXT_SECONDARY)
        self.update_idletasks()

        sender_email = self.sender_email_entry.get().strip()
        app_password = self.sender_password_entry.get().strip()

        try:
            success, message = test_connection(sender_email, app_password)
        except Exception as exc:
            logger.error(f"Work Distribution test connection failed unexpectedly: {exc}")
            success, message = False, f"Unexpected error: {exc}"

        self.sender_status_label.configure(text=message, text_color=Color.SUCCESS if success else Color.ERROR)
        self.sender_test_button.configure(state="normal")
        self.sender_save_button.configure(state="normal")

    # --- Hierarchy Workbooks -----------------------------------------------

    def _build_hierarchy_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="x")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Hierarchy Workbooks", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "Connect each division's Organization Data workbook -- the same hierarchy data "
                "Path Validator's Organization Data page manages. These determine each flagged "
                "employee's management chain automatically; no recipient is ever added by hand."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        for name in WORKBOOK_NAMES:
            row = Card(body, fg_color=Color.SURFACE, border_width=0)
            row.pack(fill="x", pady=4)

            row_inner = ctk.CTkFrame(row, fg_color="transparent")
            row_inner.pack(fill="x", padx=Spacing.MD, pady=Spacing.SM)

            ctk.CTkLabel(
                row_inner, text=name, width=100, anchor="w", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY
            ).pack(side="left")

            ctk.CTkButton(
                row_inner,
                text="Browse…",
                width=90,
                fg_color="transparent",
                border_width=1,
                border_color=Color.BORDER,
                text_color=Color.TEXT_PRIMARY,
                hover_color=Color.SURFACE,
                command=lambda n=name: self._on_browse_clicked(n),
            ).pack(side="left", padx=(Spacing.MD, 0))

            badge = StatusBadge(row_inner, "Not Configured", "neutral")
            badge.pack(side="right")
            self.status_badges[name] = badge

            path_label = ctk.CTkLabel(
                row_inner, text="Not configured", anchor="w", font=Font.SMALL, text_color=Color.TEXT_MUTED
            )
            path_label.pack(side="left", fill="x", expand=True, padx=Spacing.MD)
            self.path_labels[name] = path_label

        self.refresh_button = PrimaryButton(
            body,
            text="Refresh Hierarchy",
            image=get_icon("refresh", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_refresh_clicked,
        )
        self.refresh_button.pack(pady=(Spacing.SM, 0))

        self.hierarchy_summary_label = ctk.CTkLabel(
            body, text="", font=Font.SMALL, text_color=Color.TEXT_SECONDARY, wraplength=700, justify="left",
        )
        self.hierarchy_summary_label.pack(anchor="w", pady=(Spacing.SM, 0))

    def _refresh_connection_labels(self) -> None:
        connections = get_connections()
        for name in WORKBOOK_NAMES:
            file_path = connections.get(name)
            status = get_status(file_path)
            self.path_labels[name].configure(text=file_path or "Not configured")
            self.status_badges[name].set_status(status, STATUS_BADGE_KIND[status])

    def _on_browse_clicked(self, workbook_name: str) -> None:
        file_path = filedialog.askopenfilename(
            title=f"Select {workbook_name} Hierarchy Workbook",
            filetypes=[("Excel files", "*.xlsx")],
        )
        if not file_path:
            return

        set_connection(workbook_name, file_path)
        logger.info(f"Work Distribution hierarchy workbook connected: '{workbook_name}' -> {file_path}")
        record_upload(file_path, "Hierarchy Workbook", division=workbook_name)
        self._refresh_connection_labels()

    def _on_refresh_clicked(self) -> None:
        self.refresh_button.configure(state="disabled")
        self.hierarchy_summary_label.configure(text="Refreshing hierarchy…")
        self.update_idletasks()

        def worker() -> None:
            try:
                stats = refresh_hierarchy()
                error = None
            except Exception as exc:
                stats = None
                error = exc
            self.after(0, lambda: self._on_refresh_done(stats, error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_refresh_done(self, stats: dict | None, error: Exception | None) -> None:
        self.refresh_button.configure(state="normal")
        if not self.winfo_exists():
            return

        if error is not None:
            logger.error(f"Work Distribution hierarchy refresh failed: {error}")
            messagebox.showerror("Refresh Failed", f"Could not refresh hierarchy data.\n\n{error}")
            self.hierarchy_summary_label.configure(text="Refresh failed.")
            return

        self.hierarchy_section.load_from_db()
        self.hierarchy_section.update_kpis(stats)

        if not stats["workbooks_read"]:
            self.hierarchy_summary_label.configure(
                text="No hierarchy workbooks are connected yet -- connect at least one above and refresh again."
            )
            return

        summary = f"Processed {stats['worksheets_processed']} worksheet(s) from {', '.join(stats['workbooks_read'])}."
        if stats["workbooks_skipped"]:
            summary += " Skipped: " + ", ".join(
                f"{name} ({reason})" for name, reason in stats["workbooks_skipped"].items()
            )
        self.hierarchy_summary_label.configure(text=summary)

    # --- Notifications (Preview / Send) -------------------------------------

    def _build_notifications_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="x", pady=(0, Spacing.LG))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Notifications", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "Every currently FLAGGED employee across RGD Coverage and Manager Work Allocation is "
                "notified automatically -- recipients are resolved from the hierarchy above, never "
                "configured by hand. A recipient due more than one flagged employee receives ONE "
                "consolidated email, not several. Preview resolves recipients without sending anything."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(2, Spacing.MD))

        # Exactly one of these two is ever packed -- see
        # _apply_automatic_sending_state(), called whenever the Automatic
        # Email Sending switch's saved state is (re)loaded. Automatic Email
        # Sending ON means every flagged employee is already notified
        # automatically right after Run Analysis (see
        # ui/work_distribution_upload_page.py) -- manual Preview/Send would
        # be redundant and confusing, so they're hidden entirely, not just
        # disabled, while this indicator takes their place.
        self.automated_mode_label = ctk.CTkLabel(
            body,
            text=(
                "Automatic Email Sending is ON -- every flagged employee's notification is sent "
                "automatically right after Run Analysis. Manual sending is unavailable while this is "
                "on; turn it off in Sender Credentials above to preview or send manually."
            ),
            font=Font.SMALL_BOLD,
            text_color=Color.PRIMARY,
            anchor="w",
            wraplength=700,
            justify="left",
        )

        self.manual_controls_frame = ctk.CTkFrame(body, fg_color="transparent")

        button_row = ctk.CTkFrame(self.manual_controls_frame, fg_color="transparent")
        button_row.pack(fill="x")

        self.preview_button = SecondaryButton(
            button_row, text="Preview Recipients", command=self._on_preview_clicked
        )
        self.preview_button.pack(side="left")

        self.send_button = PrimaryButton(
            button_row, text="Send Notifications", command=self._on_send_clicked, state="disabled"
        )
        self.send_button.pack(side="left", padx=(Spacing.MD, 0))

        self.notifications_status_label = ctk.CTkLabel(
            self.manual_controls_frame, text="", font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w",
            wraplength=700, justify="left",
        )
        self.notifications_status_label.pack(anchor="w", pady=(Spacing.SM, 0))

    def _apply_automatic_sending_state(self, enabled: bool) -> None:
        """Shows exactly one of the automated-mode indicator / manual
        Preview+Send controls, per Automatic Email Sending's current saved
        state -- see this page's own module docstring."""
        if enabled:
            self.manual_controls_frame.pack_forget()
            self.automated_mode_label.pack(anchor="w", pady=(2, Spacing.MD))
        else:
            self.automated_mode_label.pack_forget()
            self.manual_controls_frame.pack(fill="x")

    def _on_preview_clicked(self) -> None:
        self.preview_button.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.notifications_status_label.configure(text="Resolving recipients…", text_color=Color.TEXT_SECONDARY)
        self.update_idletasks()

        def worker() -> None:
            try:
                drafts = build_notification_batch()
                error = None
            except Exception as exc:
                drafts = None
                error = exc
            self.after(0, lambda: self._on_preview_done(drafts, error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_preview_done(self, drafts: list | None, error: Exception | None) -> None:
        self.preview_button.configure(state="normal")
        if not self.winfo_exists():
            return

        if error is not None:
            logger.error(f"Work Distribution notification preview failed: {error}")
            messagebox.showerror("Preview Failed", f"Could not resolve recipients.\n\n{error}")
            self.notifications_status_label.configure(text="Preview failed.", text_color=Color.ERROR)
            return

        self._pending_drafts = drafts
        if not drafts:
            self.notifications_status_label.configure(
                text="No flagged employees currently route to a valid recipient -- nothing to send.",
                text_color=Color.TEXT_SECONDARY,
            )
            self.send_button.configure(state="disabled")
            return

        total_employees = sum(d["employee_count"] for d in drafts)
        self.notifications_status_label.configure(
            text=(
                f"{len(drafts)} recipient(s) will be notified, covering {total_employees} flagged "
                "employee(s) total. Review the count, then Send when ready."
            ),
            text_color=Color.SUCCESS,
        )
        self.send_button.configure(state="normal")

    def _on_send_clicked(self) -> None:
        if not self._pending_drafts:
            return
        total_employees = sum(d["employee_count"] for d in self._pending_drafts)
        confirmed = messagebox.askyesno(
            "Send Notifications",
            f"This will send {len(self._pending_drafts)} real email(s), covering {total_employees} flagged "
            "employee(s), to their resolved managers right now.\n\nThis cannot be undone. Continue?",
        )
        if not confirmed:
            return

        drafts = self._pending_drafts
        self._pending_drafts = None
        self.preview_button.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.loading_overlay.show()
        self.loading_overlay.update_progress(0, "Sending notifications...")

        def report_progress(stage: str, label: str | None = None, completed: int | None = None, total: int | None = None) -> None:
            if completed is not None and total:
                percent = int(completed / total * 100)
                self.after(0, lambda: self.loading_overlay.update_progress(percent, label or "Sending..."))

        def worker() -> None:
            try:
                result = send_notification_batch(drafts, progress_callback=report_progress)
                error = None
            except Exception as exc:
                result = None
                error = exc
            self.after(0, lambda: self._on_send_done(result, error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_send_done(self, result: dict | None, error: Exception | None) -> None:
        self.preview_button.configure(state="normal")
        self.loading_overlay.hide()
        if not self.winfo_exists():
            return

        if error is not None:
            logger.error(f"Work Distribution notification send failed: {error}")
            messagebox.showerror("Send Failed", f"Could not send notifications.\n\n{error}")
            self.notifications_status_label.configure(text="Send failed.", text_color=Color.ERROR)
            self._refresh_log()
            return

        self.notifications_status_label.configure(
            text=f"Send complete: {result['sent_count']} sent, {result['failed_count']} failed.",
            text_color=Color.SUCCESS if result["failed_count"] == 0 else Color.WARNING,
        )
        self._refresh_log()

    # --- Send Log ------------------------------------------------------

    def _build_log_card(self, outer) -> None:
        card = Card(outer)
        card.pack(fill="both", expand=True)

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Send Log", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        self.log_container = ctk.CTkFrame(body, fg_color="transparent")
        self.log_container.pack(fill="both", expand=True)

    def _refresh_log(self) -> None:
        for widget in self.log_container.winfo_children():
            widget.destroy()

        rows = get_recent_notifications()
        if not rows:
            EmptyState(self.log_container, "No notifications have been sent yet.").pack(fill="both", expand=True)
            return

        tree = styled_treeview(self.log_container, LOG_COLUMNS, LOG_HEADINGS, LOG_WIDTHS, height=10)
        tree.tag_configure("failed", background=Color.WARNING_SOFT)
        for i, row in enumerate(rows):
            tag = "failed" if row["status"] == "Failed" else None
            tree.insert(
                "", "end", iid=str(i),
                tags=(tag,) if tag else (),
                values=tuple(row[col] for col in LOG_COLUMNS),
            )
        tree.pack(fill="both", expand=True)
