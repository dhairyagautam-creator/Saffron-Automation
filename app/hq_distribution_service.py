"""HQ Distribution -- the master file mapping which (Division, Region, HQ)
combinations actually exist, uploaded separately from the Review System's
12 required files (see ui/review_hierarchy_page.py, the "Hierarchy and HQ
Distribution" page).

Why this exists (2026-08-19): Annual Targets' per-division sheets were
previously the ONLY signal for "does this HQ belong to this division" --
if an HQ from the reference Region/HQ structure (app/review_opus_mapping.py)
had no matching row in a division's Annual Targets sheet, it was marked
"unresolved", indistinguishable from a genuine data-availability problem.
That's wrong: an HQ that operates under Xandra but never under Onyx isn't
missing Onyx data, it simply isn't an Onyx location. This file is the
authoritative answer to "which HQs belong to which division", independent
of whether the numeric source files happen to have a matching row --
app.review_opus_service.generate_opus_summary consults it (when uploaded)
to EXCLUDE not-applicable blocks entirely, before the existing
resolved/unresolved machinery ever runs on what's left.

Stateless by design, like app/review_output_service.py's generated-output
tracking: the uploaded file's own existence on disk IS the state,
re-validated on read (it's a few hundred rows, not 80k+) rather than
persisted to a database row that could drift from the real file.

Uploading this file NEVER triggers Opus generation -- that stays a
separate, explicit action on File Preview, exactly like every other
Review System upload.
"""

import json
import shutil
from pathlib import Path

import pandas as pd
from loguru import logger

from app.config import REVIEW_UPLOADS_DIR
from app.excel_validation import SUPPORTED_EXTENSIONS, _normalize_header

REQUIRED_COLUMNS = ("Division", "Region Name", "HQ")


def _norm(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return " ".join(str(value).strip().upper().split())


def _storage_path() -> Path:
    return REVIEW_UPLOADS_DIR / "hq_distribution.xlsx"


def _meta_path() -> Path:
    """Tiny sidecar recording the user's ORIGINAL filename -- the stored
    file itself always lives at a fixed name (_storage_path), but every
    other Review System upload displays the name the user actually picked
    (see app.review_upload_service.upload_review_file's `original_filename`
    handling), so this does too rather than always showing the internal
    storage name."""
    return REVIEW_UPLOADS_DIR / "hq_distribution.meta.json"


def _empty_state() -> dict:
    return {
        "uploaded": False, "valid": False, "filename": None, "file_path": None,
        "row_count": None, "division_counts": {}, "errors": [],
    }


def _validate(file_path: str) -> dict:
    """Never raises -- every failure mode is reported in the returned dict,
    same contract as app.review_validation.validate_review_file."""
    extension = Path(file_path).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        return {"valid": False, "row_count": None, "division_counts": {},
                "errors": [f"Unsupported file type: '{extension}'."]}

    try:
        df = pd.read_excel(file_path, sheet_name=0)
    except Exception as exc:
        logger.warning(f"HQ Distribution: failed to read '{file_path}': {exc}")
        return {"valid": False, "row_count": None, "division_counts": {},
                "errors": [f"Could not read the file: {exc}"]}

    col_by_norm = {_normalize_header(c): c for c in df.columns}
    missing = [c for c in REQUIRED_COLUMNS if _normalize_header(c) not in col_by_norm]
    if missing:
        return {"valid": False, "row_count": len(df), "division_counts": {},
                "errors": [f"Missing required column(s): {', '.join(missing)}"]}

    division_col = col_by_norm[_normalize_header("Division")]
    hq_col = col_by_norm[_normalize_header("HQ")]
    usable = df.dropna(subset=[division_col, hq_col])

    errors = []
    if len(usable) == 0:
        errors.append("The file has no usable Division/HQ rows.")

    division_counts = usable[division_col].apply(_norm).value_counts().to_dict()
    return {"valid": len(errors) == 0, "row_count": len(df), "division_counts": division_counts, "errors": errors}


def get_hq_distribution_state() -> dict:
    """Always returns a dict, even when nothing has been uploaded (uploaded=False).

    Returns:
        {
            "uploaded": bool,
            "valid": bool,
            "filename": str | None,
            "file_path": str | None,
            "row_count": int | None,
            "division_counts": {division_upper: row_count},
            "errors": [str],
        }
    """
    path = _storage_path()
    if not path.is_file():
        return _empty_state()
    result = _validate(str(path))
    result["uploaded"] = True
    result["filename"] = _read_original_filename() or path.name
    result["file_path"] = str(path)
    return result


def _read_original_filename() -> str | None:
    meta = _meta_path()
    if not meta.is_file():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8")).get("original_filename")
    except Exception:
        return None


def upload_hq_distribution_file(source_path: str) -> dict:
    """Copies `source_path` into storage and validates it -- always stores
    the file (even if invalid), same contract as
    app.review_upload_service.upload_review_file: an invalid upload still
    occupies the slot, the user replaces or removes it explicitly. Does
    NOT touch any of the Review System's 12 required-file slots or trigger
    Opus generation."""
    dest = _storage_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, dest)
    _meta_path().write_text(json.dumps({"original_filename": Path(source_path).name}), encoding="utf-8")
    state = get_hq_distribution_state()
    logger.info(f"HQ Distribution: stored '{Path(source_path).name}' -> valid={state['valid']}")
    return state


def remove_hq_distribution_file() -> None:
    path = _storage_path()
    if path.is_file():
        path.unlink()
    meta = _meta_path()
    if meta.is_file():
        meta.unlink()
    logger.info("HQ Distribution: file removed")


def get_valid_hqs_for_division(division: str) -> set | None:
    """{hq_norm, ...} for `division`, or None if the HQ Distribution file
    hasn't been uploaded (or isn't valid) -- callers
    (app.review_opus_service._filter_applicable_blocks) MUST treat None as
    "no distribution data available, fall back to treating every reference
    block as applicable" (today's pre-existing behavior), never as
    "division has zero valid HQs" (which would silently empty every
    division's report the moment this file goes missing/invalid).

    HQ NAME ONLY -- deliberately NOT (region, hq) -- per real-file
    verification, 2026-08-19: this file's own Region Name spellings
    ("GUJARAT-AHM", "TAMIL NADU-CHEN", "MUMBAI-1") don't match the
    Opus reference structure's region display text ("Gujarat - AHM",
    "Tamil Nadu - CHN", "Mumbai - 1") at all -- matching on the (region, hq)
    tuple wrongly excluded ~55% of Xandra's own real HQs as "not
    applicable" the first time this was tried. HQ names, by contrast,
    matched cleanly (94% direct overlap for Xandra) -- verified against
    every other source file too (Annual Targets, Primary Sales, Secondary
    Sales all key on HQ name, and disagree with each other on region
    spelling exactly as this file does). The one place this could bite --
    the same HQ name legitimately existing under two different regions --
    doesn't arise in the reference structure except "Mumbai Pool" itself,
    which is already excluded everywhere else in this pipeline as
    inherently ambiguous (see app/review_opus_mapping.py)."""
    state = get_hq_distribution_state()
    if not state["uploaded"] or not state["valid"]:
        return None

    df = pd.read_excel(state["file_path"], sheet_name=0)
    col_by_norm = {_normalize_header(c): c for c in df.columns}
    division_col = col_by_norm[_normalize_header("Division")]
    hq_col = col_by_norm[_normalize_header("HQ")]

    sub = df.dropna(subset=[division_col, hq_col])
    sub = sub[sub[division_col].apply(_norm) == _norm(division)]
    return set(sub[hq_col].apply(_norm))
