"""Small, self-contained flat icon set drawn with PIL — no external icon
library or image assets needed. Each icon is a simple monochrome glyph on a
transparent background, drawn at a supersampled resolution and downscaled
for smooth anti-aliased edges, so it can be recolored for any nav/button state.
"""

import math
from functools import lru_cache

import customtkinter as ctk
from PIL import Image, ImageDraw

SUPERSAMPLE = 4


def _canvas(size: int):
    dim = size * SUPERSAMPLE
    img = Image.new("RGBA", (dim, dim), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img), dim


def _finish(img: Image.Image, size: int) -> Image.Image:
    return img.resize((size, size), Image.LANCZOS)


def _stroke_width(dim: int) -> int:
    return max(2, dim // 14)


def icon_operations(size: int, color: str) -> Image.Image:
    """Clipboard with a checkmark — running the import pipeline."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    pad = dim * 0.18
    draw.rounded_rectangle(
        [pad, pad * 1.3, dim - pad, dim - pad * 0.6], radius=dim * 0.08, outline=color, width=w
    )
    clip_w = dim * 0.22
    draw.rounded_rectangle(
        [dim / 2 - clip_w, pad * 0.55, dim / 2 + clip_w, pad * 1.5], radius=dim * 0.05, outline=color, width=w
    )
    draw.line(
        [(dim * 0.32, dim * 0.58), (dim * 0.44, dim * 0.7), (dim * 0.7, dim * 0.42)],
        fill=color,
        width=w,
        joint="curve",
    )
    return _finish(img, size)


def icon_findings(size: int, color: str) -> Image.Image:
    """A flag — flagged findings."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    pole_x = dim * 0.3
    draw.line([(pole_x, dim * 0.14), (pole_x, dim * 0.88)], fill=color, width=w)
    draw.polygon([(pole_x, dim * 0.16), (dim * 0.82, dim * 0.3), (pole_x, dim * 0.52)], fill=color)
    return _finish(img, size)


def icon_hierarchy(size: int, color: str) -> Image.Image:
    """Org chart: one node on top, two below, connected."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    r = dim * 0.11
    top = (dim / 2, dim * 0.2)
    left = (dim * 0.26, dim * 0.78)
    right = (dim * 0.74, dim * 0.78)
    mid_y = dim * 0.5

    draw.line([(dim / 2, top[1] + r), (dim / 2, mid_y)], fill=color, width=w)
    draw.line([(left[0], mid_y), (right[0], mid_y)], fill=color, width=w)
    draw.line([(left[0], mid_y), (left[0], left[1] - r)], fill=color, width=w)
    draw.line([(right[0], mid_y), (right[0], right[1] - r)], fill=color, width=w)

    for cx, cy in (top, left, right):
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=w)
    return _finish(img, size)


def icon_parameters(size: int, color: str) -> Image.Image:
    """Three horizontal sliders with knobs at different positions."""
    img, draw, dim = _canvas(size)
    r = dim * 0.09
    xs, xe = dim * 0.18, dim * 0.82
    for i, frac in enumerate((0.35, 0.65, 0.5)):
        y = dim * (0.24 + i * 0.26)
        draw.line([(xs, y), (xe, y)], fill=color, width=max(2, dim // 28))
        kx = xs + (xe - xs) * frac
        draw.ellipse([kx - r, y - r, kx + r, y + r], fill=color)
    return _finish(img, size)


def icon_dashboard(size: int, color: str) -> Image.Image:
    """Simple bar chart."""
    img, draw, dim = _canvas(size)
    base = dim * 0.85
    bar_w = dim * 0.16
    gap = dim * 0.06
    heights = (0.35, 0.6, 0.45, 0.75)
    total_w = len(heights) * bar_w + (len(heights) - 1) * gap
    x = (dim - total_w) / 2
    for h in heights:
        top = base - dim * h
        draw.rounded_rectangle([x, top, x + bar_w, base], radius=dim * 0.02, fill=color)
        x += bar_w + gap
    return _finish(img, size)


def icon_about(size: int, color: str) -> Image.Image:
    """A classic "info" glyph: circle outline, a dot, and a stem below it."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    cx, cy, r = dim / 2, dim / 2, dim * 0.32
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=w)

    dot_r = dim * 0.05
    dot_cy = cy - r * 0.42
    draw.ellipse([cx - dot_r, dot_cy - dot_r, cx + dot_r, dot_cy + dot_r], fill=color)

    stem_top = cy - r * 0.05
    stem_bottom = cy + r * 0.5
    draw.line([(cx, stem_top), (cx, stem_bottom)], fill=color, width=w)
    return _finish(img, size)


def icon_developer(size: int, color: str) -> Image.Image:
    """Code brackets `< >` — the Developer Mode page."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    mid = dim / 2
    left, right = dim * 0.28, dim * 0.72
    top, bottom = dim * 0.30, dim * 0.70
    # left chevron  <
    draw.line([(left, mid), (dim * 0.42, top)], fill=color, width=w)
    draw.line([(left, mid), (dim * 0.42, bottom)], fill=color, width=w)
    # right chevron  >
    draw.line([(right, mid), (dim * 0.58, top)], fill=color, width=w)
    draw.line([(right, mid), (dim * 0.58, bottom)], fill=color, width=w)
    return _finish(img, size)


def icon_settings(size: int, color: str) -> Image.Image:
    """A simplified gear: circle outline + radiating teeth + center dot."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    cx = cy = dim / 2
    r_outer = dim * 0.28
    r_inner = dim * 0.11

    tooth_len = dim * 0.12
    tooth_w = max(3, dim // 10)
    for i in range(8):
        angle = math.radians(i * 45)
        x1, y1 = cx + math.cos(angle) * r_outer, cy + math.sin(angle) * r_outer
        x2, y2 = cx + math.cos(angle) * (r_outer + tooth_len), cy + math.sin(angle) * (r_outer + tooth_len)
        draw.line([(x1, y1), (x2, y2)], fill=color, width=tooth_w)

    draw.ellipse([cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer], outline=color, width=w)
    draw.ellipse([cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner], fill=color)
    return _finish(img, size)


def icon_upload(size: int, color: str) -> Image.Image:
    """An upward arrow into a tray — importing a file."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    draw.line([(dim * 0.2, dim * 0.82), (dim * 0.8, dim * 0.82)], fill=color, width=w)
    draw.line([(dim / 2, dim * 0.78), (dim / 2, dim * 0.22)], fill=color, width=w)
    draw.line([(dim * 0.3, dim * 0.44), (dim / 2, dim * 0.2), (dim * 0.7, dim * 0.44)], fill=color, width=w, joint="curve")
    return _finish(img, size)


def icon_download(size: int, color: str) -> Image.Image:
    """A downward arrow out of a tray -- exporting a file. Visual inverse
    of icon_upload above."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    draw.line([(dim * 0.2, dim * 0.82), (dim * 0.8, dim * 0.82)], fill=color, width=w)
    draw.line([(dim / 2, dim * 0.22), (dim / 2, dim * 0.78)], fill=color, width=w)
    draw.line([(dim * 0.3, dim * 0.56), (dim / 2, dim * 0.8), (dim * 0.7, dim * 0.56)], fill=color, width=w, joint="curve")
    return _finish(img, size)


def icon_refresh(size: int, color: str) -> Image.Image:
    """A circular refresh arrow."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    pad = dim * 0.2
    draw.arc([pad, pad, dim - pad, dim - pad], start=30, end=300, fill=color, width=w)
    tip_angle = math.radians(300)
    r = (dim - 2 * pad) / 2
    cx = cy = dim / 2
    tip_x, tip_y = cx + math.cos(tip_angle) * r, cy + math.sin(tip_angle) * r
    arrow = dim * 0.12
    draw.polygon(
        [
            (tip_x, tip_y - arrow),
            (tip_x + arrow, tip_y + arrow * 0.2),
            (tip_x - arrow * 0.3, tip_y + arrow),
        ],
        fill=color,
    )
    return _finish(img, size)


def icon_search(size: int, color: str) -> Image.Image:
    """A magnifying glass."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    r = dim * 0.28
    cx, cy = dim * 0.42, dim * 0.42
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=w)
    angle = math.radians(45)
    x1, y1 = cx + math.cos(angle) * r, cy + math.sin(angle) * r
    x2, y2 = dim * 0.85, dim * 0.85
    draw.line([(x1, y1), (x2, y2)], fill=color, width=w)
    return _finish(img, size)


def icon_notifications(size: int, color: str) -> Image.Image:
    """A bell — manager notifications."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    cx, top, bottom = dim / 2, dim * 0.2, dim * 0.72
    draw.arc([dim * 0.22, top, dim * 0.78, bottom], start=180, end=360, fill=color, width=w)
    draw.line([(dim * 0.22, (top + bottom) / 2), (dim * 0.22, bottom)], fill=color, width=w)
    draw.line([(dim * 0.78, (top + bottom) / 2), (dim * 0.78, bottom)], fill=color, width=w)
    draw.line([(dim * 0.18, bottom), (dim * 0.82, bottom)], fill=color, width=w)
    r = dim * 0.07
    draw.ellipse([cx - r, bottom + w * 0.3, cx + r, bottom + w * 0.3 + 2 * r], fill=color)
    return _finish(img, size)


def icon_inventory(size: int, color: str) -> Image.Image:
    """A packing crate: outer box outline, a horizontal seam, and a
    diagonal flap line — Inventory Monitoring."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    left, right = dim * 0.16, dim * 0.84
    top, bottom = dim * 0.28, dim * 0.82
    draw.rounded_rectangle([left, top, right, bottom], radius=dim * 0.04, outline=color, width=w)
    mid_y = (top + bottom) / 2
    draw.line([(left, mid_y), (right, mid_y)], fill=color, width=w)
    draw.line([(dim / 2, top), (dim / 2, mid_y)], fill=color, width=w)
    draw.line([(dim * 0.36, dim * 0.16), (dim / 2, top), (dim * 0.64, dim * 0.16)], fill=color, width=w, joint="curve")
    return _finish(img, size)


def icon_threshold(size: int, color: str) -> Image.Image:
    """A horizontal threshold line with an above/below marker pair —
    Inventory Thresholds."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    mid_y = dim / 2
    draw.line([(dim * 0.14, mid_y), (dim * 0.86, mid_y)], fill=color, width=w)
    cx = dim / 2
    # upward chevron above the line
    draw.line([(cx - dim * 0.1, dim * 0.28), (cx, dim * 0.16), (cx + dim * 0.1, dim * 0.28)], fill=color, width=w, joint="curve")
    # downward chevron below the line
    draw.line([(cx - dim * 0.1, dim * 0.72), (cx, dim * 0.84), (cx + dim * 0.1, dim * 0.72)], fill=color, width=w, joint="curve")
    return _finish(img, size)


def icon_replenishment(size: int, color: str) -> Image.Image:
    """Two opposing arcs with arrowheads forming a restock cycle —
    Inventory Replenishment."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    pad = dim * 0.2
    r = (dim - 2 * pad) / 2
    cx = cy = dim / 2

    draw.arc([pad, pad, dim - pad, dim - pad], start=200, end=340, fill=color, width=w)
    tip_angle = math.radians(340)
    tip_x, tip_y = cx + math.cos(tip_angle) * r, cy + math.sin(tip_angle) * r
    arrow = dim * 0.1
    draw.polygon(
        [(tip_x, tip_y), (tip_x - arrow * 1.1, tip_y - arrow * 0.2), (tip_x - arrow * 0.3, tip_y + arrow)],
        fill=color,
    )

    draw.arc([pad, pad, dim - pad, dim - pad], start=20, end=160, fill=color, width=w)
    tip_angle2 = math.radians(160)
    tip_x2, tip_y2 = cx + math.cos(tip_angle2) * r, cy + math.sin(tip_angle2) * r
    draw.polygon(
        [(tip_x2, tip_y2), (tip_x2 + arrow * 1.1, tip_y2 + arrow * 0.2), (tip_x2 + arrow * 0.3, tip_y2 - arrow)],
        fill=color,
    )
    return _finish(img, size)


def icon_payment_card(size: int, color: str) -> Image.Image:
    """A payment card: outer card outline, a filled magnetic-stripe band,
    and a short digits line -- Payment Analytics."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    left, right = dim * 0.14, dim * 0.86
    top, bottom = dim * 0.26, dim * 0.74
    draw.rounded_rectangle([left, top, right, bottom], radius=dim * 0.06, outline=color, width=w)
    stripe_top = top + (bottom - top) * 0.24
    stripe_bottom = stripe_top + (bottom - top) * 0.16
    draw.rectangle([left, stripe_top, right, stripe_bottom], fill=color)
    line_y = bottom - (bottom - top) * 0.22
    draw.line([(left + dim * 0.06, line_y), (left + dim * 0.36, line_y)], fill=color, width=w)
    return _finish(img, size)


def icon_table(size: int, color: str) -> Image.Image:
    """A data table: outer grid with one vertical and two horizontal
    dividers -- Customer Analytics."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    left, right = dim * 0.16, dim * 0.84
    top, bottom = dim * 0.2, dim * 0.8
    draw.rounded_rectangle([left, top, right, bottom], radius=dim * 0.04, outline=color, width=w)
    row_h = (bottom - top) / 3
    for i in (1, 2):
        y = top + row_h * i
        draw.line([(left, y), (right, y)], fill=color, width=w)
    mid_x = (left + right) / 2
    draw.line([(mid_x, top), (mid_x, bottom)], fill=color, width=w)
    return _finish(img, size)


def icon_warehouse(size: int, color: str) -> Image.Image:
    """A warehouse building: peaked roof, a body outline, and a center
    bay door -- Central Warehouse (CWH), distinct from the packing-crate
    Inventory Monitoring icon."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    left, right = dim * 0.14, dim * 0.86
    top, bottom = dim * 0.42, dim * 0.84
    draw.rectangle([left, top, right, bottom], outline=color, width=w)
    draw.line([(dim * 0.08, top), (dim / 2, dim * 0.16), (dim * 0.92, top)], fill=color, width=w, joint="curve")
    door_w = dim * 0.16
    draw.rectangle([dim / 2 - door_w, dim * 0.58, dim / 2 + door_w, bottom], outline=color, width=max(2, w - 1))
    return _finish(img, size)


def icon_calendar_check(size: int, color: str) -> Image.Image:
    """A calendar page with two hanger tabs and a checkmark in the body --
    Work Distribution (monthly doctor-coverage tracking)."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    left, right = dim * 0.14, dim * 0.86
    top, bottom = dim * 0.26, dim * 0.86
    draw.rounded_rectangle([left, top, right, bottom], radius=dim * 0.06, outline=color, width=w)
    draw.line([(left, top + dim * 0.14), (right, top + dim * 0.14)], fill=color, width=w)
    tab_w = max(2, dim // 20)
    draw.line([(dim * 0.32, dim * 0.16), (dim * 0.32, top + dim * 0.06)], fill=color, width=tab_w)
    draw.line([(dim * 0.68, dim * 0.16), (dim * 0.68, top + dim * 0.06)], fill=color, width=tab_w)
    draw.line(
        [(dim * 0.32, dim * 0.62), (dim * 0.45, dim * 0.74), (dim * 0.72, dim * 0.46)],
        fill=color,
        width=w,
        joint="curve",
    )
    return _finish(img, size)


def icon_person(size: int, color: str) -> Image.Image:
    """A simple person glyph: a head circle over a shoulder arc --
    Employee Details."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    cx = dim / 2
    r = dim * 0.16
    cy = dim * 0.32
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=w)
    draw.arc(
        [dim * 0.18, dim * 0.5, dim * 0.82, dim * 1.06], start=180, end=360, fill=color, width=w
    )
    return _finish(img, size)


def icon_collections(size: int, color: str) -> Image.Image:
    """A document with ruled lines and a small clock badge -- Collections
    Action Center (following up on overdue invoices)."""
    img, draw, dim = _canvas(size)
    w = _stroke_width(dim)
    left, right = dim * 0.14, dim * 0.62
    top, bottom = dim * 0.14, dim * 0.86
    draw.rounded_rectangle([left, top, right, bottom], radius=dim * 0.05, outline=color, width=w)
    for frac in (0.34, 0.5, 0.66):
        y = top + (bottom - top) * frac
        draw.line([(left + dim * 0.08, y), (right - dim * 0.08, y)], fill=color, width=max(2, w - 1))
    cx, cy, r = dim * 0.74, dim * 0.74, dim * 0.24
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=w)
    draw.line([(cx, cy), (cx, cy - r * 0.6)], fill=color, width=max(2, w - 1))
    draw.line([(cx, cy), (cx + r * 0.5, cy)], fill=color, width=max(2, w - 1))
    return _finish(img, size)


_ICON_FUNCS = {
    # Master Overview is a people-triage view -- reuses the person glyph
    # rather than a new icon (same reasoning as Excess Inventory below).
    "Master": icon_person,
    "Operations": icon_operations,
    "Findings": icon_findings,
    "Organization Data": icon_hierarchy,
    "Parameters": icon_parameters,
    "Analytics Dashboard": icon_dashboard,
    "Settings": icon_settings,
    "About": icon_about,
    "Developer": icon_developer,
    "Email Center": icon_notifications,
    "Automated Emails": icon_notifications,
    "Inventory Monitoring": icon_inventory,
    "Dashboard": icon_dashboard,
    "Uploads": icon_upload,
    "Thresholds": icon_threshold,
    "Replenishment": icon_replenishment,
    "Central Warehouse (CWH)": icon_warehouse,
    # Reuses the same threshold glyph as "Thresholds" above -- Excess
    # Inventory is fundamentally a threshold comparison too (stock ABOVE
    # threshold, instead of Replenishment's BELOW), not a distinct visual
    # concept that needs its own icon drawn.
    "Excess Inventory": icon_threshold,
    "upload": icon_upload,
    "download": icon_download,
    "refresh": icon_refresh,
    "search": icon_search,
    "Payment Analytics": icon_payment_card,
    "Historical Upload": icon_upload,
    "Customer Analytics": icon_table,
    "Collections Action Center": icon_collections,
    "Work Distribution": icon_calendar_check,
    "Upload": icon_upload,
    "Employee Details": icon_person,
}


@lru_cache(maxsize=None)
def _cached_pil(name: str, size: int, color: str) -> Image.Image:
    return _ICON_FUNCS[name](size, color)


def get_icon(name: str, size: int = 20, color: str = "#2B2B2B") -> ctk.CTkImage:
    """Return a CTkImage for a named nav/action icon, generated and cached."""
    pil_image = _cached_pil(name, size, color)
    return ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(size, size))
