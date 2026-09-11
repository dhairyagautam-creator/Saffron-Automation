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

Phase 1 email authority work removed the Notifications card (Preview/Send)
and the Send Log entirely -- sending is now ui/work_distribution_findings_page.py's
"Send Emails" button only (see app/work_distribution_notification_service.py).
This page is Sender Credentials + Hierarchy Workbooks only now.
"""

import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk
from loguru import logger

from app.hierarchy_parser import refresh_hierarchy
from app.smtp_service import test_connection
from app.work_distribution_email_settings_service import get_settings, save_settings
from app.work_distribution_upload_log_service import record_upload
from app.workbook_connections import WORKBOOK_NAMES, get_connections, get_status, set_connection
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader, StatusBadge
from ui.hierarchy_table_section import HierarchyTableSection
from ui.icons import get_icon
from ui.loading_overlay import LoadingOverlay
from ui.theme import Color, Font, Spacing

STATUS_BADGE_KIND = {"Connected": "success", "File Not Found": "error", "Not Configured": "neutral"}


class WorkDistributionEmailCenterPage(ctk.CTkFrame):
    """Sender credentials + hierarchy workbook connections."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self.path_labels: dict[str, ctk.CTkLabel] = {}
        self.status_badges: dict[str, StatusBadge] = {}
        self._build_widgets()
        self.loading_overlay = LoadingOverlay(self)

    def on_show(self) -> None:
        self._load_sender_credentials()
        self._refresh_connection_labels()
        self.hierarchy_section.load_from_db()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer,
            "Email Center",
            "Sender credentials and hierarchy data for Work Distribution notifications",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        self._build_sender_credentials_card(outer)
        self._build_hierarchy_card(outer)

        self.hierarchy_section = HierarchyTableSection(outer, export_filename_prefix="WorkDistributionHierarchy")
        self.hierarchy_section.pack(fill="both", expand=True, pady=(0, Spacing.LG))

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
                "The Gmail account Work Distribution sends coverage-report notifications from -- a "
                "separate account from Path Validator's and Inventory's own."
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
        self.sender_status_label.configure(text="")

    def _on_save_sender_clicked(self) -> None:
        # Automatic sending no longer exists (Phase 1 email authority work --
        # see ui/work_distribution_findings_page.py's Send Emails button).
        # automatic_sending_enabled is passed False and otherwise unread by
        # anything; left in place rather than migrated away.
        save_settings(
            self.sender_email_entry.get().strip(),
            self.sender_password_entry.get().strip(),
            False,
        )
        self.sender_status_label.configure(text="Sender credentials saved.", text_color=Color.SUCCESS)

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

