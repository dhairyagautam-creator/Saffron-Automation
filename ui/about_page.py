"""About page: application identity and version."""

import customtkinter as ctk
from PIL import Image

from app.version import APP_VERSION, BUILD_DATE, CHANNEL, COMPANY, DESCRIPTION, DEVELOPER
from ui.components import Card, SectionHeader
from ui.theme import LOGO_PNG, Color, Font, Spacing


class AboutPage(ctk.CTkFrame):
    """Static identity/version info."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._build_widgets()

    def _build_widgets(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(outer, "About", "Application information and version").pack(
            anchor="w", pady=(0, Spacing.LG)
        )

        card = Card(outer)
        card.pack(fill="x")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        header_row = ctk.CTkFrame(body, fg_color="transparent")
        header_row.pack(anchor="w", pady=(0, Spacing.MD))

        if LOGO_PNG.exists():
            logo_image = ctk.CTkImage(light_image=Image.open(LOGO_PNG), size=(56, 56))
            ctk.CTkLabel(header_row, image=logo_image, text="").pack(side="left", padx=(0, Spacing.MD))

        title_box = ctk.CTkFrame(header_row, fg_color="transparent")
        title_box.pack(side="left")
        ctk.CTkLabel(
            title_box, text="Saffron Automation", font=Font.H1, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box,
            text=f"Version v{APP_VERSION}  •  Built {BUILD_DATE}",
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
        ).pack(anchor="w")

        ctk.CTkFrame(body, fg_color=Color.DIVIDER, height=1).pack(fill="x", pady=Spacing.MD)

        for label, value in [
            ("Channel", "Development" if CHANNEL == "development" else "Production"),
            ("Description", DESCRIPTION),
            ("Developer", DEVELOPER),
            ("Company", COMPANY),
        ]:
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", pady=(0, Spacing.SM))
            ctk.CTkLabel(
                row, text=label, font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, width=140, anchor="w"
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=value, font=Font.BODY, text_color=Color.TEXT_SECONDARY, anchor="w", wraplength=500, justify="left"
            ).pack(side="left")
