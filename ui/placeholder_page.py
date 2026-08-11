"""A simple placeholder page for sections that aren't built yet."""

import customtkinter as ctk

from ui.components import SecondaryButton
from ui.theme import Color, Font, Spacing


class PlaceholderPage(ctk.CTkFrame):
    """Shows a single centered message. Used for pages reserved for future work.

    `on_home`, if given, adds a "Back to Home" button below the message —
    used for top-level module placeholders (e.g. Inventory Monitoring)
    reachable from MainWindow's Home screen. Left as None (no button) for
    placeholders nested inside a module's own navigation, such as Path
    Validator's "Analytics Dashboard - Coming Soon" page, which already has
    its own way back via the sidebar."""

    def __init__(self, master, message: str, on_home=None) -> None:
        super().__init__(master, fg_color=Color.SURFACE)

        box = ctk.CTkFrame(self, fg_color="transparent")
        box.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(box, text=message, font=Font.H3, text_color=Color.TEXT_SECONDARY).pack()

        if on_home is not None:
            SecondaryButton(box, text="← Back to Home", command=on_home).pack(pady=(Spacing.MD, 0))
