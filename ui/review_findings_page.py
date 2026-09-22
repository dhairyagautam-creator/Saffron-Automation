"""Review System -- Send Emails page (the module's Findings-page
equivalent, see ui/work_distribution_findings_page.py for the reference
shape this mirrors: SectionHeader + SendEmailsButton top-right, gated by
can_send_emails, opening the same shared ui.send_emails_dialog.SendEmailsDialog).

Review System has no per-import/per-finding concept the way Path
Validator or Work Distribution do -- what it sends is one combined
Opus Summary + Coverage Summary + RGD Visit and Support workbook per BM
(see app/review_notification_service.py), across ALL THREE divisions in
a single action, gated entirely on whether every division has all three
report types ready (a single not-ready division blocks the whole send --
there is no partial send). This page's body is therefore a simple
per-division readiness grid rather than a findings table -- there is
nothing else to review or filter here."""

import threading

import customtkinter as ctk

from app.email_send_history_service import format_relative_time
from app.permissions import can_send_emails
from app.review_coverage_service import DIVISIONS, coverage_prerequisites_ready
from app.review_notification_service import (
    all_divisions_data_state,
    all_divisions_ready,
    build_notification_batch_all_divisions,
    send_button_state,
    send_notification_batch,
)
from app.review_opus_service import OPUS_HQ_BLOCKS_BY_DIVISION, opus_prerequisites_ready
from app.review_rgd_service import rgd_prerequisites_ready
from ui.components import Card, SectionHeader, SendEmailsButton, StatusBadge
from ui.send_emails_dialog import SendEmailsDialog
from ui.theme import Color, Font, Spacing

REPORT_LABELS = ("Opus Summary", "Coverage Summary", "RGD Visit and Support")


def _report_ready_flags(division: str) -> tuple[bool, bool, bool]:
    """(opus_ready, coverage_ready, rgd_ready) for `division` -- the same
    three checks app.review_notification_service._division_reports_ready
    combines into one bool; broken out here so the page can show which
    specific report is blocking a division, not just a pass/fail dot."""
    opus_ready = OPUS_HQ_BLOCKS_BY_DIVISION.get(division) is not None and opus_prerequisites_ready(division)[0]
    coverage_ready = coverage_prerequisites_ready(division)[0]
    rgd_ready = rgd_prerequisites_ready(division)[0]
    return opus_ready, coverage_ready, rgd_ready


class ReviewFindingsPage(ctk.CTkFrame):
    """Send Emails for Review System -- one combined workbook per BM,
    across all three divisions, gated on can_send_emails("review_system")
    and on every division having all three report types ready."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._build_widgets()

    def on_show(self) -> None:
        self._refresh_readiness()
        self._refresh_send_emails_button()

    def _build_widgets(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(outer, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.LG))
        SectionHeader(
            header_row, "Send Emails",
            "Send each BM's combined Opus Summary, Coverage Summary, and RGD Visit and "
            "Support to their ABM -- covers all three divisions at once",
        ).pack(side="left", anchor="w")
        self.send_emails_button = SendEmailsButton(header_row, command=self._on_send_emails_clicked)
        self.send_emails_button.pack(side="right", anchor="e")

        self._readiness_card = Card(outer)
        self._readiness_card.pack(fill="x")

        self._refresh_readiness()

    # --- Readiness grid ------------------------------------------------

    def _refresh_readiness(self) -> None:
        if not self._readiness_card.winfo_exists():
            return
        for widget in self._readiness_card.winfo_children():
            widget.destroy()

        body = ctk.CTkFrame(self._readiness_card, fg_color="transparent")
        body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Report Readiness", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.MD))
        ctk.CTkLabel(
            body,
            text="Every division needs all three report types ready before Send Emails is enabled.",
            font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        header_row = ctk.CTkFrame(body, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.SM))
        ctk.CTkLabel(header_row, text="", width=110).pack(side="left")
        for label in REPORT_LABELS:
            ctk.CTkLabel(
                header_row, text=label, font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY,
                width=170, anchor="w",
            ).pack(side="left")

        for division in DIVISIONS:
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", pady=(0, Spacing.SM))
            ctk.CTkLabel(
                row, text=division, font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY,
                width=110, anchor="w",
            ).pack(side="left")
            for ready in _report_ready_flags(division):
                cell = ctk.CTkFrame(row, fg_color="transparent", width=170)
                cell.pack(side="left")
                StatusBadge(
                    cell, "Ready" if ready else "Not Ready", "success" if ready else "warning"
                ).pack(anchor="w")

    # --- Send Emails -----------------------------------------------------

    def _refresh_send_emails_button(self) -> None:
        if not can_send_emails("review_system"):
            self.send_emails_button.set_state(enabled=False, subtext="For authoritative users only.")
            self.send_emails_button.set_status_line("")
            return

        if not all_divisions_ready():
            self.send_emails_button.set_state(
                enabled=False, subtext="All 3 report types must be ready for every division."
            )
            _, _, last_send = all_divisions_data_state()
            self._render_send_status_line(last_send)
            return

        has_data, changed, last_send = all_divisions_data_state()
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
        if not can_send_emails("review_system") or not all_divisions_ready():
            return  # defensive -- button should already be disabled

        self.send_emails_button.button.configure(state="disabled", text="Preparing...")

        def worker() -> None:
            drafts = build_notification_batch_all_divisions()
            self.after(0, self._on_batch_built, drafts)

        threading.Thread(target=worker, daemon=True).start()

    def _on_batch_built(self, drafts: list) -> None:
        self.send_emails_button.button.configure(text="Send Emails")
        self._refresh_send_emails_button()

        _, changed, _ = all_divisions_data_state()
        state = send_button_state(has_data=True, changed_since_last_send=changed)

        SendEmailsDialog(
            self.winfo_toplevel(),
            state=state,
            recipient_count=len(drafts),
            drafts=drafts,
            send_fn=lambda d, progress_cb: send_notification_batch(d, progress_callback=progress_cb),
            describe_failure_fn=lambda d: f"{d.get('recipient_email', '?')}: {d.get('error_message', 'unknown error')}",
            on_complete=lambda result: self.on_show(),
        )
