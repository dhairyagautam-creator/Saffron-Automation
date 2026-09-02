"""Review System -- All Top Performers page.

ALL TOP PERFORMERS: a primary Level selector (HQ | BM | ABM | RBM) picks
which entity type is ranked. HQ and BM have real ranking logic; ABM and
RBM show a placeholder until their own ranking requirements are defined --
do not invent them here (see app.review_top_performers_service's own
module docstring).

HQ exposes a Scope selector (Corporate | Division-wise); Division-wise
additionally reveals a Division selector (Xandra | Onyx | Guardians),
hidden entirely under Corporate. BM has one ranking criterion (Number of
RXRs, i.e. Coverage Summary's own "Total Rxrs" for JUN -- see
top_bms_by_rxrs's own docstring for why never JUL) and no further
sub-controls -- there's nothing to select yet, so none are shown.

ONE global Load button (app.review_top_performers_service.
load_all_top_performers) loads every HQ combination AND the BM ranking in
a single batch pass -- see that function's own docstring for why: this
page used to fetch per-combination, which is how one combination's
numbers could end up rendered under another combination's controls (a
slow fetch finishing after the user had already switched controls). Each
combination's result is kept under its own (level, scope, division) key
in self._results; switching controls only ever LOOKS UP an already-loaded
result, never triggers a new fetch.

Read-only -- never uploads or generates Opus/Coverage Summary's own source
data, only reads it. Never auto-loads: this page is built eagerly at app
startup (ui.review_system_module), so a Load click is required, same
pattern Opus/Coverage/RGD's own Generate buttons already use.
"""

import time
from tkinter import ttk

import customtkinter as ctk

from app.review_coverage_service import COVERAGE_REPORT_MONTHS
from app.review_opus_service import DIVISIONS, OPUS_REPORT_MONTHS
from app.review_top_performers_service import load_all_top_performers
from ui.background_task import run_in_background
from ui.components import Card, EmptyState, PrimaryButton, SectionHeader, TabBar, styled_treeview
from ui.icons import get_icon
from ui.loading_overlay import LoadingOverlay
from ui.theme import Color, Font, Spacing

_MONTH = OPUS_REPORT_MONTHS[-1]
_BM_MONTH = COVERAGE_REPORT_MONTHS[-1]
_LEVELS = ("HQ", "BM", "ABM", "RBM")
_SCOPES = ("Corporate", "Division-wise")
_PLACEHOLDER_TEXT = "This page has six structure placeholders."

_HQ_COLUMNS = ("rank", "hq", "ypm", "primary", "bm_count", "divisions")
_HQ_HEADINGS = {
    "rank": "RANK", "hq": "HQ", "ypm": f"YPM ({_MONTH.upper()})",
    "primary": "PRIMARY SALES", "bm_count": "BM COUNT", "divisions": "DIVISION(S)",
}
_HQ_WIDTHS = {"rank": 60, "hq": 220, "ypm": 130, "primary": 140, "bm_count": 100, "divisions": 200}

_BM_COLUMNS = ("rank", "name", "division", "hq", "total_rxrs")
_BM_HEADINGS = {
    "rank": "RANK", "name": "BM NAME", "division": "DIVISION", "hq": "HQ",
    "total_rxrs": f"TOTAL RXRS ({_BM_MONTH.upper()})",
}
_BM_WIDTHS = {"rank": 60, "name": 220, "division": 130, "hq": 180, "total_rxrs": 160}

# Fixed height for the Level/Scope/Division control stack (see
# __init__'s _controls_area) -- sized for the MAXIMUM simultaneous row
# count (Level + Scope + Division, ~45px per TabBar row + gaps), so the
# table below it sits at the same vertical position no matter which
# combination is selected, instead of shifting depending on how many
# selector rows happen to be visible.
_CONTROLS_AREA_HEIGHT = 190

# A page-local ttk style, separate from the shared "Saffron.Treeview" every
# other table in the app uses -- taller rows and a bolder heading for a
# more deliberate leaderboard feel here, without touching any other page's
# table (see ui.components.styled_treeview's own `style` param docstring).
_RANKING_STYLE = "TopPerformers.Treeview"
_ranking_style_configured = False


def _configure_ranking_style() -> None:
    global _ranking_style_configured
    if _ranking_style_configured:
        return
    style = ttk.Style()
    style.configure(
        _RANKING_STYLE, background=Color.CARD, fieldbackground=Color.CARD,
        foreground=Color.TEXT_PRIMARY, rowheight=34, borderwidth=0,
        font=(Font.FAMILY, 12),
    )
    style.configure(
        f"{_RANKING_STYLE}.Heading", background=Color.SURFACE, foreground=Color.TEXT_SECONDARY,
        borderwidth=0, font=(Font.FAMILY, 11, "bold"), relief="flat",
    )
    style.map(
        _RANKING_STYLE,
        background=[("selected", Color.PRIMARY_SOFT)], foreground=[("selected", Color.TEXT_PRIMARY)],
    )
    _ranking_style_configured = True


def _configure_rank_tags(tree) -> None:
    """Shared row-emphasis tags for every ranking table on this page (HQ
    and BM alike) -- #1 gets the brand-soft highlight, #2-#3 get bold
    weight only, the rest alternate for row separation."""
    tree.tag_configure(
        "rank1", background=Color.PRIMARY_SOFT, foreground=Color.PRIMARY_DARK,
        font=(Font.FAMILY, 12, "bold"),
    )
    tree.tag_configure("rank_top3", font=(Font.FAMILY, 12, "bold"))
    tree.tag_configure("even", background=Color.CARD)
    tree.tag_configure("odd", background=Color.SURFACE)


def _rank_tag(rank: int) -> str:
    if rank == 1:
        return "rank1"
    if rank <= 3:
        return "rank_top3"
    return "even" if rank % 2 == 0 else "odd"


def _format_number(value: float) -> str:
    return f"{value:,.2f}"


def _format_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"~{seconds}s remaining"
    minutes, secs = divmod(seconds, 60)
    return f"~{minutes}m {secs}s remaining"


class ReviewTopPerformersPage(ctk.CTkFrame):
    """Top 10 HQs by YPM (Corporate or Division-wise) and Top 10 BMs by
    Number of RXRs, all loaded together by one Load click. ABM/RBM are
    still placeholders."""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._loading = False
        self._load_started_at: float | None = None
        self._results: dict = {}  # {(level, scope, division|None): result dict}

        # CTkScrollableFrame, not a plain CTkFrame -- every other Review
        # System page (review_hierarchy_page.py, review_uploads_page.py,
        # review_settings_page.py) already uses this for the same reason:
        # this page's content (title, Load row, up to 3 selector rows,
        # then a 10-row table) can exceed a shorter window's visible
        # height, and a non-scrolling page has no way to reach whatever
        # falls below the fold -- previously this could make the
        # Division-wise selector's Onyx/Guardians options effectively
        # unreachable on some window sizes, not because they weren't
        # built, but because nothing on the page could scroll to them.
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        SectionHeader(
            outer, "ALL TOP PERFORMERS",
            "Ranks the highest-performing HQs, BMs, ABMs, and RBMs by the selected criteria",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        header_row = ctk.CTkFrame(outer, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, Spacing.MD))
        self._load_button = PrimaryButton(
            header_row, text="Load",
            image=get_icon("refresh", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._start_load,
        )
        self._load_button.pack(side="right")
        self._status_label = ctk.CTkLabel(header_row, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED)
        self._status_label.pack(side="right", padx=(0, Spacing.MD))

        # A fixed-height reserved area for every selector row (Level,
        # and -- only under HQ -- Scope and Division), sized for the
        # maximum simultaneous stack via pack_propagate(False). This is
        # what keeps the table's position constant across every
        # combination: switching HQ<->BM never shifts it (both consume
        # the same reserved height), and switching Corporate<->
        # Division-wise never shifts it either (see _CONTROLS_AREA_HEIGHT).
        self._controls_area = ctk.CTkFrame(outer, fg_color="transparent", height=_CONTROLS_AREA_HEIGHT)
        self._controls_area.pack(fill="x", pady=(0, Spacing.LG))
        self._controls_area.pack_propagate(False)

        self._level_tabs = TabBar(self._controls_area, list(_LEVELS), on_change=self._on_level_changed)
        self._level_tabs.pack(fill="x", pady=(0, Spacing.MD))

        self._hq_controls = ctk.CTkFrame(self._controls_area, fg_color="transparent")
        self._scope_tabs = TabBar(self._hq_controls, list(_SCOPES), on_change=self._on_scope_changed)
        self._scope_tabs.pack(fill="x")
        self._division_row = ctk.CTkFrame(self._hq_controls, fg_color="transparent")
        self._division_tabs = TabBar(self._division_row, list(DIVISIONS), on_change=lambda _d: self._render_current())
        self._division_tabs.pack(fill="x")
        # _division_row is packed/unpacked by _on_scope_changed (hidden
        # under Corporate, the default scope). _hq_controls itself is
        # packed/unpacked by _on_level_changed (hidden for BM/ABM/RBM) --
        # both start visible here since HQ + Corporate are the defaults.
        self._hq_controls.pack(fill="x")

        self._result_container = ctk.CTkFrame(outer, fg_color="transparent")
        self._result_container.pack(fill="x")

        self._overlay = LoadingOverlay(self)
        _configure_ranking_style()

        # NEVER auto-load here -- this page (ui.review_system_module's
        # BASE_PAGES) is built eagerly at app startup, and a load reads
        # every division's full Opus Summary source data, documented at
        # 35s+92s per Primary Sales read alone in app.review_opus_service.
        # Only an explicit Load click triggers it.
        self._render_current()

    def on_show(self) -> None:
        pass  # whatever is already rendered (nothing yet, or the last load) stays as-is

    # --- Level / Scope / Division control wiring ----------------------------

    def _on_level_changed(self, level: str) -> None:
        if level == "HQ":
            # No pady here -- self._level_tabs's own bottom pady already
            # provides the gap; adding a second one on re-pack (vs. the
            # construction-time pack() below, which has none) would widen
            # the gap only on a switch back to HQ, an inconsistency that
            # would itself look like the table "jumping."
            self._hq_controls.pack(fill="x", after=self._level_tabs)
        else:
            self._hq_controls.pack_forget()
        self._render_current()

    def _on_scope_changed(self, scope: str) -> None:
        if scope == "Division-wise":
            self._division_row.pack(fill="x", pady=(Spacing.SM, 0), after=self._scope_tabs)
        else:
            self._division_row.pack_forget()
        self._render_current()

    def _current_key(self) -> tuple:
        scope = self._scope_tabs.active_tab
        division = self._division_tabs.active_tab if scope == "Division-wise" else None
        return ("HQ", scope, division)

    # --- Loading (one batch pass for every HQ combination) ------------------

    def _start_load(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._load_started_at = time.monotonic()
        self._load_button.configure(state="disabled")
        self._overlay.show()

        def on_progress(percent, message) -> None:
            elapsed = time.monotonic() - self._load_started_at
            if percent >= 2:
                eta = elapsed / percent * (100 - percent)
                message = f"{message} ({_format_eta(eta)})"
            self._overlay.update_progress(percent, message)

        def work_fn(report_progress):
            return load_all_top_performers(month=_MONTH, report_progress=report_progress)

        def on_done(result, error) -> None:
            self._loading = False
            self._overlay.hide()
            if not self._load_button.winfo_exists():
                return
            self._load_button.configure(state="normal")
            if error is not None:
                self._status_label.configure(text="")
                self._render_error([f"{error!r}"])
                return
            self._results = result["results"]
            self._status_label.configure(
                text=f"Loaded HQ {result['month'].title()} / BM {result['bm_month'].title()}"
                + (f" -- {len(result['errors'])} issue(s)" if result["errors"] else "")
            )
            self._render_current()

        run_in_background(self, work_fn, on_progress=on_progress, on_done=on_done)

    # --- Rendering ------------------------------------------------------------

    def _clear_result(self) -> None:
        for widget in self._result_container.winfo_children():
            widget.destroy()

    def _render_current(self) -> None:
        if self._loading:
            return  # the overlay covers the page; nothing to render underneath yet
        level = self._level_tabs.active_tab
        if level == "HQ":
            key, renderer = self._current_key(), self._render_hq_rankings
        elif level == "BM":
            key, renderer = ("BM", "Number of RXRs", None), self._render_bm_rankings
        else:
            self._render_placeholder()
            return

        result = self._results.get(key)
        if result is None:
            self._render_not_loaded()
        elif not result["success"]:
            self._render_error(result["errors"])
        else:
            renderer(result["rankings"])

    def _render_placeholder(self) -> None:
        self._clear_result()
        card = Card(self._result_container)
        card.pack(fill="x")
        EmptyState(card, _PLACEHOLDER_TEXT).pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

    def _render_not_loaded(self) -> None:
        self._clear_result()
        card = Card(self._result_container)
        card.pack(fill="x")
        EmptyState(card, "Not loaded yet -- click Load.").pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

    def _render_error(self, errors: list) -> None:
        self._clear_result()
        card = Card(self._result_container)
        card.pack(fill="x")
        message = "\n".join(f"  • {e}" for e in errors)
        message += "\n\nGo to Uploads / File Preview to check source-data readiness."
        EmptyState(card, message).pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

    def _new_ranking_card(self, title: str, columns, headings, widths, rankings: list):
        """Shared card + title label + themed table shell for both the HQ
        and BM ranking tables -- returns the tree so the caller only fills
        in its own row values."""
        card = Card(self._result_container)
        card.pack(fill="x")
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)

        ctk.CTkLabel(
            body, text=title, font=Font.H3, text_color=Color.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, Spacing.SM))

        # height=len(rankings) (never more than TOP_N=10, enforced by
        # app.review_top_performers_service) shows every row with no
        # vertical scrollbar -- a fully-visible <=10-row table reads as
        # compact, not as a clipped viewport into a longer list.
        tree = styled_treeview(
            body, columns, headings, widths=widths, height=len(rankings),
            scrollbars=False, style=_RANKING_STYLE,
        )
        tree.pack(fill="x")
        _configure_rank_tags(tree)
        return tree

    def _render_hq_rankings(self, rankings: list) -> None:
        self._clear_result()
        if not rankings:
            card = Card(self._result_container)
            card.pack(fill="x")
            EmptyState(card, "No HQ has a valid BM count yet.").pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)
            return

        scope = self._scope_tabs.active_tab
        label = "Corporate" if scope == "Corporate" else self._division_tabs.active_tab

        tree = self._new_ranking_card(
            f"Top {len(rankings)} HQs -- {label}", _HQ_COLUMNS, _HQ_HEADINGS, _HQ_WIDTHS, rankings,
        )
        for r in rankings:
            ypm = r.get("ypm", r.get("corporate_ypm"))
            rank = r["rank"]
            tree.insert("", "end", tags=(_rank_tag(rank),), values=(
                rank, r["hq"], _format_number(ypm),
                _format_number(r["primary"]), r["bm_count"], ", ".join(r["divisions"]),
            ))

    def _render_bm_rankings(self, rankings: list) -> None:
        self._clear_result()
        if not rankings:
            card = Card(self._result_container)
            card.pack(fill="x")
            EmptyState(card, "No BM has a Total Rxrs value yet.").pack(fill="x", padx=Spacing.LG, pady=Spacing.LG)
            return

        tree = self._new_ranking_card(
            f"Top {len(rankings)} BMs -- Number of RXRs ({_BM_MONTH.title()})",
            _BM_COLUMNS, _BM_HEADINGS, _BM_WIDTHS, rankings,
        )
        for r in rankings:
            rank = r["rank"]
            tree.insert("", "end", tags=(_rank_tag(rank),), values=(
                rank, r["name"], r["division"], r["hq"], r["total_rxrs"],
            ))
