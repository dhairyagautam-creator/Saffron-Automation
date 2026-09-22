"""Shared "Sync" button + banner + status line for every parameter/
settings page's cloud config sync (parameter sync project, Phase 5) --
one reusable widget instead of six near-identical copies across Path
Validator's Parameters page, Inventory/Work Distribution/Payment
Analytics' own Settings pages, and both recipient-list pages.

Matches the existing file-upload sync banner pattern (see
ui/review_uploads_page.py) exactly: a Sync button triggers a check; if
the remote config differs from local, a persistent banner appears
("New configuration available, changed by X") with an Apply button; a
status line always shows "Last synced [time] by [name]." Visibility of
both is gated only by whatever can_access already gates the surrounding
page -- no separate sync-specific permission (per explicit instruction).

Auto-push-on-save is NOT handled here -- each page's own Save button(s)
call app.parameter_sync_service.try_push_and_apply() directly (see that
function's own docstring), so a page with several independent Save
buttons still pushes its FULL current config after each one. This
widget only handles the PULL direction: checking for and applying
someone else's incoming change. Works identically for scalar-parameter
pages (`check_fn`/`apply_fn` wired to check_for_config_update/
apply_full_configuration) and recipient-list pages (wired to
app.recipient_sync_service's own check_for_recipient_update / a
union-merge apply_fn) -- this widget only knows "check produces a
changed/not-changed result with an updated_by_name and updated_at",
never the shape of the config itself.
"""

import customtkinter as ctk

from database.connection import to_local
from ui.components import PrimaryButton, SecondaryButton
from ui.theme import Color, Font, Spacing


class ParameterSyncPanel(ctk.CTkFrame):
    """`check_fn() -> dict` must return the same shape
    app.parameter_sync_service.check_for_config_update() /
    app.recipient_sync_service.check_for_recipient_update() do:
    {'ok': bool, 'reason': str (if not ok), 'changed': bool,
    'updated_at': datetime, 'updated_by_name': str, ...}. `apply_fn(result)
    -> (bool, str | None)` applies the PENDING remote state captured from
    the last check_fn() result (the whole dict is handed back so
    apply_fn can pull out whatever key it needs -- 'config' for scalar
    parameters, 'new_recipients' for a recipient union-merge) and
    returns (True, None) on success or (False, error_message) on a
    rejected/malformed remote blob. `on_applied()` is called after a
    successful apply so the page can reload its own displayed values."""

    def __init__(self, master, check_fn, apply_fn, on_applied, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._check_fn = check_fn
        self._apply_fn = apply_fn
        self._on_applied = on_applied
        self._pending_result: dict | None = None

        top_row = ctk.CTkFrame(self, fg_color="transparent")
        top_row.pack(fill="x")
        self._status_label = ctk.CTkLabel(
            top_row, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w"
        )
        self._status_label.pack(side="left", fill="x", expand=True)
        self._sync_button = SecondaryButton(top_row, text="Sync", command=self.check_now)
        self._sync_button.pack(side="right")

        self._banner_container = ctk.CTkFrame(self, fg_color="transparent")
        self._banner_container.pack(fill="x")

    def check_now(self) -> None:
        if not self._sync_button.winfo_exists():
            return
        self._sync_button.configure(state="disabled")
        result = self._check_fn()
        if self._sync_button.winfo_exists():
            self._sync_button.configure(state="normal")

        if not result["ok"]:
            reason = "Could not reach Supabase" if result.get("reason") == "offline" else "Last check failed"
            self._status_label.configure(text=f"⚠ {reason} -- showing this machine's last known state.")
            return

        self._render_status(result)
        if result["changed"]:
            self._pending_result = result
            self._render_banner(result["updated_by_name"])
        else:
            self._pending_result = None
            self._clear_banner()

    def _render_status(self, result: dict) -> None:
        local_time = to_local(result.get("updated_at"))
        time_text = local_time.strftime("%d %b %Y, %I:%M %p") if local_time else "an unknown time"
        self._status_label.configure(text=f"Last synced {time_text} by {result.get('updated_by_name', 'someone')}")

    def _clear_banner(self) -> None:
        for widget in self._banner_container.winfo_children():
            widget.destroy()

    def _render_banner(self, updated_by_name: str) -> None:
        self._clear_banner()
        banner = ctk.CTkFrame(self._banner_container, fg_color=Color.WARNING_SOFT, corner_radius=6)
        banner.pack(fill="x", pady=(Spacing.SM, 0))
        body = ctk.CTkFrame(banner, fg_color="transparent")
        body.pack(fill="x", padx=Spacing.MD, pady=Spacing.SM)
        ctk.CTkLabel(
            body, text=f"⚠  New configuration available, changed by {updated_by_name}.",
            font=Font.BODY, text_color=Color.WARNING, anchor="w", wraplength=500, justify="left",
        ).pack(side="left", fill="x", expand=True)
        PrimaryButton(body, text="Apply", command=self._on_apply_clicked).pack(side="right")

    def _on_apply_clicked(self) -> None:
        result = self._pending_result
        if result is None:
            return
        ok, error = self._apply_fn(result)
        if not ok:
            self._status_label.configure(text=f"Could not apply: {error}", text_color=Color.ERROR)
            return
        self._clear_banner()
        self._pending_result = None
        self._on_applied()
