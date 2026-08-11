"""Email Center: monitoring page for the active session's manager
notification emails, plus Master Email Recipients management.

The monitoring half has no manual action — no Preview, no Send, no Send
Selected, no Send All, no Draft workflow. The entire email workflow runs on
its own right after Run Analysis finishes on the Operations page (see
ui/operations_page.py and app/notification_service.py): the rule engine
flags employees, the hierarchy resolves each one's manager, the email
directory resolves that manager's address, flagged coordinates are
reverse-geocoded, findings are grouped into one consolidated email per
manager, and — if Automatic Email Sending is enabled on Settings — those
emails are sent immediately using the saved Gmail credentials. That half
only reports what happened: KPIs plus a send log with any errors.

Master Email Recipients (ui/master_email_recipients_section.py) is packed
at the bottom, below the Send Log, per instruction: it's rarely configured
and doesn't warrant its own sidebar page, so everything email-related now
lives on this one page. Purely a UI relocation -- the section's own CRUD/
dialog logic is untouched, and this page's own monitoring half (KPIs, Send
Log, polling) is unaffected by its presence.
"""

from tkinter import messagebox

import customtkinter as ctk

from app.feature_flags_service import is_feature_enabled
from app.findings_service import get_notification_status_counts
from app.send_state import get_progress, is_sending
from app.session_state import get_active_import_id
from app.table_export_service import default_export_filename, export_rows_with_ui
from database.connection import get_session
from database.models import EmailNotification
from ui.components import Card, EmptyState, KPICard, PrimaryButton, SectionHeader, styled_treeview
from ui.icons import get_icon
from ui.master_email_recipients_section import MasterEmailRecipientsSection
from ui.theme import Color, Font, Spacing

LOG_COLUMNS = ("manager_name", "manager_email", "employees", "status", "sent_at", "error")
LOG_HEADINGS = {
    "manager_name": "Manager",
    "manager_email": "Email",
    "employees": "Employees",
    "status": "Status",
    "sent_at": "Sent At",
    "error": "Error",
}
LOG_WIDTHS = {
    "manager_name": 160,
    "manager_email": 200,
    "employees": 80,
    "status": 90,
    "sent_at": 140,
    "error": 260,
}

# While a send is running, poll this often so the page updates on its own —
# the user never has to click anything to see progress.
POLL_INTERVAL_MS = 1000


class EmailCenterPage(ctk.CTkFrame):
    """KPI cards plus a send log — read-only monitoring of the fully
    automatic email workflow. Auto-refreshes while a send is in progress."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)

        self.kpi_cards: dict[str, KPICard] = {}
        self._batches_by_id: dict[int, EmailNotification] = {}
        self._current_display_rows: list[dict] = []
        self._polling = False
        self._build_widgets()

    def _build_widgets(self) -> None:
        # CTkScrollableFrame (not a plain CTkFrame) -- KPIs + Send Log +
        # the Master Email Recipients section together routinely exceed a
        # smaller window's height, same reasoning as the identical fix
        # already applied to ui/inventory_settings_page.py and
        # ui/inventory_automated_emails_page.py. Without this, anything
        # below the visible viewport (the Recipients section, added at the
        # bottom) is simply unreachable -- no scrollbar, no way to get to it.
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer,
            "Email Center",
            "Monitoring only — manager emails are generated and sent automatically after each analysis",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        kpi_row = ctk.CTkFrame(outer, fg_color="transparent")
        kpi_row.pack(fill="x", pady=(0, Spacing.MD))
        kpi_specs = [
            ("Pending", Color.INFO),
            ("Sending", Color.INFO),
            ("Sent", Color.SUCCESS),
            ("Failed", Color.ERROR),
        ]
        # Hospital Suppression is experimental — its KPI only shows when the
        # feature is on for the current mode (hidden from production).
        self._hospital_kpi_shown = is_feature_enabled("hospital_suppression")
        if self._hospital_kpi_shown:
            kpi_specs.append(("Hospital Suppressed", Color.WARNING))
        # Region Suppression (app/region_suppression.py) is a separate,
        # always-on business rule -- unlike Hospital Suppression, its KPI is
        # never hidden behind a feature flag.
        kpi_specs.append(("Region Suppressed", Color.PRIMARY))
        kpi_specs += [
            ("Total Managers Emailed", Color.INFO),
            ("Total Employees Included", Color.PRIMARY),
        ]
        for i in range(4):
            kpi_row.grid_columnconfigure(i, weight=1)
        for i, (label, accent) in enumerate(kpi_specs):
            row_idx, col_idx = divmod(i, 4)
            card = KPICard(kpi_row, label=label, value="0", accent=accent)
            card.grid(
                row=row_idx,
                column=col_idx,
                sticky="ew",
                padx=(0 if col_idx == 0 else Spacing.SM, 0),
                pady=(0 if row_idx == 0 else Spacing.SM, 0),
            )
            self.kpi_cards[label] = card

        self.summary_label = ctk.CTkLabel(
            outer, text="", font=Font.SMALL, text_color=Color.TEXT_SECONDARY, wraplength=800, justify="left"
        )
        self.summary_label.pack(anchor="w", pady=(0, Spacing.MD))

        log_card = Card(outer)
        log_card.pack(fill="both", expand=True)

        log_body = ctk.CTkFrame(log_card, fg_color="transparent")
        log_body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        log_header_row = ctk.CTkFrame(log_body, fg_color="transparent")
        log_header_row.pack(fill="x", pady=(0, Spacing.SM))

        ctk.CTkLabel(
            log_header_row, text="Send Log", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(side="left")

        self.export_button = PrimaryButton(
            log_header_row,
            text="Export",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_export_clicked,
        )
        self.export_button.pack(side="right")

        self.log_container = ctk.CTkFrame(log_body, fg_color="transparent")
        self.log_container.pack(fill="both", expand=True)

        # Master Email Recipients -- packed below the Send Log, per
        # instruction (rarely configured, doesn't warrant its own sidebar
        # page). Self-contained: owns its own Card/heading/Add-Recipient
        # dialog, so embedding it here is just packing one more frame.
        self.master_recipients_section = MasterEmailRecipientsSection(outer)
        self.master_recipients_section.pack(fill="both", expand=True, pady=(Spacing.LG, 0))

    def on_show(self) -> None:
        """Called by MainWindow every time this page becomes visible."""
        self._refresh()
        self._ensure_polling()
        self.master_recipients_section.on_show()

    def _ensure_polling(self) -> None:
        """Keep refreshing on a timer while a send is in progress, so the
        page updates itself with no user action. Stops on its own once the
        send finishes or this page is no longer showing."""
        if self._polling:
            return
        if not is_sending(get_active_import_id()):
            return
        self._polling = True
        self._poll_tick()

    def _poll_tick(self) -> None:
        if not self.winfo_exists():
            self._polling = False
            return

        self._refresh()

        if is_sending(get_active_import_id()):
            self.after(POLL_INTERVAL_MS, self._poll_tick)
        else:
            self._polling = False

    def _refresh(self) -> None:
        self._refresh_kpis()
        self._render_log()

    def _sending_kpi_text(self, sending_now: bool) -> str:
        """"Idle", "In Progress" (no per-item detail yet), or a live
        "Sending: RBM 2/6" once app.notification_service.send_all_emails
        starts reporting per-email progress via app.send_state."""
        if not sending_now:
            return "Idle"
        progress = get_progress()
        if progress.get("stage") == "sending" and progress.get("total"):
            label = progress.get("label") or "email"
            return f"Sending: {label} {progress['completed']}/{progress['total']}"
        return "In Progress"

    def _refresh_kpis(self) -> None:
        import_id = get_active_import_id()

        if import_id is None:
            for card in self.kpi_cards.values():
                card.set_value("0")
            self.summary_label.configure(text="No active session file — import a workbook on Operations first.")
            return

        # Per-finding outcome (Pending/Hospital Suppressed) is tracked on
        # each finding's own notification_status — a finding that's already
        # been suppressed is never "still Pending", unlike the old
        # Open-review-status count this used to use (see
        # app/findings_service.get_notification_status_counts).
        notification_counts = get_notification_status_counts(import_id)

        session = get_session()
        try:
            rows = session.query(EmailNotification).filter_by(import_id=import_id).all()
        finally:
            session.close()

        sent_rows = [r for r in rows if r.status == "Sent"]
        failed_rows = [r for r in rows if r.status == "Failed"]
        managers_emailed = {r.manager_email for r in sent_rows if r.manager_email}
        employees_included = sum(len(r.finding_ids.split(",")) for r in sent_rows if r.finding_ids)

        sending_now = is_sending(import_id)

        self.kpi_cards["Pending"].set_value(f"{notification_counts['Pending']:,}")
        self.kpi_cards["Sending"].set_value(self._sending_kpi_text(sending_now))
        self.kpi_cards["Sent"].set_value(f"{len(sent_rows):,}")
        self.kpi_cards["Failed"].set_value(f"{len(failed_rows):,}")
        if self._hospital_kpi_shown:
            self.kpi_cards["Hospital Suppressed"].set_value(f"{notification_counts['Hospital Suppressed']:,}")
        self.kpi_cards["Region Suppressed"].set_value(f"{notification_counts['Suppressed - Region Rule']:,}")
        self.kpi_cards["Total Managers Emailed"].set_value(f"{len(managers_emailed):,}")
        self.kpi_cards["Total Employees Included"].set_value(f"{employees_included:,}")

        draft_rows = [r for r in rows if r.status == "Draft"]

        if sending_now:
            self.summary_label.configure(text="Sending manager emails now…")
        elif rows and not sent_rows and not failed_rows and draft_rows:
            # Automatic Email Sending was off for this run (see the Settings
            # page) -- these batches were fully generated but deliberately
            # never sent, so "0 sent, 0 failed" alone would misleadingly
            # read as if sending was attempted and silently did nothing.
            self.summary_label.configure(
                text=(
                    f"{len(draft_rows):,} email draft(s) generated for review — Automatic Email Sending "
                    "is off, so nothing has been sent."
                )
            )
        elif rows:
            self.summary_label.configure(
                text=(
                    f"{len(sent_rows):,} sent, {len(failed_rows):,} failed out of {len(rows):,} "
                    "batch(es) for the active session."
                )
            )
        else:
            self.summary_label.configure(text="No email batches yet for the active session.")

    def _on_export_clicked(self) -> None:
        """Exports `self._current_display_rows` -- the exact list
        `_render_log()` just used to populate the Treeview."""
        export_rows_with_ui(
            self,
            rows=self._current_display_rows,
            columns=LOG_COLUMNS,
            headings=LOG_HEADINGS,
            suggested_filename=default_export_filename("EmailSendLog"),
            sheet_title="Send Log",
        )

    def _render_log(self) -> None:
        for widget in self.log_container.winfo_children():
            widget.destroy()
        self._current_display_rows = []

        import_id = get_active_import_id()
        if import_id is None:
            EmptyState(
                self.log_container, "No active session file — import a workbook on Operations first."
            ).pack(fill="both", expand=True)
            return

        session = get_session()
        try:
            rows = (
                session.query(EmailNotification)
                .filter_by(import_id=import_id)
                .order_by(EmailNotification.created_at.desc())
                .all()
            )
        finally:
            session.close()

        if not rows:
            EmptyState(self.log_container, "No email batches yet for the active session.").pack(
                fill="both", expand=True
            )
            return

        self._batches_by_id = {row.id: row for row in rows}

        tree = styled_treeview(self.log_container, LOG_COLUMNS, LOG_HEADINGS, LOG_WIDTHS, height=14)
        for row in rows:
            employee_count = len(row.finding_ids.split(",")) if row.finding_ids else 0
            sent_at_text = row.sent_at.strftime("%Y-%m-%d %H:%M") if row.sent_at else "-"
            # Same dict both feeds the Treeview's own `values=` tuple and the
            # exported row (Milestone 55) -- one source of truth, never two
            # separately-maintained value lists.
            display_row = {
                "manager_name": row.manager_name or "-",
                "manager_email": row.manager_email or "-",
                "employees": employee_count,
                "status": row.status,
                "sent_at": sent_at_text,
                "error": row.error_message or "",
            }
            self._current_display_rows.append(display_row)
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
        attempted) — a read-only inspection, not a resend action."""
        selection = tree.selection()
        if not selection:
            return
        row = self._batches_by_id.get(int(selection[0]))
        if row is None:
            return
        messagebox.showinfo(row.subject, row.body)
