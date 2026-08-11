"""Reusable themed UI building blocks: cards, KPI tiles, status badges,
section headers, empty states, buttons, and a themed ttk.Treeview style —
so every page shares the same enterprise look instead of hand-rolling
widgets from scratch.
"""

from datetime import datetime
from tkinter import ttk

import customtkinter as ctk

from ui.icons import get_icon
from ui.theme import Color, Font, Radius, Spacing

_ttk_style_configured = False


def configure_ttk_style() -> None:
    """Apply the enterprise theme to ttk.Treeview widgets (used for tables).
    Safe to call more than once — the actual configuration only runs once."""
    global _ttk_style_configured
    if _ttk_style_configured:
        return

    style = ttk.Style()
    style.theme_use("clam")

    style.configure(
        "Saffron.Treeview",
        background=Color.CARD,
        fieldbackground=Color.CARD,
        foreground=Color.TEXT_PRIMARY,
        rowheight=28,
        borderwidth=0,
        font=(Font.FAMILY, 11),
    )
    style.configure(
        "Saffron.Treeview.Heading",
        background=Color.SURFACE,
        foreground=Color.TEXT_SECONDARY,
        borderwidth=0,
        font=(Font.FAMILY, 11, "bold"),
        relief="flat",
    )
    style.map(
        "Saffron.Treeview",
        background=[("selected", Color.PRIMARY_SOFT)],
        foreground=[("selected", Color.TEXT_PRIMARY)],
    )

    _ttk_style_configured = True


def styled_treeview(master, columns, headings, widths=None, height=10, scrollbars=True, **kwargs) -> ttk.Treeview:
    """Create a Treeview already themed and with headings/columns configured.

    With `scrollbars=True` (the default) a vertical and horizontal scrollbar
    are attached, so a table that is taller or wider than its container
    scrolls instead of clipping rows off the bottom or columns off the right
    edge — essential on smaller screens and higher display-scaling settings,
    where a multi-column table (Findings, Organization Data, Replenishment,
    Thresholds) would otherwise have its rightmost columns silently cut off.
    The scrollbars are packed into `master`; the caller still packs the
    returned tree itself (typically `tree.pack(fill="both", expand=True)`)
    into the remaining area. Pass `scrollbars=False` for small fixed tables
    that always fit.
    """
    configure_ttk_style()
    tree = ttk.Treeview(
        master, columns=columns, show="headings", height=height, style="Saffron.Treeview", **kwargs
    )
    for col in columns:
        tree.heading(col, text=headings.get(col, col))
        tree.column(col, width=(widths or {}).get(col, 120), anchor="w")

    if scrollbars:
        vsb = ttk.Scrollbar(master, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(master, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        # Packed before the caller packs the tree, so the tree's own
        # fill="both", expand=True fills the space left of the vertical bar and
        # above the horizontal bar.
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
    return tree


class Card(ctk.CTkFrame):
    """A rounded white card that sits above the light-gray page background."""

    def __init__(self, master, **kwargs):
        kwargs.setdefault("fg_color", Color.CARD)
        kwargs.setdefault("corner_radius", Radius.MD)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", Color.BORDER)
        super().__init__(master, **kwargs)


class SectionHeader(ctk.CTkFrame):
    """A page or section title, optionally with a subtitle beneath it."""

    def __init__(self, master, title: str, subtitle: str | None = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        title_label = ctk.CTkLabel(
            self, text=title, font=Font.H1, text_color=Color.TEXT_PRIMARY, anchor="w"
        )
        title_label.pack(anchor="w")

        if subtitle:
            ctk.CTkLabel(
                self, text=subtitle, font=Font.BODY, text_color=Color.TEXT_SECONDARY, anchor="w"
            ).pack(anchor="w", pady=(2, 0))


class KPICard(Card):
    """A stat tile: a large value with a label beneath and a colored accent bar."""

    def __init__(self, master, label: str, value: str = "—", accent: str = Color.PRIMARY, **kwargs):
        super().__init__(master, **kwargs)

        ctk.CTkFrame(self, fg_color=accent, width=4, corner_radius=2).pack(
            side="left", fill="y", pady=Spacing.SM
        )

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(side="left", fill="both", expand=True, padx=Spacing.MD, pady=Spacing.SM)

        self.value_label = ctk.CTkLabel(
            body, text=value, font=Font.KPI_VALUE, text_color=Color.TEXT_PRIMARY, anchor="w"
        )
        self.value_label.pack(anchor="w")

        ctk.CTkLabel(
            body, text=label, font=Font.KPI_LABEL, text_color=Color.TEXT_SECONDARY, anchor="w"
        ).pack(anchor="w", pady=(2, 0))

    def set_value(self, value: str) -> None:
        self.value_label.configure(text=value)


class StatusBadge(ctk.CTkLabel):
    """A small pill-shaped label for statuses like Connected / Open / Reviewed."""

    STYLES = {
        "success": (Color.SUCCESS_SOFT, Color.SUCCESS),
        "warning": (Color.WARNING_SOFT, Color.WARNING),
        "error": (Color.ERROR_SOFT, Color.ERROR),
        "info": (Color.INFO_SOFT, Color.INFO),
        "primary": (Color.PRIMARY_SOFT, Color.PRIMARY),
        "neutral": (Color.SURFACE, Color.TEXT_SECONDARY),
    }

    def __init__(self, master, text: str, kind: str = "neutral", **kwargs):
        bg, fg = self.STYLES.get(kind, self.STYLES["neutral"])
        super().__init__(
            master,
            text=f"  {text}  ",
            font=Font.SMALL_BOLD,
            fg_color=bg,
            text_color=fg,
            corner_radius=Radius.SM,
            **kwargs,
        )

    def set_status(self, text: str, kind: str) -> None:
        bg, fg = self.STYLES.get(kind, self.STYLES["neutral"])
        self.configure(text=f"  {text}  ", fg_color=bg, text_color=fg)


class EmptyState(ctk.CTkFrame):
    """A centered placeholder message shown when a table/section has no data."""

    def __init__(self, master, message: str, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        ctk.CTkLabel(self, text=message, font=Font.BODY, text_color=Color.TEXT_MUTED).pack(
            expand=True, pady=Spacing.XL
        )


# --- Upload-flow result banners + KPI row -----------------------------------
# Shared by every upload page's success/warning/error banner and post-upload
# summary row (Historical/Monthly Payment Reports, Outstanding Report, ...)
# so each one doesn't hand-roll its own copy.


def render_success_banner(container, message: str) -> None:
    banner = ctk.CTkFrame(container, fg_color=Color.SUCCESS_SOFT, corner_radius=Radius.SM)
    banner.pack(fill="x")
    ctk.CTkLabel(
        banner, text=f"✓  {message}", font=Font.BODY_BOLD, text_color=Color.SUCCESS, anchor="w"
    ).pack(anchor="w", padx=Spacing.MD, pady=Spacing.SM)


def render_warning_banner(container, message: str) -> None:
    banner = ctk.CTkFrame(container, fg_color=Color.WARNING_SOFT, corner_radius=Radius.SM)
    banner.pack(fill="x", pady=(Spacing.SM, 0))
    ctk.CTkLabel(
        banner, text=f"⚠  {message}", font=Font.BODY, text_color=Color.WARNING, anchor="w",
        wraplength=900, justify="left",
    ).pack(anchor="w", padx=Spacing.MD, pady=Spacing.SM)


def render_error_banner(container, title: str, items: list[str]) -> None:
    banner = ctk.CTkFrame(container, fg_color=Color.ERROR_SOFT, corner_radius=Radius.SM)
    banner.pack(fill="x")
    inner = ctk.CTkFrame(banner, fg_color="transparent")
    inner.pack(fill="x", padx=Spacing.MD, pady=Spacing.SM)
    ctk.CTkLabel(inner, text=f"{title}:", font=Font.BODY_BOLD, text_color=Color.ERROR, anchor="w").pack(anchor="w")
    for item in items:
        ctk.CTkLabel(inner, text=f"•  {item}", font=Font.BODY, text_color=Color.ERROR, anchor="w").pack(
            anchor="w", pady=(2, 0)
        )


def render_kpi_row(container, specs: list[str], values: list[str]) -> None:
    row = ctk.CTkFrame(container, fg_color="transparent")
    row.pack(fill="x")
    for i in range(len(specs)):
        row.grid_columnconfigure(i, weight=1)
    for i, (label, value) in enumerate(zip(specs, values)):
        card = KPICard(row, label=label, value=value, accent=Color.PRIMARY)
        card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else Spacing.SM, 0))


class PrimaryButton(ctk.CTkButton):
    """The main call-to-action button style: solid brand orange."""

    def __init__(self, master, **kwargs):
        kwargs.setdefault("fg_color", Color.PRIMARY)
        kwargs.setdefault("hover_color", Color.PRIMARY_HOVER)
        kwargs.setdefault("text_color", Color.TEXT_ON_PRIMARY)
        kwargs.setdefault("corner_radius", Radius.SM)
        kwargs.setdefault("font", Font.BODY_BOLD)
        kwargs.setdefault("height", 36)
        super().__init__(master, **kwargs)


class SecondaryButton(ctk.CTkButton):
    """A lower-emphasis, outlined button."""

    def __init__(self, master, **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        kwargs.setdefault("hover_color", Color.SURFACE)
        kwargs.setdefault("text_color", Color.TEXT_PRIMARY)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", Color.BORDER)
        kwargs.setdefault("corner_radius", Radius.SM)
        kwargs.setdefault("font", Font.BODY_BOLD)
        kwargs.setdefault("height", 36)
        super().__init__(master, **kwargs)


class CollapsibleSection(ctk.CTkFrame):
    """A card with a clickable chevron+title header that shows/hides a body
    frame -- generalizes the collapsible-card pattern
    ui/parameters_page.py's own `_build_section_card` already uses locally
    (identical chevron glyphs, identical click-to-toggle behavior), so any
    page needing an expandable section reuses the exact same look/feel
    instead of a second hand-rolled copy of that toggle logic.

    Pack child widgets into `.body` after construction. Pass `nested=True`
    for a section placed inside another CollapsibleSection's `.body` --
    uses a flat, borderless background instead of a second full Card
    border, so nested sections don't read as a card-inside-a-card."""

    def __init__(self, master, title: str, expanded: bool = True, nested: bool = False, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        if nested:
            self.card = ctk.CTkFrame(self, fg_color=Color.SURFACE, corner_radius=Radius.MD)
        else:
            self.card = Card(self)
        self.card.pack(fill="x")

        header = ctk.CTkFrame(self.card, fg_color="transparent")
        header.pack(fill="x", padx=Spacing.LG, pady=(Spacing.LG, 0))

        self.body = ctk.CTkFrame(self.card, fg_color="transparent")

        self._chevron_label = ctk.CTkLabel(
            header, text="▾", font=Font.H3, text_color=Color.TEXT_SECONDARY, width=20, anchor="w"
        )
        self._chevron_label.pack(side="left")
        title_font = Font.BODY_BOLD if nested else Font.H2
        title_label = ctk.CTkLabel(header, text=title, font=title_font, text_color=Color.TEXT_PRIMARY, anchor="w")
        title_label.pack(side="left", fill="x", expand=True)

        self._expanded = expanded

        def toggle(_event=None) -> None:
            self._expanded = not self._expanded
            self._apply_state()

        for widget in (header, self._chevron_label, title_label):
            widget.bind("<Button-1>", toggle)

        self._apply_state()

    def _apply_state(self) -> None:
        if self._expanded:
            self.body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)
            self._chevron_label.configure(text="▾")
        else:
            self.body.pack_forget()
            self._chevron_label.configure(text="▸")


class TabBar(ctk.CTkFrame):
    """A row of tab buttons that shows/hides corresponding content frames --
    built from the app's existing button/color/font tokens (no shared tab
    widget existed anywhere in the app before this), so it reads as native
    to the theme rather than a second design language. The active tab uses
    PrimaryButton's solid styling; inactive tabs use SecondaryButton's
    outlined styling -- exactly the two button looks every other page
    already uses, just applied to a tab strip.

    Usage: `tabs = TabBar(parent, ["A", "B"], on_change=fn)`, then pack
    each tab's own content frame and show/hide it from `on_change`."""

    def __init__(self, master, tab_names: list[str], default: str | None = None, on_change=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_change = on_change
        self._buttons: dict[str, ctk.CTkButton] = {}
        self.active_tab = default or tab_names[0]

        button_row = ctk.CTkFrame(self, fg_color="transparent")
        button_row.pack(fill="x")

        for name in tab_names:
            btn = ctk.CTkButton(
                button_row,
                text=name,
                font=Font.BODY_BOLD,
                corner_radius=Radius.SM,
                height=36,
                command=lambda n=name: self._select(n),
            )
            btn.pack(side="left", padx=(0, Spacing.SM))
            self._buttons[name] = btn

        ctk.CTkFrame(self, fg_color=Color.DIVIDER, height=1).pack(fill="x", pady=(Spacing.SM, 0))

        self._apply_styles()

    def select(self, name: str) -> None:
        """Programmatic tab switch (e.g. a caller navigating here from
        another page) -- identical effect to the user clicking that tab,
        including firing `on_change`."""
        self._select(name)

    def _select(self, name: str) -> None:
        if name == self.active_tab:
            return
        self.active_tab = name
        self._apply_styles()
        if self._on_change is not None:
            self._on_change(name)

    def _apply_styles(self) -> None:
        for name, btn in self._buttons.items():
            if name == self.active_tab:
                btn.configure(
                    fg_color=Color.PRIMARY, hover_color=Color.PRIMARY_HOVER,
                    text_color=Color.TEXT_ON_PRIMARY, border_width=0,
                )
            else:
                btn.configure(
                    fg_color="transparent", hover_color=Color.SURFACE,
                    text_color=Color.TEXT_SECONDARY, border_width=1, border_color=Color.BORDER,
                )


class ModuleRefreshControl(ctk.CTkFrame):
    """A single, reusable "Refresh" button + status label wired to
    app/module_refresh_service.py's generic refresh mechanism -- Version
    2.0, Milestone 22. Used identically by every cloud-synced module's
    shell (Path Validator, Inventory, ...) so there is exactly one
    implementation of "click Refresh, disable while running, re-render
    the active page if anything changed, show sync status" across the
    whole app, not one copy per module.

    `module_shell` is the owning module's shell widget (e.g.
    PathValidatorModule/InventoryModule) -- needs `.active_page` (str |
    None) and `.pages` (dict of name -> page widget) so a successful,
    changed refresh can re-render whichever page is currently visible via
    that page's own on_show(), the same "reload on navigate" convention
    every page already implements. Duck-typed, no shared base class
    required."""

    def __init__(self, master, module_shell, module_key: str, **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self._module_shell = module_shell
        self._module_key = module_key

        self.button = ctk.CTkButton(
            self,
            text="Refresh",
            image=get_icon("refresh", size=16, color=Color.TEXT_ON_PRIMARY),
            compound="left",
            font=Font.BODY,
            fg_color=Color.PRIMARY,
            hover_color=Color.PRIMARY_HOVER,
            text_color=Color.TEXT_ON_PRIMARY,
            corner_radius=Radius.SM,
            height=36,
            command=self._on_clicked,
        )
        self.button.pack(fill="x")

        self.status_label = ctk.CTkLabel(
            self, text="Not yet synced this session", font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w"
        )
        self.status_label.pack(fill="x", pady=(4, 0))

        # A shell rebuild (e.g. Path Validator's mode toggle) can happen
        # while a refresh started before it is still running -- reflect
        # that immediately instead of showing a stale, clickable "Refresh"
        # while one is in flight.
        from app.module_refresh_service import is_module_refreshing

        if is_module_refreshing(module_key):
            self.button.configure(state="disabled", text="Syncing…")
            self.status_label.configure(text="Syncing…")

    def _on_clicked(self) -> None:
        from app.module_refresh_service import refresh_module_async

        self.button.configure(state="disabled", text="Syncing…")
        self.status_label.configure(text="Syncing…")

        started = refresh_module_async(self._module_key, self._on_complete)
        if not started:
            # Something else (the background poller, if this module has
            # one) is already mid-sync -- there's no handle on that run's
            # completion, so just say so; the button naturally re-enables
            # next time either one finishes.
            self.status_label.configure(text="Already syncing…")

    def _on_complete(self, result) -> None:
        """Runs on the background thread refresh_module_async() spawned --
        marshal every widget touch back to the main thread via
        self.after(0, ...)."""

        def apply() -> None:
            if not self.winfo_exists():
                return
            self.button.configure(state="normal", text="Refresh")
            if result.success:
                stamp = (result.finished_at or datetime.now()).strftime("%I:%M %p")
                self.status_label.configure(text=f"Synced {stamp}")
                if result.changed and self._module_shell.active_page:
                    page = self._module_shell.pages.get(self._module_shell.active_page)
                    if page is not None and hasattr(page, "on_show"):
                        page.on_show()
            else:
                self.status_label.configure(text=f"Sync failed: {result.error_message}")

        self.after(0, apply)
