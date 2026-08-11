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

import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk
from loguru import logger

from app.hierarchy_parser import refresh_hierarchy
from app.organization_data_sync_service import push_workbook
from app.workbook_connections import WORKBOOK_NAMES, get_connections, get_status, set_connection
from ui.components import Card, PrimaryButton, SectionHeader, StatusBadge
from ui.hierarchy_table_section import HierarchyTableSection
from ui.icons import get_icon
from ui.theme import Color, Font, Spacing

STATUS_BADGE_KIND = {"Connected": "success", "File Not Found": "error", "Not Configured": "neutral"}


class OrganizationDataPage(ctk.CTkFrame):
    """Connected Workbooks section (Onyx/Guardians/Xandra), a Refresh
    action, an import summary, and a searchable table of the resulting
    employee_hierarchy data."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)

        self.path_labels: dict[str, ctk.CTkLabel] = {}
        self.status_badges: dict[str, StatusBadge] = {}

        self._build_widgets()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer,
            "Organization Data",
            "Connect each division's Organization Data workbook — hierarchy and email "
            "addresses are both embedded, resolved purely from row order",
        ).pack(anchor="w", pady=(0, Spacing.LG))

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

        self.hierarchy_section = HierarchyTableSection(outer, export_filename_prefix="OrganizationData")
        self.hierarchy_section.pack(fill="both", expand=True)

    def on_show(self) -> None:
        """Called by MainWindow every time this page becomes visible.
        Pulling from the cloud is no longer this page's own job -- see the
        module-wide Refresh button/background poller in
        ui/path_validator_module.py, which call this page's on_show()
        again after a successful sync (see app/path_validator_refresh.py)."""
        self._refresh_connection_labels()
        self.hierarchy_section.load_from_db()

    def _refresh_connection_labels(self) -> None:
        connections = get_connections()
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

        set_connection(workbook_name, file_path)
        logger.info(f"Organization Data workbook connected: '{workbook_name}' -> {file_path}")
        self._refresh_connection_labels()
        self._push_workbook_in_background(workbook_name, file_path)

    def _push_workbook_in_background(self, workbook_name: str, file_path: str) -> None:
        def worker() -> None:
            try:
                push_workbook(workbook_name, file_path)
            except Exception as exc:
                logger.error(f"Failed to sync workbook '{workbook_name}' to the cloud: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_refresh_clicked(self) -> None:
        self.refresh_button.configure(state="disabled")
        self.summary_label.configure(text="Refreshing Organization Data…")
        self.update_idletasks()

        try:
            stats = refresh_hierarchy()
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
