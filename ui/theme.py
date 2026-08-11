"""Central design tokens for Saffron Automation: colors, fonts, and spacing.

Colors are derived directly from the Saffron Formulations logo
(assets/saffron_logo.png) — the primary orange is sampled from the logo's
gradient. Every page should pull its colors/fonts/spacing from here rather
than hardcoding values, so the whole app stays visually consistent.
"""

from app.config import ASSETS_DIR

LOGO_PNG = ASSETS_DIR / "saffron_logo.png"
LOGO_JPG = ASSETS_DIR / "saffron_logo.jpg"
APP_ICON = ASSETS_DIR / "icon.ico"


class Color:
    """Brand + semantic color palette (enterprise light theme)."""

    # Brand orange, sampled from the logo gradient.
    PRIMARY = "#F2811A"
    PRIMARY_HOVER = "#D96F0F"
    PRIMARY_DARK = "#C25D0C"
    PRIMARY_SOFT = "#FDECDD"  # tint for badges/highlights on white

    # Neutrals.
    WHITE = "#FFFFFF"
    SURFACE = "#F6F7F9"       # app background
    CARD = "#FFFFFF"          # card background sits above SURFACE
    BORDER = "#E4E6EA"
    DIVIDER = "#ECEDF0"

    TEXT_PRIMARY = "#2B2B2B"   # dark charcoal
    TEXT_SECONDARY = "#6B7280"
    TEXT_MUTED = "#9AA0AA"
    TEXT_ON_PRIMARY = "#FFFFFF"

    # Semantic states.
    SUCCESS = "#1E9E5A"
    SUCCESS_SOFT = "#E4F6EC"
    WARNING = "#E0A200"
    WARNING_SOFT = "#FDF3DA"
    ERROR = "#D64545"
    ERROR_SOFT = "#FBE8E7"
    INFO = "#3B82F6"
    INFO_SOFT = "#E8F1FE"

    # Muted red row highlight for the Replenishment page's CWH-shortage
    # warning (Milestone 46) -- an Excel "Bad"-cell-style pairing (light
    # red fill, dark red text), not a saturated/neon red. Revised from an
    # earlier bright #FF0000 per user feedback that it looked cartoonish.
    CRITICAL_ROW_BG = "#FFC7CE"
    CRITICAL_ROW_TEXT = "#9C0006"

    SIDEBAR_BG = "#FFFFFF"
    SIDEBAR_ACTIVE = "#FDECDD"
    SIDEBAR_HOVER = "#F6F7F9"
    SIDEBAR_TEXT = "#4B5160"
    SIDEBAR_TEXT_ACTIVE = "#C25D0C"


class Font:
    """Font family + size scale. Segoe UI is the Windows system font used
    by most Microsoft/enterprise tools; falls back gracefully elsewhere."""

    FAMILY = "Segoe UI"

    H1 = (FAMILY, 24, "bold")
    H2 = (FAMILY, 18, "bold")
    H3 = (FAMILY, 15, "bold")
    BODY = (FAMILY, 13, "normal")
    BODY_BOLD = (FAMILY, 13, "bold")
    SMALL = (FAMILY, 11, "normal")
    SMALL_BOLD = (FAMILY, 11, "bold")
    KPI_VALUE = (FAMILY, 28, "bold")
    KPI_LABEL = (FAMILY, 12, "normal")


class Spacing:
    """Consistent spacing scale (pixels), used for padx/pady throughout."""

    XS = 4
    SM = 8
    MD = 16
    LG = 24
    XL = 32


class Radius:
    """Corner radius scale used on cards/buttons/badges."""

    SM = 6
    MD = 10
    LG = 14
