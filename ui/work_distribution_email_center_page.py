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
from app.hierarchy_upload_service import slot_id_for as hierarchy_slot_id_for
from app.smtp_service import test_connection
from app.work_distribution_email_settings_service import get_settings, save_settings
from app.work_distribution_sync_service import (
    HIERARCHY_SLOTS,
    SLOT_LABELS,
    apply_pending_updates,
    check_for_updates,
    upload_and_sync,
)
from app.work_distribution_upload_log_service import record_upload
from app.workbook_connections import WORKBOOK_NAMES, get_connections, get_status, set_connection
from ui.background_task import run_in_background
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader, StatusBadge
from ui.hierarchy_table_section import HierarchyTableSection
from ui.icons import get_icon
from ui.loading_overlay import LoadingOverlay
from ui.theme import Color, Font, Radius, Spacing

STATUS_BADGE_KIND = {"Connected": "success", "File Not Found": "error", "Not Configured": "neutral"}
# Work Distribution's own hierarchy dataset (module_registry's canonical
# key for this module) -- see app/hierarchy_parser.py's HIERARCHY_TABLES.
MODULE_KEY = "work_distribution"


class WorkDistributionEmailCenterPage(ctk.CTkFrame):
    """Sender credentials + hierarchy workbook connections."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self.path_labels: dict[str, ctk.CTkLabel] = {}
        self.status_badges: dict[str, StatusBadge] = {}
        self.advisory_labels: dict[str, ctk.CTkLabel] = {}
        self._checking = False
        self._banner_dismissed = False
        self._build_widgets()
        self.loading_overlay = LoadingOverlay(self)

    def on_show(self) -> None:
        self._load_sender_credentials()
        self._refresh_connection_labels()
        self.hierarchy_section.load_from_db()
        self._run_check()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(outer, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.SM))
        SectionHeader(
            header_row,
            "Email Center",
            "Sender credentials and hierarchy data for Work Distribution notifications",
        ).pack(side="left", anchor="w")
        self._refresh_button = SecondaryButton(
            header_row, text="Refresh", image=get_icon("refresh", size=14, color=Color.PRIMARY),
            command=self._run_check,
        )
        self._refresh_button.pack(side="right", anchor="n")

        self._sync_status_label = ctk.CTkLabel(
            outer, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w"
        )
        self._sync_status_label.pack(anchor="w", pady=(0, Spacing.MD))

        self._banner_container = ctk.CTkFrame(outer, fg_color="transparent")
        self._banner_container.pack(fill="x", pady=(0, Spacing.MD))

        self._build_sender_credentials_card(outer)
        self._build_hierarchy_card(outer)

        self.hierarchy_section = HierarchyTableSection(outer, MODULE_KEY, export_filename_prefix="WorkDistributionHierarchy")
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

            # Persistent, non-dismissible, per-division advisory (never a
            # gate -- see app/work_distribution_sync_service.py's module
            # docstring: RGD/ABM/RBM already resolve a missing hierarchy to
            # showing the employee code instead of a name, with no
            # blocking behavior anywhere). Packed on demand in
            # _refresh_connection_labels -- absent entirely once this
            # division is Connected, exactly as if it had never appeared.
            advisory_label = ctk.CTkLabel(
                row, text=(
                    f"⚠  Employee hierarchy not yet synced for {name} -- names will show as "
                    "codes until it is."
                ),
                font=Font.SMALL, text_color=Color.WARNING, anchor="w", wraplength=650, justify="left",
            )
            self.advisory_labels[name] = advisory_label

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
        connections = get_connections(MODULE_KEY)
        for name in WORKBOOK_NAMES:
            file_path = connections.get(name)
            status = get_status(file_path)
            self.path_labels[name].configure(text=file_path or "Not configured")
            self.status_badges[name].set_status(status, STATUS_BADGE_KIND[status])

            advisory = self.advisory_labels[name]
            if status == "Connected":
                advisory.pack_forget()
            else:
                advisory.pack(anchor="w", pady=(2, 0))

    def _on_browse_clicked(self, workbook_name: str) -> None:
        file_path = filedialog.askopenfilename(
            title=f"Select {workbook_name} Hierarchy Workbook",
            filetypes=[("Excel files", "*.xlsx")],
        )
        if not file_path:
            return

        logger.info(f"Loading {workbook_name} hierarchy workbook: {file_path}")
        record_upload(file_path, "Hierarchy Workbook", division=workbook_name)
        # Retains the file and repoints workbook_connections at the
        # retained copy (not the original picked path), then pushes to the
        # sync manifest -- see app/work_distribution_sync_service.py.
        # refresh_hierarchy() stays a separate manual action below,
        # unchanged.
        sync_result = upload_and_sync(hierarchy_slot_id_for(workbook_name), file_path)
        logger.info(f"Work Distribution hierarchy workbook connected: '{workbook_name}' -> {file_path}")
        if not sync_result.get("synced"):
            messagebox.showwarning("Not Synced", sync_result.get("sync_error") or "This file was not synced to the cloud.")
        self._refresh_connection_labels()

    def _on_refresh_clicked(self) -> None:
        self.refresh_button.configure(state="disabled")
        self.hierarchy_summary_label.configure(text="Refreshing hierarchy…")
        self.update_idletasks()

        def worker() -> None:
            try:
                stats = refresh_hierarchy(MODULE_KEY)
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

    # --- Sync check (banner) -- this page's 3 hierarchy slots only; the
    # other 9 are ui/work_distribution_upload_page.py's own banner, sharing
    # the same underlying check_for_updates()/apply_pending_updates() pair.

    def _run_check(self) -> None:
        if self._checking:
            return
        self._checking = True
        self._refresh_button.configure(state="disabled")

        def work(_report_progress):
            return check_for_updates()

        def on_done(result, error):
            self._checking = False
            if self._refresh_button.winfo_exists():
                self._refresh_button.configure(state="normal")
            if error is not None:
                logger.error(f"Work Distribution sync: check raised unexpectedly: {error!r}")
                result = {"ok": False, "reason": "error"}

            self._banner_dismissed = False
            self._render_sync_status(result)
            self._render_banner(result)
            if result.get("ok"):
                self._refresh_connection_labels()

        run_in_background(self, work, on_done=on_done)

    def _render_sync_status(self, result: dict) -> None:
        if not self._sync_status_label.winfo_exists():
            return
        if not result.get("ok"):
            reason = "Could not reach Supabase" if result.get("reason") == "offline" else "Last check failed"
            self._sync_status_label.configure(text=f"⚠ {reason} -- showing this machine's last known state.")
            return
        page_changed = [c for c in (result.get("changed") or []) if c["slot_id"] in HIERARCHY_SLOTS]
        self._sync_status_label.configure(
            text="Synced." if not page_changed else f"{len(page_changed)} update(s) available below."
        )

    def _clear_banner(self) -> None:
        for widget in self._banner_container.winfo_children():
            widget.destroy()

    def _render_banner(self, result: dict) -> None:
        self._clear_banner()
        if self._banner_dismissed or not result.get("ok"):
            return
        changed = [c for c in (result.get("changed") or []) if c["slot_id"] in HIERARCHY_SLOTS]
        if not changed:
            return

        replacements = [c for c in changed if c["is_replacement"]]
        first_fills = [c for c in changed if c["is_first_fill"]]

        lines = []
        for c in replacements:
            lines.append(f"{SLOT_LABELS[c['slot_id']]} was replaced by {c['uploader_name']}.")
        if first_fills:
            lines.append(f"{len(first_fills)} new hierarchy file{'s' if len(first_fills) != 1 else ''} available.")

        banner = ctk.CTkFrame(self._banner_container, fg_color=Color.WARNING_SOFT, corner_radius=Radius.SM)
        banner.pack(fill="x")
        body = ctk.CTkFrame(banner, fg_color="transparent")
        body.pack(fill="x", padx=Spacing.MD, pady=Spacing.SM)

        text_col = ctk.CTkFrame(body, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True)
        for line in lines:
            ctk.CTkLabel(
                text_col, text=f"⚠  {line}", font=Font.BODY, text_color=Color.WARNING, anchor="w",
                wraplength=650, justify="left",
            ).pack(anchor="w")

        button_col = ctk.CTkFrame(body, fg_color="transparent")
        button_col.pack(side="right")
        PrimaryButton(
            button_col, text="Pull Updates", command=lambda: self._on_pull_clicked([c["slot_id"] for c in changed])
        ).pack(side="left", padx=(0, Spacing.SM))
        ctk.CTkButton(
            button_col, text="✕", width=28, height=28, fg_color="transparent",
            text_color=Color.WARNING, hover_color=Color.WARNING_SOFT,
            command=self._on_dismiss_banner,
        ).pack(side="left")

    def _on_dismiss_banner(self) -> None:
        self._banner_dismissed = True
        self._clear_banner()

    def _on_pull_clicked(self, slot_ids: list[str]) -> None:
        self._clear_banner()
        ctk.CTkLabel(
            self._banner_container, text="Pulling updates...", font=Font.BODY, text_color=Color.INFO, anchor="w"
        ).pack(anchor="w")

        def work(_report_progress):
            return apply_pending_updates(slot_ids)

        def on_done(result, error):
            self._clear_banner()
            if error is not None:
                logger.error(f"Work Distribution sync: apply raised unexpectedly: {error!r}")
                messagebox.showerror("Sync Failed", f"Could not apply updates.\n\n{error}")
                self._run_check()
                return

            self._refresh_connection_labels()
            self.hierarchy_section.load_from_db()

            problems = []
            for f in result["failed"]:
                problems.append(f"{SLOT_LABELS[f['slot_id']]}: {f['reason']}")
            for v in result["version_blocked"]:
                problems.append(
                    f"{SLOT_LABELS[v['slot_id']]}: uploaded by a newer app version "
                    f"({v['remote_version']}) than this one -- update the app to apply it."
                )
            if problems:
                messagebox.showerror(
                    "Some Updates Could Not Be Applied",
                    "\n\n".join(problems) + "\n\nThese slots will show again next time you check.",
                )

            # Re-check: a partial failure (e.g. refresh_hierarchy() raised)
            # leaves the failed slot in `changed` again on the next check,
            # so the banner persists for exactly that division.
            self._run_check()

        run_in_background(self, work, on_done=on_done)

