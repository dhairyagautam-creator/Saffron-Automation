"""SendEmailsDialog: the confirm -> live-progress -> completion popup shared
by all three Phase 1 "Send Emails" buttons (Path Validator, Inventory, Work
Distribution) -- the interaction is identical across modules by explicit
instruction, so this is the one place that choreography lives rather than
three near-duplicate copies. Each module's own page supplies the
module-specific pieces (recipient count + pre-built drafts, the send
function itself, and an optional per-failure description) and gets back one
`on_complete(result)` callback once the user dismisses the completion popup.
"""

import threading
from typing import Callable, Optional

import customtkinter as ctk

from ui.components import PrimaryButton, SecondaryButton
from ui.theme import Color, Font, Spacing

_MAX_FAILURE_LINES_SHOWN = 5


class SendEmailsDialog(ctk.CTkToplevel):
    """`state` is "new_data" (never sent, or new data since the last send --
    shows a recipient count with Send/Cancel) or "resend_confirm" (nothing
    new since the last successful send -- shows the "send again?" wording
    with Yes/No). Both converge on the same live-countdown send step and the
    same completion popup once confirmed -- per instruction, confirming
    either popup starts the identical send flow.

    `send_fn(drafts, progress_callback) -> result` is called on a background
    thread; the UI is intentionally left non-interactive elsewhere for the
    duration (this dialog is modal via grab_set(), and the window's close
    button is disabled once sending starts) -- per instruction, this is not
    an oversight to fix later.

    `describe_failure_fn(draft) -> str | None`, if given, renders up to
    _MAX_FAILURE_LINES_SHOWN per-recipient failure reasons under a
    "N sent, M failed" result -- optional per instruction; a module whose
    send function doesn't cheaply expose a failure reason per draft can pass
    None and just get the count."""

    def __init__(
        self,
        parent,
        *,
        state: str,
        recipient_count: int,
        drafts: list,
        send_fn: Callable[[list, Callable], dict],
        on_complete: Callable[[dict], None],
        describe_failure_fn: Optional[Callable[[dict], str]] = None,
    ) -> None:
        super().__init__(parent)
        self._drafts = drafts
        self._send_fn = send_fn
        self._on_complete = on_complete
        self._describe_failure_fn = describe_failure_fn
        self._result: dict | None = None

        self.title("Send Emails")
        self.geometry("400x200")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(fg_color=Color.SURFACE)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._body = ctk.CTkFrame(self, fg_color="transparent")
        self._body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        self._render_confirm(state, recipient_count)
        self.after(0, self._center_on_parent, parent)

    def _center_on_parent(self, parent) -> None:
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")

    def _clear_body(self) -> None:
        for widget in self._body.winfo_children():
            widget.destroy()

    # --- Step 1: confirm ----------------------------------------------------

    def _render_confirm(self, state: str, recipient_count: int) -> None:
        self._clear_body()
        if state == "resend_confirm":
            message = (
                "No new reports have been uploaded since the last email was sent. "
                "Do you wish to send emails again?"
            )
            confirm_text, cancel_text = "Yes", "No"
        else:
            message = f"{recipient_count} recipient(s) will be emailed."
            confirm_text, cancel_text = "Send", "Cancel"

        ctk.CTkLabel(
            self._body, text=message, font=Font.BODY, text_color=Color.TEXT_PRIMARY,
            wraplength=340, justify="left",
        ).pack(anchor="w", pady=(0, Spacing.LG), fill="both", expand=True)

        button_row = ctk.CTkFrame(self._body, fg_color="transparent")
        button_row.pack(fill="x", side="bottom")
        SecondaryButton(button_row, text=cancel_text, command=self._on_cancel).pack(side="left")
        PrimaryButton(button_row, text=confirm_text, command=self._start_send).pack(side="right")

    def _on_cancel(self) -> None:
        self.destroy()

    # --- Step 2: sending, with a live countdown -----------------------------

    def _start_send(self) -> None:
        self._clear_body()
        total = len(self._drafts)
        self._progress_label = ctk.CTkLabel(
            self._body, text=f"0 of {total} sent", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY,
        )
        self._progress_label.pack(expand=True)
        # Can't be closed mid-send -- there is nothing sensible to cancel
        # into once SMTP sending has started for some recipients but not
        # others.
        self.protocol("WM_DELETE_WINDOW", lambda: None)

        def report_progress(stage, label=None, completed=None, total=None) -> None:
            if stage == "sending" and total:
                self.after(0, lambda: self._progress_label.configure(text=f"{completed} of {total} sent"))

        def worker() -> None:
            result = self._send_fn(self._drafts, report_progress)
            self.after(0, self._render_done, result)

        threading.Thread(target=worker, daemon=True).start()

    # --- Step 3: completion, requires an explicit OK ------------------------

    def _render_done(self, result: dict) -> None:
        self._result = result
        self._clear_body()

        sent = result.get("sent_count", 0)
        failed = result.get("failed_count", 0)
        if failed:
            text = f"{sent} sent, {failed} failed."
            if self._describe_failure_fn is not None:
                failure_lines = [
                    self._describe_failure_fn(d)
                    for d in result.get("drafts", [])
                    if d.get("status") == "Failed"
                ]
                failure_lines = [line for line in failure_lines if line]
                if failure_lines:
                    shown = failure_lines[:_MAX_FAILURE_LINES_SHOWN]
                    text += "\n\n" + "\n".join(f"• {line}" for line in shown)
                    remaining = len(failure_lines) - len(shown)
                    if remaining > 0:
                        text += f"\n…and {remaining} more."
        else:
            text = "Emails sent."

        ctk.CTkLabel(
            self._body, text=text, font=Font.BODY, text_color=Color.TEXT_PRIMARY,
            wraplength=340, justify="left",
        ).pack(anchor="w", pady=(0, Spacing.LG), fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self._finish)
        PrimaryButton(self._body, text="OK", command=self._finish).pack(side="bottom")

    def _finish(self) -> None:
        result = self._result
        self.destroy()
        self._on_complete(result)
