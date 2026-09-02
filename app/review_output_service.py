"""The Review System's single final-output seam.

The Review System's analysis/processing pipeline -- business logic,
calculations, hierarchy mapping, the actual "review" -- has not been built
yet (see app/review_upload_service.py and app/review_validation.py for
what exists today: upload + validation only, by explicit design).

This module exists now, ahead of that pipeline, so File Preview
(ui/review_file_preview_page.py) and the eventual Export feature are both
wired to the SAME place from day one. When the analysis pipeline is built,
it persists its result through here -- nothing downstream (File Preview,
Export) needs to change, and there is never a second, independent copy of
"the output" for either of them to drift from.
"""

from datetime import datetime


def get_final_review_output() -> dict | None:
    """The generated final Review System output, or None if analysis has
    not been run yet -- always None today (see module docstring; no
    pipeline writes here yet).

    Shape once populated (mirrors a plain table, so File Preview's
    rendering barely changes when this starts returning real data):
        {
            "columns": [str, ...],
            "rows": [[value, ...], ...],
            "row_count": int,
            "column_count": int,
            "generated_at": datetime,
        }
    """
    return None


def get_generated_opus_summary(division: str) -> dict | None:
    """Metadata for `division`'s already-generated Opus Summary workbook,
    or None if it hasn't been generated (yet, or ever) this session --
    stateless by design: the generated .xlsx file's own existence/mtime on
    disk IS the state, so there's nothing to keep in sync with a database
    row that could ever drift from the real file.

    File Preview (ui/review_file_preview_page.py) reads this to know
    whether to show a "not generated yet" empty state or the generated
    summary; app.review_opus_service.generate_opus_summary() is the only
    writer.

    Returns:
        {
            "division": str,
            "file_path": str,
            "generated_at": datetime,     # the file's own mtime
            "hq_count": int,
            "row_count": int,
        }
    """
    from app.review_opus_mapping import OPUS_HQ_BLOCKS_BY_DIVISION
    from app.review_opus_service import ROW_LABELS, generated_opus_summary_path

    path = generated_opus_summary_path(division)
    if not path.is_file():
        return None

    hq_blocks = OPUS_HQ_BLOCKS_BY_DIVISION.get(division, ())
    return {
        "division": division,
        "file_path": str(path),
        "generated_at": datetime.fromtimestamp(path.stat().st_mtime),
        "hq_count": len(hq_blocks),
        "row_count": len(hq_blocks) * (len(ROW_LABELS) + 1),  # +1 for each block's spacer row
    }


def get_opus_summary_preview(division: str) -> dict | None:
    """The full preview grid for `division`'s generated Opus Summary (every
    HQ block, every row, real formatted values), or None if nothing has
    been generated yet. See
    app.review_opus_service.generated_opus_preview_path's docstring for why
    this reads a JSON sidecar rather than the .xlsx itself.

    Returns: {"columns": [str, ...], "rows": [dict, ...]} -- see
    app.review_opus_service._build_preview_rows for each row dict's shape.
    """
    import json

    from app.review_opus_service import generated_opus_preview_path

    path = generated_opus_preview_path(division)
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_generated_coverage_summary(division: str) -> dict | None:
    """Metadata for `division`'s already-generated Coverage Summary
    workbook, or None if it hasn't been generated yet -- same stateless
    contract as get_generated_opus_summary above.

    Returns:
        {
            "division": str,
            "file_path": str,
            "generated_at": datetime,     # the file's own mtime
            "bm_count": int,
        }
    """
    from app.review_coverage_service import ROW_LABELS, generated_coverage_summary_path

    path = generated_coverage_summary_path(division)
    if not path.is_file():
        return None

    preview = get_coverage_summary_preview(division)
    bm_count = (len(preview["rows"]) // len(ROW_LABELS)) if preview else 0
    return {
        "division": division,
        "file_path": str(path),
        "generated_at": datetime.fromtimestamp(path.stat().st_mtime),
        "bm_count": bm_count,
    }


def get_coverage_summary_preview(division: str) -> dict | None:
    """The full preview grid for `division`'s generated Coverage Summary,
    or None if nothing has been generated yet -- see
    app.review_coverage_service's module docstring for the JSON-sidecar
    reasoning (same as Opus's).

    Returns: {"columns": [str, ...], "rows": [dict, ...]} -- see
    app.review_coverage_service._build_preview_rows for each row dict's shape.
    """
    import json

    from app.review_coverage_service import generated_coverage_preview_path

    path = generated_coverage_preview_path(division)
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_generated_rgd_summary(division: str) -> dict | None:
    """Metadata for `division`'s already-generated RGD Visit and Support
    workbook, or None if it hasn't been generated yet -- same stateless
    contract as get_generated_opus_summary/get_generated_coverage_summary
    above.

    Returns:
        {
            "division": str,
            "file_path": str,
            "generated_at": datetime,     # the file's own mtime
            "row_count": int,
        }
    """
    from app.review_rgd_service import generated_rgd_path

    path = generated_rgd_path(division)
    if not path.is_file():
        return None

    preview = get_rgd_summary_preview(division)
    row_count = len(preview["rows"]) if preview else 0
    return {
        "division": division,
        "file_path": str(path),
        "generated_at": datetime.fromtimestamp(path.stat().st_mtime),
        "row_count": row_count,
    }


def get_rgd_summary_preview(division: str) -> dict | None:
    """The full preview grid for `division`'s generated RGD Visit and
    Support, or None if nothing has been generated yet -- see
    app.review_rgd_service's module docstring for the JSON-sidecar
    reasoning (same as Opus's/Coverage's).

    Returns: {"columns": [str, ...], "rows": [dict, ...]} -- see
    app.review_rgd_service._build_preview_rows for each row dict's shape.
    """
    import json

    from app.review_rgd_service import generated_rgd_preview_path

    path = generated_rgd_preview_path(division)
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)
