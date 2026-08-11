"""Uploads page: combines Inventory Monitoring's two upload workflows --
the current Inventory Report (ui/inventory_upload_page.py) and the
Previous Month Sales Report (ui/sales_upload_page.py) -- onto one screen,
per explicit instruction to reduce two separate nav entries to one.

Pure UI composition, nothing else: both existing page classes are
embedded here completely UNMODIFIED, each still owning its own
independent widgets, background-thread upload workflow, validation,
progress bar, loading overlay, and status label. Neither class's own
upload/validation/processing logic is touched in any way -- this file
only decides where the two are shown, stacked vertically on one page
instead of two separate nav entries. Each keeps its own existing
SectionHeader ("Inventory Upload" / "Previous Month Sales Upload") as its
own sub-section title within this page.
"""

import customtkinter as ctk

from ui.components import SectionHeader
from ui.inventory_upload_page import InventoryUploadPage
from ui.sales_upload_page import SalesUploadPage
from ui.theme import Color, Spacing


class InventoryUploadsPage(ctk.CTkFrame):
    """Both Inventory Monitoring upload workflows on one page -- see
    module docstring."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._build_widgets()

    def _build_widgets(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True)

        SectionHeader(
            outer, "Uploads", "Upload the current inventory report and the previous month's sales report"
        ).pack(anchor="w", padx=Spacing.LG, pady=(Spacing.LG, 0))

        InventoryUploadPage(outer).pack(fill="x")
        SalesUploadPage(outer).pack(fill="x")
