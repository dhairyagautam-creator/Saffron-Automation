"""Uploads page: combines Inventory Monitoring's two upload workflows --
the current Inventory Report (ui/inventory_upload_page.py) and the
Previous Month Sales Report (ui/sales_upload_page.py) -- onto one screen,
per explicit instruction to reduce two separate nav entries to one.

Both existing page classes are embedded here essentially unmodified --
each still owns its own independent widgets, background-thread upload
workflow, validation, progress bar, loading overlay, and status label.
The only addition to each is an `on_uploaded` callback so this page can
refresh the sync status panel below after either one completes.

Sync (see docs/SYNC_DESIGN.md, docs/INVENTORY_SYNC_CONTEXT.md): extends
the exact mechanism ui/review_uploads_page.py already proved
(app/review_sync_service.py) to the two new slots app/inventory_sync_service.py
defines -- the same check-on-entry/Refresh-button, persistent
pending-changes banner, and "Pull Updates" flow, scoped down to 2 slots
instead of 12. The one persistent panel this page adds (per explicit
instruction: no changes to the Thresholds page or Dashboard) shows the
CURRENTLY ACTIVE Sales Report's filename, uploader, upload time, and when
thresholds were last generated from it -- always visible, not just after
a check finds something new, so a long-unchanged Sales Report is never
silently assumed current.
"""

from tkinter import messagebox

import customtkinter as ctk
from loguru import logger

from app.inventory_sync_service import apply_pending_updates, check_for_updates, display_name_for
from app.inventory_upload_service import INVENTORY_REPORT_SLOT, SALES_REPORT_SLOT, get_slot_state
from database.connection import to_local
from ui.background_task import run_in_background
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader, StatusBadge
from ui.icons import get_icon
from ui.inventory_upload_page import InventoryUploadPage
from ui.sales_upload_page import SalesUploadPage
from ui.theme import Color, Font, Radius, Spacing

_SLOT_LABELS = {SALES_REPORT_SLOT: "Sales Report", INVENTORY_REPORT_SLOT: "Inventory Report"}


class InventoryUploadsPage(ctk.CTkFrame):
    """Both Inventory Monitoring upload workflows on one page, plus the
    Inventory sync status panel and pull mechanism -- see module
    docstring."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._checking = False
        self._banner_dismissed = False
        self._build_widgets()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True)

        header_row = ctk.CTkFrame(outer, fg_color="transparent")
        header_row.pack(fill="x", padx=Spacing.LG, pady=(Spacing.LG, 0))
        SectionHeader(
            header_row, "Uploads", "Upload the current inventory report and the previous month's sales report"
        ).pack(side="left", anchor="w")
        self._refresh_button = SecondaryButton(
            header_row, text="Refresh", image=get_icon("refresh", size=14, color=Color.PRIMARY),
            command=self._run_check,
        )
        self._refresh_button.pack(side="right", anchor="n")

        self._sync_status_label = ctk.CTkLabel(
            outer, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w"
        )
        self._sync_status_label.pack(anchor="w", padx=Spacing.LG, pady=(Spacing.SM, 0))

        self._banner_container = ctk.CTkFrame(outer, fg_color="transparent")
        self._banner_container.pack(fill="x", padx=Spacing.LG)

        self._inventory_upload_widget = InventoryUploadPage(outer, on_uploaded=self._on_child_uploaded)
        self._inventory_upload_widget.pack(fill="x")

        # Persistent status panel, near the Sales Report control (below),
        # visible at all times regardless of recent activity -- see module
        # docstring.
        self._status_card = Card(outer)
        self._status_card.pack(fill="x", padx=Spacing.LG, pady=(0, Spacing.LG))
        self._render_status_panel()

        self._sales_upload_widget = SalesUploadPage(outer, on_uploaded=self._on_child_uploaded)
        self._sales_upload_widget.pack(fill="x")

    # --- Lifecycle -----------------------------------------------------------

    def on_show(self) -> None:
        self._render_status_panel()
        self._inventory_upload_widget.refresh_from_state()
        self._sales_upload_widget.refresh_from_state()
        self._run_check()

    def _on_child_uploaded(self) -> None:
        self._render_status_panel()

    # --- Status panel: currently active Sales Report -------------------------

    def _render_status_panel(self) -> None:
        if not self._status_card.winfo_exists():
            return
        for widget in self._status_card.winfo_children():
            widget.destroy()

        body = ctk.CTkFrame(self._status_card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text="Active Sales Report", font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")

        state = get_slot_state(SALES_REPORT_SLOT)
        if not state["uploaded"]:
            ctk.CTkLabel(
                body,
                text="No Sales Report uploaded or synced to this machine yet -- thresholds cannot be generated "
                "until one is.",
                font=Font.BODY, text_color=Color.TEXT_MUTED, anchor="w", wraplength=600, justify="left",
            ).pack(anchor="w", pady=(4, 0))
            return

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        StatusBadge(row, "ACTIVE", "success").pack(side="left", padx=(0, Spacing.SM))
        ctk.CTkLabel(
            row, text=state["filename"] or "", font=Font.BODY, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(side="left")
        if state.get("only_on_this_machine"):
            StatusBadge(row, "ONLY ON THIS MACHINE", "warning").pack(side="left", padx=(Spacing.SM, 0))

        meta_parts = []
        if state.get("uploaded_by"):
            meta_parts.append(f"Uploaded by {display_name_for(state['uploaded_by'])}")
        uploaded_local = to_local(state.get("uploaded_at"))
        if uploaded_local:
            meta_parts.append(f"on {uploaded_local.strftime('%d %b %Y, %I:%M %p')}")
        if meta_parts:
            ctk.CTkLabel(
                body, text="  ·  ".join(meta_parts), font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w"
            ).pack(anchor="w", pady=(2, 0))

        generated_local = to_local(state.get("thresholds_generated_at"))
        generated_text = (
            f"Thresholds last generated {generated_local.strftime('%d %b %Y, %I:%M %p')}"
            if generated_local
            else "Thresholds have not been generated from this file yet"
        )
        ctk.CTkLabel(
            body, text=generated_text, font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w"
        ).pack(anchor="w", pady=(2, 0))

    # --- Sync check (banner) --------------------------------------------------

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
                logger.error(f"Inventory sync: check raised unexpectedly: {error!r}")
                result = {"ok": False, "reason": "error"}

            self._banner_dismissed = False
            self._render_sync_status(result)
            self._render_banner(result)

        run_in_background(self, work, on_done=on_done)

    def _render_sync_status(self, result: dict) -> None:
        if not self._sync_status_label.winfo_exists():
            return
        if not result.get("ok"):
            reason = "Could not reach Supabase" if result.get("reason") == "offline" else "Last check failed"
            self._sync_status_label.configure(text=f"⚠ {reason} -- showing this machine's last known state.")
            return
        filled = result["filled_count"]
        total = result["total_count"]
        self._sync_status_label.configure(text=f"Synced -- {filled} / {total} slots filled.")

    # --- Banner ----------------------------------------------------------------

    def _clear_banner(self) -> None:
        for widget in self._banner_container.winfo_children():
            widget.destroy()

    def _render_banner(self, result: dict) -> None:
        self._clear_banner()
        if self._banner_dismissed or not result.get("ok"):
            return
        changed = result.get("changed") or []
        if not changed:
            return

        lines = [
            f"{_SLOT_LABELS[c['slot_id']]}: "
            + ("replaced by " if c["is_replacement"] else "new file from ")
            + f"{c['uploader_name']}."
            for c in changed
        ]

        banner = ctk.CTkFrame(self._banner_container, fg_color=Color.WARNING_SOFT, corner_radius=Radius.SM)
        banner.pack(fill="x", pady=(0, Spacing.LG))
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
            button_col, text="Pull Updates", command=lambda: self._on_pull_clicked(changed)
        ).pack(side="left", padx=(0, Spacing.SM))
        ctk.CTkButton(
            button_col, text="✕", width=28, height=28, fg_color="transparent",
            text_color=Color.WARNING, hover_color=Color.WARNING_SOFT,
            command=self._on_dismiss_banner,
        ).pack(side="left")

    def _on_dismiss_banner(self) -> None:
        self._banner_dismissed = True
        self._clear_banner()

    def _on_pull_clicked(self, changed: list[dict]) -> None:
        slot_ids = [c["slot_id"] for c in changed]

        self._clear_banner()
        ctk.CTkLabel(
            self._banner_container, text="Pulling updates...", font=Font.BODY, text_color=Color.INFO, anchor="w"
        ).pack(anchor="w", pady=(0, Spacing.SM))

        def work(_report_progress):
            return apply_pending_updates(slot_ids)

        def on_done(result, error):
            self._clear_banner()
            if error is not None:
                logger.error(f"Inventory sync: apply raised unexpectedly: {error!r}")
                messagebox.showerror("Sync Failed", f"Could not apply updates.\n\n{error}")
                self._run_check()
                return

            self._render_status_panel()
            # Same rendering path a local upload uses -- see
            # ui/inventory_upload_page.py / ui/sales_upload_page.py's
            # refresh_from_state() -- so a pulled file shows exactly the
            # same filename + green success text a local upload would,
            # with no visual way to tell them apart.
            self._inventory_upload_widget.refresh_from_state()
            self._sales_upload_widget.refresh_from_state()

            problems = []
            for f in result["failed"]:
                problems.append(f"{_SLOT_LABELS[f['slot_id']]}: {f['reason']}")
            for v in result["version_blocked"]:
                problems.append(
                    f"{_SLOT_LABELS[v['slot_id']]}: uploaded by a newer app version "
                    f"({v['remote_version']}) than this one -- update the app to apply it."
                )
            if problems:
                messagebox.showerror(
                    "Some Updates Could Not Be Applied",
                    "\n\n".join(problems) + "\n\nThese slots will show again next time you check.",
                )

            # Re-check: a partial failure (e.g. the no-sales-retained gate)
            # leaves the failed slot in `changed` again, so the banner
            # persists for exactly that slot.
            self._run_check()

        run_in_background(self, work, on_done=on_done)
