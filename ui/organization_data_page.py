"""Organization Data page: connect each division's Organization Data
workbook (Onyx, Guardians, Xandra) and browse the resulting hierarchy —
this replaces the old separate Employee Hierarchy and Email Directory
pages, since each division's file now embeds employee/manager/email data
together (see app/hierarchy_parser.py).

The KPI grid + searchable/exportable hierarchy table is
ui.hierarchy_table_section.HierarchyTableSection -- extracted so
ui/work_distribution_email_center_page.py's own Hierarchy Workbooks card
can show byte-for-byte identical output with zero duplicated logic. This
page keeps ownership of the actual refresh_hierarchy() call (and its own
Browse/Connected-Workbooks UI, which differs per page) and simply hands
the resulting stats + a reload request to that shared component.
"""

from tkinter import filedialog, messagebox

import customtkinter as ctk
from loguru import logger

from app.hierarchy_parser import refresh_hierarchy
from app.hierarchy_upload_service import slot_id_for as hierarchy_slot_id_for
from app.path_validator_sync_service import (
    HIERARCHY_SLOTS,
    SLOT_LABELS,
    apply_pending_updates,
    check_for_updates,
    upload_and_sync,
)
from app.workbook_connections import WORKBOOK_NAMES, get_connections, get_status
from ui.background_task import run_in_background
from ui.components import Card, PrimaryButton, SecondaryButton, SectionHeader, StatusBadge
from ui.hierarchy_table_section import HierarchyTableSection
from ui.icons import get_icon
from ui.theme import Color, Font, Radius, Spacing

STATUS_BADGE_KIND = {"Connected": "success", "File Not Found": "error", "Not Configured": "neutral"}
# Path Validator's own hierarchy dataset (module_registry's canonical key
# for this module) -- see app/hierarchy_parser.py's HIERARCHY_TABLES.
MODULE_KEY = "employee_module"


class OrganizationDataPage(ctk.CTkFrame):
    """Connected Workbooks section (Onyx/Guardians/Xandra), a Refresh
    action, an import summary, and a searchable table of the resulting
    employee_hierarchy data."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)

        self.path_labels: dict[str, ctk.CTkLabel] = {}
        self.status_badges: dict[str, StatusBadge] = {}
        self._checking = False
        self._banner_dismissed = False

        self._build_widgets()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(outer, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.SM))
        SectionHeader(
            header_row,
            "Organization Data",
            "Connect each division's Organization Data workbook — hierarchy and email "
            "addresses are both embedded, resolved purely from row order",
        ).pack(side="left", anchor="w")
        self._refresh_sync_button = SecondaryButton(
            header_row, text="Refresh", image=get_icon("refresh", size=14, color=Color.PRIMARY),
            command=self._run_check,
        )
        self._refresh_sync_button.pack(side="right", anchor="n")

        self._sync_status_label = ctk.CTkLabel(
            outer, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w"
        )
        self._sync_status_label.pack(anchor="w", pady=(0, Spacing.SM))

        self._banner_container = ctk.CTkFrame(outer, fg_color="transparent")
        self._banner_container.pack(fill="x", pady=(0, Spacing.MD))

        workbooks_card = Card(outer)
        workbooks_card.pack(fill="x", pady=(0, Spacing.LG))

        workbooks_body = ctk.CTkFrame(workbooks_card, fg_color="transparent")
        workbooks_body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            workbooks_body,
            text="Connected Workbooks",
            font=Font.H3,
            text_color=Color.TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w", pady=(0, Spacing.SM))

        for name in WORKBOOK_NAMES:
            row = Card(workbooks_body, fg_color=Color.SURFACE, border_width=0)
            row.pack(fill="x", pady=4)

            row_inner = ctk.CTkFrame(row, fg_color="transparent")
            row_inner.pack(fill="x", padx=Spacing.MD, pady=Spacing.SM)

            ctk.CTkLabel(
                row_inner, text=name, width=100, anchor="w", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY
            ).pack(side="left")

            # The Browse button and status badge (both fixed-width) are packed
            # BEFORE the flexible, expanding path label below, so they always
            # claim their space first. Previously the Browse button was packed
            # last, after the expand=True path label had already consumed the
            # row's cavity — on a narrower row (a smaller screen or higher
            # display scaling on another computer) Tk squeezed that last-packed
            # control down to zero width, making the Browse button vanish
            # entirely on third-party installations while it still fit on the
            # wider development screen. Packing the fixed controls first makes
            # the path label yield space instead, so the button never
            # disappears regardless of screen size.
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
            outer,
            text="Refresh Organization Data",
            image=get_icon("refresh", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_refresh_clicked,
        )
        self.refresh_button.pack(pady=(0, Spacing.SM))

        self.summary_label = ctk.CTkLabel(
            outer,
            text="",
            font=Font.SMALL,
            text_color=Color.TEXT_SECONDARY,
            wraplength=700,
            justify="center",
        )
        self.summary_label.pack(pady=(0, Spacing.MD))

        self.hierarchy_section = HierarchyTableSection(outer, MODULE_KEY, export_filename_prefix="OrganizationData")
        self.hierarchy_section.pack(fill="both", expand=True)

    def on_show(self) -> None:
        """Called by MainWindow every time this page becomes visible --
        reloads connection labels and the hierarchy table from local data.

        Deliberately does NOT call _run_check() (the sync check) -- by
        explicit design, checking the manifest only ever happens via an
        explicit click on the "Refresh" button, never automatically on
        page visit/navigation."""
        self._refresh_connection_labels()
        self.hierarchy_section.load_from_db()

    def _refresh_connection_labels(self) -> None:
        connections = get_connections(MODULE_KEY)
        for name in WORKBOOK_NAMES:
            file_path = connections.get(name)
            status = get_status(file_path)
            self.path_labels[name].configure(text=file_path or "Not configured")
            self.status_badges[name].set_status(status, STATUS_BADGE_KIND[status])

    def _on_browse_clicked(self, workbook_name: str) -> None:
        file_path = filedialog.askopenfilename(
            title=f"Select {workbook_name} Organization Data Workbook",
            filetypes=[("Excel files", "*.xlsx")],
        )
        if not file_path:
            return

        # Retains the file and repoints workbook_connections at the
        # retained copy (not the original picked path), then pushes to the
        # sync manifest -- see app/path_validator_sync_service.py.
        # refresh_hierarchy() stays a separate manual action below,
        # unchanged.
        sync_result = upload_and_sync(hierarchy_slot_id_for(workbook_name), file_path)
        logger.info(f"Organization Data workbook connected: '{workbook_name}' -> {file_path}")
        if not sync_result.get("synced"):
            messagebox.showwarning("Not Synced", sync_result.get("sync_error") or "This file was not synced to the cloud.")
        self._refresh_connection_labels()

    def _on_refresh_clicked(self) -> None:
        self.refresh_button.configure(state="disabled")
        self.summary_label.configure(text="Refreshing Organization Data…")
        self.update_idletasks()

        try:
            stats = refresh_hierarchy(MODULE_KEY)
        except Exception as exc:
            logger.error(f"Refreshing Organization Data failed: {exc}")
            messagebox.showerror("Refresh Failed", f"Could not refresh Organization Data.\n\n{exc}")
            self.summary_label.configure(text="Refresh failed.")
            self.refresh_button.configure(state="normal")
            return

        self.hierarchy_section.load_from_db()
        self.hierarchy_section.update_kpis(stats)

        if not stats["workbooks_read"]:
            self.summary_label.configure(
                text="No Organization Data workbooks are connected yet — connect at least one above and refresh again."
            )
            self.refresh_button.configure(state="normal")
            return

        summary = f"Processed {stats['worksheets_processed']} worksheet(s) from {', '.join(stats['workbooks_read'])}."
        if stats["workbooks_skipped"]:
            summary += " Skipped: " + ", ".join(
                f"{name} ({reason})" for name, reason in stats["workbooks_skipped"].items()
            )
        self.summary_label.configure(text=summary)
        self.refresh_button.configure(state="normal")

    # --- Sync check (banner) -- see app/path_validator_sync_service.py --
    # this page's 3 hierarchy slots only.

    def _run_check(self) -> None:
        if self._checking:
            return
        self._checking = True
        self._refresh_sync_button.configure(state="disabled")

        def work(_report_progress):
            return check_for_updates()

        def on_done(result, error):
            self._checking = False
            if self._refresh_sync_button.winfo_exists():
                self._refresh_sync_button.configure(state="normal")
            if error is not None:
                logger.error(f"Path Validator sync: check raised unexpectedly: {error!r}")
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
                logger.error(f"Path Validator sync: apply raised unexpectedly: {error!r}")
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
