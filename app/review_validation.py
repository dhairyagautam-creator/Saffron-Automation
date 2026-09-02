"""Centralized, strict schema validator for Review System uploads.

Deliberately separate from app/excel_validation.py's engine: that engine is
built for messy historical exports -- it scans up to MAX_HEADER_SCAN_ROWS
rows across every sheet for whichever row satisfies a REQUIRED (but
order-independent, extras-tolerant) column set. Review System's 12 files
are schema-locked: exact column set, exact order, no extras, no
duplicates -- a fundamentally stricter check, so header detection here is
intentionally simple (first sheet, first row) rather than fuzzy-scanned.

Header matching reuses app.excel_validation._normalize_header (whitespace/
case/NBSP handling) as its base, then layers Review-System-only leniency
on top -- see _normalize_header_lenient below -- entirely local to this
module. app/excel_validation.py itself is never modified, so every other
report type's validation is completely unaffected.
"""

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from loguru import logger

from app.excel_validation import SUPPORTED_EXTENSIONS, _normalize_header
from app.review_schemas import get_slot_def

# --- Lenient header matching (Review System only) ---------------------------
#
# Real uploaded files carry harmless variations _normalize_header alone
# doesn't cover: apostrophes ("Apr'26"), mixed separators ("Apr-2026" /
# "Apr.2026" / "Apr_2026" / "Apr 2026"), and month spelled out vs
# abbreviated vs numeric. This layer canonicalizes those WITHOUT collapsing
# distinct columns into each other -- the year is preserved verbatim (only
# 4-digit -> last-2-digits, e.g. "2026" -> "26", so "Apr-2026" and "Apr'26"
# match, but "Apr'25" and "Apr'26" still don't), and only text immediately
# forming a "<month> <year>" pattern is touched; everything else (a "BM
# Visit Count " prefix, a " Pri"/" Sec"/" Sup" suffix) stays literal.
#
# Deliberately conservative: month-year canonicalization narrows format
# noise, it does not invent equivalences between different periods -- a
# file for the wrong year, or missing whole columns, still fails exactly
# as before. See test_review_validation.py's
# test_lenient_normalization_never_collides_within_a_schema for the safety
# check this relies on (no two distinct required/optional columns in any
# single schema may ever normalize to the same value).
_MONTH_TO_NUM = {
    "jan": "01", "january": "01",
    "feb": "02", "february": "02",
    "mar": "03", "march": "03",
    "apr": "04", "april": "04",
    "may": "05",
    "jun": "06", "june": "06",
    "jul": "07", "july": "07",
    "aug": "08", "august": "08",
    "sep": "09", "sept": "09", "september": "09",
    "oct": "10", "october": "10",
    "nov": "11", "november": "11",
    "dec": "12", "december": "12",
}
_MONTH_YEAR_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTH_TO_NUM, key=len, reverse=True)) + r")\s*(\d{2,4})\b"
)


def _canonicalize_month_year(text: str) -> str:
    def replace(match: re.Match) -> str:
        month_num = _MONTH_TO_NUM[match.group(1)]
        year_2digit = match.group(2)[-2:]
        return f"{month_num} {year_2digit}"

    return _MONTH_YEAR_RE.sub(replace, text)


def _normalize_header_lenient(value) -> str:
    """_normalize_header's output, plus: apostrophes dropped, hyphens/
    underscores/periods treated as spaces (then re-collapsed), and any
    "<month> <year>" fragment canonicalized to "<2-digit month> <2-digit
    year>". Used for every Review System comparison (missing/unexpected/
    duplicate/order) in place of the bare normalizer."""
    text = _normalize_header(value)
    text = text.replace("'", "")
    text = re.sub(r"[-_.]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _canonicalize_month_year(text)
    return text


def _stringify_header_value(value) -> str:
    """A header cell's logical text -- including when Excel/pandas parsed a
    date-shaped header (e.g. "Feb-26") as an actual date/datetime object
    rather than the string the user sees. Renders those as "<2-digit
    month> <2-digit year>", the exact form _canonicalize_month_year already
    produces from text like "Feb-26" or "Feb 2026" -- so a header read as a
    Timestamp and the same header read as text always normalize identically
    (see _normalize_header_lenient). Every other value stringifies as
    before (plain str(), stripped)."""
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return f"{value.month:02d} {value.year % 100:02d}"
    return str(value).strip()


def _read_raw_header_row(file_path: str, extension: str, sheet=0) -> list:
    """The true header row, exactly as it appears in the file -- read with
    header=None so pandas does NOT apply its own duplicate-column mangling
    (e.g. a second literal "REGION" would otherwise silently become
    "REGION.1" before validation ever sees it, hiding a real duplicate-
    header problem as a fake "unexpected column" instead). Blank trailing
    cells are dropped; a genuine duplicate value is not blank, so it
    survives."""
    if extension == ".csv":
        raw = None
        for encoding in ("utf-8-sig", "latin-1"):
            try:
                raw = pd.read_csv(file_path, header=None, nrows=1, encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        if raw is None:
            raise UnicodeDecodeError("csv", b"", 0, 1, "unable to decode CSV with utf-8-sig or latin-1")
    else:
        engine = "xlrd" if extension == ".xls" else "openpyxl"
        raw = pd.read_excel(file_path, sheet_name=sheet, header=None, nrows=1, engine=engine)

    if len(raw) == 0:
        return []
    values = raw.iloc[0].tolist()
    blank = lambda v: v is None or (isinstance(v, float) and pd.isna(v))
    return [_stringify_header_value(v) for v in values if not blank(v) and _stringify_header_value(v) != ""]


def _read_row_count(file_path: str, extension: str, sheet=0) -> int:
    """Number of data rows below the header -- a plain header=0 read is
    fine here since only len(df) is used, never its (possibly
    duplicate-mangled) column names."""
    if extension == ".csv":
        for encoding in ("utf-8-sig", "latin-1"):
            try:
                return len(pd.read_csv(file_path, header=0, encoding=encoding))
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError("csv", b"", 0, 1, "unable to decode CSV with utf-8-sig or latin-1")
    engine = "xlrd" if extension == ".xls" else "openpyxl"
    return len(pd.read_excel(file_path, sheet_name=sheet, header=0, engine=engine))


def _score_sheet_columns(columns: list, slot) -> int:
    """How well `columns` (one sheet's header row) satisfies `slot`'s full
    requirements -- literal required_columns plus every month_families/
    fiscal_year_families member, each counted independently. Used only to
    RANK candidate sheets against each other (see _select_sheet); the real
    missing/unexpected computation in validate_review_file is separate and
    unaffected by this score."""
    norm_expected_set = {_normalize_header_lenient(c) for c in slot.required_columns}
    hits = len({_normalize_header_lenient(c) for c in columns} & norm_expected_set)

    for family in getattr(slot, "month_families", ()):
        required_months = {_MONTH_TO_NUM[m.lower()] for m in family.months}
        found_months = {m for m in (_match_month_in_family(c, family) for c in columns) if m is not None}
        hits += len(found_months & required_months)

    for family in getattr(slot, "fiscal_year_families", ()):
        if any(_match_fiscal_year_family(c, family) for c in columns):
            hits += 1

    return hits


def _select_sheet(file_path: str, extension: str, slot):
    """Which sheet in `file_path` holds the data to validate. Almost every
    Review System file has exactly one sheet, where this is a no-op
    (returns 0, identical to the previous hardcoded sheet_name=0 behavior).
    A few real files carry several -- Secondary Sales has one sheet per
    fiscal year, oldest first (unconditionally reading sheet 0 there
    silently validated the OLDEST fiscal year's data, e.g. 2022-23,
    instead of the current one); Annual Targets has one sheet per source.
    Neither "always first" nor "always last" is correct for both shapes,
    so this instead reads every sheet's header (cheap -- nrows=1) and picks
    whichever scores highest against `slot`'s full requirements (see
    _score_sheet_columns) -- literal columns AND family membership, since
    for a slot like Secondary Sales whose required_columns is now just a
    handful of identifier fields, literal columns alone can't tell two
    fiscal-year sheets apart (both carry "Region"/"HQ"/...). Ties keep the
    earliest sheet, so a file where sheet 0 already matches (the common
    case, and today's Annual Targets real file) behaves exactly as before."""
    if extension == ".csv":
        return 0
    engine = "xlrd" if extension == ".xls" else "openpyxl"
    try:
        sheet_names = pd.ExcelFile(file_path, engine=engine).sheet_names
    except Exception:
        return 0
    if len(sheet_names) <= 1:
        return 0

    best_sheet, best_hits = sheet_names[0], -1
    for name in sheet_names:
        try:
            columns = _read_raw_header_row(file_path, extension, sheet=name)
        except Exception:
            continue
        hits = _score_sheet_columns(columns, slot)
        if hits > best_hits:
            best_sheet, best_hits = name, hits
    return best_sheet


_CANONICAL_MONTH_YEAR_RE = re.compile(r"^\s*(0[1-9]|1[0-2]) \d{2}$")
_FY_RANGE_RE = re.compile(r"^\s*\d{2}\s+\d{2}$")


def _match_month_in_family(original_column: str, family) -> str | None:
    """The canonical 2-digit month ('04'..'07' etc.) if `original_column`
    is a member of `family` (app.review_schemas.MonthFamily) for ANY year,
    else None. Both the column and family's prefix/suffix go through the
    same _normalize_header_lenient pipeline used everywhere else in this
    module, so hyphen/apostrophe/spacing and Excel date-typed headers are
    handled identically to plain required_columns."""
    norm_col = _normalize_header_lenient(original_column)
    norm_prefix = _normalize_header_lenient(family.prefix) if family.prefix else ""
    if not norm_col.startswith(norm_prefix):
        return None
    remainder = norm_col[len(norm_prefix):]
    if family.allow_trailing_closing and remainder.endswith(" closing"):
        remainder = remainder[: -len(" closing")]
    norm_suffix = _normalize_header_lenient(family.suffix) if family.suffix else ""
    if norm_suffix:
        if not remainder.endswith(norm_suffix):
            return None
        remainder = remainder[: -len(norm_suffix)]
    match = _CANONICAL_MONTH_YEAR_RE.match(remainder.strip())
    return match.group(1) if match else None


def _match_fiscal_year_family(original_column: str, family) -> bool:
    """True if `original_column` is a member of `family`
    (app.review_schemas.FiscalYearFamily) for ANY well-formed 2-digit
    "<start>-<end>" fiscal-year range -- the specific range is never
    checked, only that one is present for one of the family's
    suffix_options (tried in order, so e.g. "Sup" vs "Support" are
    recognized as the same field)."""
    norm_col = _normalize_header_lenient(original_column)
    norm_prefix = _normalize_header_lenient(family.prefix)
    if not norm_col.startswith(norm_prefix):
        return False
    remainder = norm_col[len(norm_prefix):]
    for suffix in family.suffix_options:
        norm_suffix = _normalize_header_lenient(suffix)
        if norm_suffix and remainder.endswith(norm_suffix):
            if _FY_RANGE_RE.match(remainder[: -len(norm_suffix)].strip()):
                return True
    return False


def _is_monthly_series_extra(original_column: str, slot) -> bool:
    """True if `original_column` is an unlisted-but-valid extra member of
    one of the slot's monthly_series_prefixes families (e.g. "BM Visit
    Count Feb-2026" when only Apr-Jul are individually required) -- present
    only in files that happen to carry more history than required, never a
    reason to fail. Both prefix and column go through the same
    _normalize_header_lenient pipeline the rest of this module uses (not
    the bare base normalizer) so hyphen/underscore/period separators and
    month spelling variations are canonicalized the same way here as
    everywhere else -- after which the remainder must be an actual
    canonical "<month> <year>" pair, so an unrelated column that merely
    starts with the same words (no month/year following) is still flagged
    unexpected rather than silently accepted."""
    for prefix in getattr(slot, "monthly_series_prefixes", []):
        norm_prefix = _normalize_header_lenient(prefix)
        norm_column = _normalize_header_lenient(original_column)
        if norm_column.startswith(norm_prefix) and _CANONICAL_MONTH_YEAR_RE.match(norm_column[len(norm_prefix):]):
            return True
    return False


def validate_review_file(slot_id: str, file_path: str) -> dict:
    """Validate an uploaded file against the exact schema assigned to
    `slot_id` (app.review_schemas.REVIEW_FILE_SCHEMAS). Never raises --
    every failure mode is reported in the returned dict, same contract as
    app.excel_validation's engine.

    `valid` is driven ONLY by missing_columns and duplicate_columns (per
    real-file verification, 2026-08-18) -- unexpected_columns and
    wrong_order are still computed and returned for informational display,
    but never make an otherwise-complete upload invalid. See the module
    docstring for why.

    Returns:
        {
            "valid": bool,
            "slot_id": str,
            "filename": str,
            "missing_columns": [str],       # required, not found -- makes valid False
            "unexpected_columns": [str],    # found, not recognized -- informational only
            "wrong_order": bool,            # column SET matches, order doesn't -- informational only
            "duplicate_columns": [str],     # makes valid False
            "row_count": int | None,
            "column_count": int | None,
            "errors": [str],                # human-readable, ready to display when NOT valid
        }
    """
    slot = get_slot_def(slot_id)
    filename = Path(file_path).name
    expected = slot.required_columns

    result = {
        "valid": False,
        "slot_id": slot_id,
        "filename": filename,
        "missing_columns": [],
        "unexpected_columns": [],
        "wrong_order": False,
        "duplicate_columns": [],
        "row_count": None,
        "column_count": None,
        "errors": [],
    }

    extension = Path(file_path).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        result["errors"].append(f"Unsupported file type: '{extension}'.")
        logger.warning(f"Review System '{slot_id}': rejected unsupported file type {extension!r}")
        return result

    sheet = _select_sheet(file_path, extension, slot)

    try:
        actual_columns = _read_raw_header_row(file_path, extension, sheet=sheet)
    except Exception as exc:
        result["errors"].append(f"Could not read the file: {exc}")
        logger.warning(f"Review System '{slot_id}': failed to read '{file_path}': {exc}")
        return result

    if not actual_columns:
        result["errors"].append("The file is empty or has no header row.")
        return result

    try:
        result["row_count"] = _read_row_count(file_path, extension, sheet=sheet)
    except Exception as exc:
        result["errors"].append(f"Could not read the file's data rows: {exc}")
        logger.warning(f"Review System '{slot_id}': failed to read data rows of '{file_path}': {exc}")
        return result

    result["column_count"] = len(actual_columns)

    optional = getattr(slot, "optional_columns", [])
    month_columns = getattr(slot, "month_columns", [])
    norm_expected = [_normalize_header_lenient(c) for c in expected]
    norm_optional_set = {_normalize_header_lenient(c) for c in optional}
    norm_month_set = {_normalize_header_lenient(c) for c in month_columns}
    norm_actual = [_normalize_header_lenient(c) for c in actual_columns]

    # Duplicates: any normalized actual header appearing more than once
    # (including a duplicated optional column -- two "Zone" columns is
    # still a real problem worth flagging).
    seen: dict = {}
    duplicates: list = []
    for original, norm in zip(actual_columns, norm_actual):
        if norm in seen:
            if original not in duplicates:
                duplicates.append(original)
        else:
            seen[norm] = original
    result["duplicate_columns"] = duplicates

    norm_actual_set = set(norm_actual)
    norm_expected_set = set(norm_expected)

    missing = [name for name, norm in zip(expected, norm_expected) if norm not in norm_actual_set]

    # month_families/fiscal_year_families members (Secondary Sales' rolling
    # Pri/Sec/Sup/closing months and F.Y totals -- see app.review_schemas)
    # are required IN ADDITION to the literal `expected` list above, but
    # matched by calendar month or fiscal-year SHAPE rather than literal
    # text, since the year label genuinely varies by source and even
    # within one real file (see module docstring). `consumed_norms` marks
    # every actual column any family recognized as a member, regardless of
    # whether it ended up satisfying a specific requirement -- excluded
    # from `unexpected` below, same treatment as a literal optional column.
    consumed_norms = set()
    for family in getattr(slot, "month_families", ()):
        found_months = set()
        for original, norm in zip(actual_columns, norm_actual):
            month = _match_month_in_family(original, family)
            if month is not None:
                found_months.add(month)
                consumed_norms.add(norm)
        for month_name in family.months:
            if _MONTH_TO_NUM[month_name.lower()] not in found_months:
                label = f"{family.prefix}{month_name}'YY{family.suffix}".strip()
                missing.append(f"{label} (any year)")

    for family in getattr(slot, "fiscal_year_families", ()):
        found = False
        for original, norm in zip(actual_columns, norm_actual):
            if _match_fiscal_year_family(original, family):
                found = True
                consumed_norms.add(norm)
        if not found:
            label = f"{family.prefix}<FY>{' ' + family.suffix_options[0] if family.suffix_options else ''}".strip()
            missing.append(f"{label} (any fiscal year)")

    # Report each unexpected actual column once, in the order it appears --
    # an optional column (present or not) is never "unexpected", and
    # neither is a recognized extra member of a monthly_series_prefixes
    # family (e.g. "BM Visit Count Feb-2026" alongside the required
    # Apr-Jul -- see _is_monthly_series_extra) or of a month/fiscal-year
    # family (consumed_norms above).
    unexpected = []
    seen_unexpected = set()
    for original, norm in zip(actual_columns, norm_actual):
        if norm in norm_expected_set or norm in norm_optional_set or norm in seen_unexpected or norm in consumed_norms:
            continue
        if _is_monthly_series_extra(original, slot):
            continue
        unexpected.append(original)
        seen_unexpected.add(norm)

    result["missing_columns"] = missing
    result["unexpected_columns"] = unexpected

    # Order only means something once the column SET matches exactly --
    # otherwise missing/unexpected already explain what's wrong. Optional
    # columns, month-identity columns (slot.month_columns -- e.g. Annual
    # Targets' fiscal-order JAN..DEC, or Visits and Support's Feb-26..Jun-26
    # and BM Visit Count Apr-2026..Jul-2026), and recognized
    # monthly-series/period-family extras are all dropped before comparing:
    # none of them are required to appear in any particular position, only
    # to be present (already checked above) -- only the remaining columns
    # must appear in the exact expected order.
    #
    # NOTE (per real-file verification, 2026-08-18): `wrong_order` and
    # `unexpected_columns` are informational only -- see `valid` below and
    # the module docstring. Two real sources uploading the same report
    # through different regional processes can legitimately carry their
    # identifier columns in a different relative order, or a harmless extra
    # column neither family nor optional_columns names; that alone does not
    # make the upload the wrong report.
    wrong_order = False
    if not missing and not duplicates:
        ignored = norm_optional_set | norm_month_set | consumed_norms
        required_only_actual = [
            norm for original, norm in zip(actual_columns, norm_actual)
            if norm not in ignored and not _is_monthly_series_extra(original, slot)
        ]
        unique_norm_actual = list(dict.fromkeys(required_only_actual))
        order_sensitive_expected = [norm for norm in norm_expected if norm not in norm_month_set]
        if unique_norm_actual != order_sensitive_expected:
            wrong_order = True
    result["wrong_order"] = wrong_order

    if missing:
        result["errors"].append(f"Missing required column(s): {', '.join(missing)}")
    if unexpected:
        result["errors"].append(f"Unexpected column(s) found: {', '.join(unexpected)}")
    if duplicates:
        result["errors"].append(f"Duplicate column(s) found: {', '.join(duplicates)}")
    if wrong_order:
        result["errors"].append("Column order does not match the expected schema.")

    # A required column being genuinely absent, or a duplicate, is the only
    # thing that makes this NOT the correct business report -- a harmless
    # extra column or a different (but complete) column ordering is not.
    result["valid"] = not (missing or duplicates)

    if result["valid"]:
        logger.info(f"Review System '{slot_id}': '{filename}' valid ({result['row_count']} rows)")
    else:
        logger.warning(f"Review System '{slot_id}': '{filename}' invalid -- {result['errors']}")

    return result
