"""The Home screen: the app's landing page, offering each module as a card.

Built entirely from existing components (Card, SectionHeader, PrimaryButton)
so it matches the rest of the app's look with no new styling primitives.

Which cards actually appear is decided by app/permissions.py, and WHICH
modules exist at all comes from app/module_registry.py -- the single
source of truth also used by ui/main_window.py's screen-permission gate
and User Management's module checklist. A module the current user isn't
granted (and isn't a super admin) is never built, not just disabled, so
there's nothing in the DOM/widget tree to find. Cards are rebuilt in
on_show() (called every time MainWindow.show_screen("Home") runs) rather
than once in __init__, since __init__ happens before login -- before
permission loading has run, every check would say False.

Adding a new module requires touching this file for exactly ZERO reasons:
register it once in app/module_registry.py and it appears here
automatically (icon, title, description, and the Open button's target
screen all come from that one ModuleDef).
"""

import customtkinter as ctk
from PIL import Image

from app import module_registry, permissions
from ui.components import Card, EmptyState, PrimaryButton, SecondaryButton, SectionHeader
from ui.icons import get_icon
from ui.theme import LOGO_PNG, Color, Font, Spacing

# Module cards wrap onto a new row after this many -- keeps the Home
# screen from stretching wider than the window as more modules are added.
MODULE_ROW_SIZE = 3


class ModuleCard(Card):
    """One large clickable module tile: icon, title, description, Open button."""

    def __init__(self, master, icon_name: str, title: str, description: str, command) -> None:
        super().__init__(master)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        icon_box = ctk.CTkLabel(
            body,
            image=get_icon(icon_name, size=48, color=Color.PRIMARY),
            text="",
            fg_color=Color.PRIMARY_SOFT,
            corner_radius=Spacing.SM,
            width=72,
            height=72,
        )
        icon_box.pack(anchor="w", pady=(0, Spacing.MD))

        ctk.CTkLabel(
            body, text=title, font=Font.H2, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=description,
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=280,
            justify="left",
        ).pack(anchor="w", pady=(Spacing.XS, Spacing.LG))

        PrimaryButton(body, text="Open", command=command, width=120).pack(anchor="w")


class HomePage(ctk.CTkFrame):
    """Landing screen shown first at launch: pick a module to open."""

    def __init__(self, master, on_open_module, on_logout) -> None:
        """`on_open_module(screen_name)` opens the given screen (normally
        `MainWindow.show_screen`) -- one generic callback instead of one
        per module, so a module registered in app/module_registry.py never
        needs a matching constructor parameter added here or in
        ui/main_window.py's own HomePage(...) call."""
        super().__init__(master, fg_color=Color.SURFACE)
        self._on_open_module = on_open_module

        SecondaryButton(self, text="Log out", width=100, command=on_logout).place(
            relx=1.0, rely=0.0, anchor="ne", x=-Spacing.LG, y=Spacing.LG
        )

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.place(relx=0.5, rely=0.5, anchor="center")

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(pady=(0, Spacing.XL))

        if LOGO_PNG.exists():
            logo_image = ctk.CTkImage(light_image=Image.open(LOGO_PNG), size=(48, 48))
            ctk.CTkLabel(header, image=logo_image, text="").pack(pady=(0, Spacing.SM))

        SectionHeader(
            header, "Saffron Automation", "Choose a module to get started"
        ).pack()

        self._cards = ctk.CTkFrame(outer, fg_color="transparent")
        self._cards.pack()

    def _rebuild_module_cards(self) -> None:
        """Wraps module cards into rows of MODULE_ROW_SIZE instead of one
        long horizontal row -- with five-plus modules a single row overflows
        a normal-width window. Each row is packed (not gridded) at its own
        natural width; since every row shares the same parent (`self._cards`,
        itself sized to its widest child), a shorter final row is
        automatically centered beneath a fuller one by pack's own default
        "center" anchor -- no manual offset math, and it stays correct as
        more modules are added later."""
        for widget in self._cards.winfo_children():
            widget.destroy()

        visible = [module for module in module_registry.all_modules() if permissions.can_access(module.key)]

        if not visible:
            EmptyState(self._cards, "No modules are enabled for your account. Contact your administrator.").pack(
                pady=Spacing.XL
            )
            return

        for row_start in range(0, len(visible), MODULE_ROW_SIZE):
            row_modules = visible[row_start : row_start + MODULE_ROW_SIZE]
            row = ctk.CTkFrame(self._cards, fg_color="transparent")
            row.pack(pady=(0 if row_start == 0 else Spacing.LG, 0))
            for i, module in enumerate(row_modules):
                ModuleCard(
                    row,
                    icon_name=module.icon_name,
                    title=module.title,
                    description=module.description,
                    command=lambda screen=module.screen_name: self._on_open_module(screen),
                ).pack(side="left", padx=(Spacing.LG if i > 0 else 0, 0))

    def on_show(self) -> None:
        """Called by MainWindow.show_screen() every time Home becomes
        active -- rebuilds the visible cards from the current user's
        permissions each time, rather than relying on stale state from a
        prior login."""
        self._rebuild_module_cards()
