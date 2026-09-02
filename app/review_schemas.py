"""The 12 required Review System upload slots and their exact expected
schemas -- the single source of truth every other Review System file reads
from (validator, upload service, Uploads page, and later the Analysis
engine).

Column lists are transcribed verbatim from the specification -- do not
reorder, rename, add, or remove columns here. If a schema needs to change,
it changes here once; nothing else hardcodes a column list.

Two explicit, narrow exceptions (per real-file verification, 2026-08-18):
Last Year Primary Sales doesn't require Tax Name/L.R. No./L.R.Date/
BasicValue (Primary Sales itself is unaffected), and Avg. and Calls
doesn't require Zone. Both are modeled as `optional_columns` on the
affected ReviewSlotDef, not simply deleted from required_columns --
present or absent, they're never checked either way (see
app/review_validation.py).

Month-identity columns (per real-file verification, 2026-08-18): a
required column that names a specific month is declared in
`month_columns` below -- still required (must be present), but exempt
from column-ORDER checking (Annual Targets' real export lists months
Apr-Mar fiscal order, not Jan-Dec) and matched by logical month/year, not
literal text (a date-typed Excel cell reads back as a datetime, not the
visible "Feb-26" label -- see app.review_validation._stringify_header_value).
`monthly_series_prefixes` additionally tolerates MORE months than
required appearing under a shared prefix (e.g. a real Visits & Support
file carrying "BM Visit Count Feb-2026"/"Mar-2026" alongside the required
Apr-Jul) -- present extras are recognized, never flagged unexpected.

Column order and unrecognized extra columns are NEVER a validity
requirement anywhere in this module (per real-file verification,
2026-08-18) -- app.review_validation.validate_review_file's `valid` is
driven entirely by required columns being present (literal, plus the
family concepts below) and by duplicate columns; `wrong_order` and
`unexpected_columns` are still computed and returned, but informational
only. Two real-world sources uploading the SAME report through different
regional processes -- one carrying a stray "Division" column, another
with its identifier columns in a different relative order -- are still
recognizably the right report and must not be rejected for it. A file is
still correctly rejected when it's genuinely missing required data, or
belongs to a different report entirely (which shows up as required
columns being absent, not as extra columns being present).

Period families (per real-file verification, 2026-08-18: Secondary
Sales' Onyx/Guardians/Xandra sources each independently maintain their
own workbook, and their month/fiscal-year LABELS drift out of sync with
each other and with the calendar -- e.g. one source's real file labels
its rolling Apr-Jul columns "Apr'25/May'26/Jun'26/Jul'26" in the same
row). `MonthFamily` declares a required set of CALENDAR MONTHS sharing a
literal prefix/suffix (e.g. suffix=" Pri") where the YEAR is a wildcard,
never checked, and may even be inconsistent across sibling members within
one file. `FiscalYearFamily` similarly declares a required
"F.Y <any 2-digit>-<any 2-digit><suffix>" column where the specific
fiscal-year range is a wildcard -- only that a well-formed one exists for
that suffix (trying `suffix_options` in order, so a spelling variant like
"Sup" vs "Support" is recognized as the same field). Both are matched via
the same _normalize_header_lenient pipeline as everything else in
app/review_validation.py, so hyphen/apostrophe/spacing and Excel
date-typed headers are handled identically to plain required_columns.
"""

from dataclasses import dataclass, field

OPUS_SUMMARY = "Opus Summary"
COVERAGE_SUMMARY = "Coverage Summary"

# Columns that, when present in a real file for a given slot, must not be
# checked at all -- neither required (missing doesn't fail) nor flagged as
# unexpected (present doesn't fail either). See ReviewSlotDef.optional_columns.
LAST_YEAR_PRIMARY_SALES_OPTIONAL_COLUMNS = ["Tax Name", "L.R. No.", "L.R.Date", "BasicValue"]
AVG_AND_CALLS_OPTIONAL_COLUMNS = ["Zone"]

# Secondary Sales' identifier columns -- literal, always required. The
# rolling-month sales figures, fiscal-year totals, and month-end closing
# values are NOT listed here as literal text (see SECONDARY_SALES_MONTH_FAMILIES
# / SECONDARY_SALES_FY_FAMILIES below and the module docstring) because
# their year labels genuinely differ, even inconsistently within a single
# real file.
SECONDARY_SALES_COLUMNS = [
    "Region",
    "HQ",
    "ABM Name",
    "No Of BM",
]

# The 4 calendar months every Secondary Sales file's rolling reporting
# window must cover -- the specific year attached to each is never
# checked (see MonthFamily, defined below).
SECONDARY_SALES_MONTHS = ["Apr", "May", "Jun", "Jul"]

AVG_AND_CALLS_COLUMNS = [
    "Division",
    "Region",
    "Employee Name",
    "Employee Code",
    "Desig.",
    "Reporting HQ",
    "Parameters",
    "Apr-2026",
    "May-2026",
    "Jun-2026",
    "Jul-2026",
]
AVG_AND_CALLS_MONTH_COLUMNS = ["Apr-2026", "May-2026", "Jun-2026", "Jul-2026"]

VISITS_AND_SUPPORT_COLUMNS = [
    "Division",
    "Region",
    "HQ",
    "BM code",
    "BM Name",
    "Dr. Code",
    "Dr. Name",
    "Town",
    "Category",
    "Speciality",
    "Feb-26",
    "Mar-26",
    "Apr-26",
    "May-26",
    "Jun-26",
    "BM Visit Count Apr-2026",
    "BM Visit Count May-2026",
    "BM Visit Count Jun-2026",
    "BM Visit Count Jul-2026",
]

PRIMARY_SALES_COLUMNS = [
    "Trans.Type",
    "Daybook Name",
    "BranchLocation",
    "Br.Sh Name",
    "InvoiceNo.",
    "A/c Date",
    "Month",
    "Year",
    "Tax Name",
    "Item Group",
    "CustomerCode",
    "Customer Name",
    "City",
    "Territory Name",
    "HQ",
    "BM",
    "Region Name",
    "HSN Code",
    "Item Code",
    "Item Name",
    "Division",
    "Packing",
    "Batch No.",
    "Mfg.Date",
    "Exp.Date",
    "BatchCost Rate",
    "GST%",
    "ExpiryMonths",
    "M.R.P.",
    "PTSRate",
    "PTRRate",
    "Rate",
    "Disc.",
    "MOP",
    "SalesRate",
    "BasicValue",
    "Disc.Value",
    "Qty.",
    "FreeQty.",
    "TotalQty",
    "Disc.Sales",
    "NetSales",
    "TaxAmount",
    "AmountAfter Tax",
    "Order No.",
    "OrderDate",
    "Transporter",
    "State",
    "L.R. No.",
    "L.R.Date",
]

# Last Year Primary Sales shares Primary Sales' report format, except the
# real prior-year export omits these four columns entirely -- Primary
# Sales itself (PRIMARY_SALES_COLUMNS above) is untouched.
LAST_YEAR_PRIMARY_SALES_COLUMNS = [
    c for c in PRIMARY_SALES_COLUMNS if c not in LAST_YEAR_PRIMARY_SALES_OPTIONAL_COLUMNS
]

ANNUAL_TARGETS_COLUMNS = [
    "REGION",
    "HQ",
    "NO. OF BMs",
    "ANNUAL TGT",
    "JAN TGT",
    "FEB TGT",
    "MAR TGT",
    "APRIL TGT",
    "MAY TGT",
    "JUN TGT",
    "JULY TGT",
    "AUG TGT",
    "SEP TGT",
    "OCT TGT",
    "NOV TGT",
    "DEC TGT",
    "TOTAL",
    "DIFF",
]
# The 12 month-target columns -- required, but the real export lists them
# in fiscal-year order (Apr..Mar), not Jan..Dec, so they're order-exempt.
ANNUAL_TARGETS_MONTH_COLUMNS = [c for c in ANNUAL_TARGETS_COLUMNS if c.endswith(" TGT") and c != "ANNUAL TGT"]

# Visits and Support's month-identity columns -- order-exempt and matched
# by logical month/year, not literal text (see module docstring). The
# "BM Visit Count " family additionally tolerates extra earlier/later
# months beyond the 4 explicitly required.
VISITS_AND_SUPPORT_PLAIN_MONTH_COLUMNS = ["Feb-26", "Mar-26", "Apr-26", "May-26", "Jun-26"]
VISITS_AND_SUPPORT_BM_VISIT_COUNT_COLUMNS = [
    "BM Visit Count Apr-2026",
    "BM Visit Count May-2026",
    "BM Visit Count Jun-2026",
    "BM Visit Count Jul-2026",
]

# Secondary Sales' month-identity columns (excludes the "F.Y 25-26 ..."
# fiscal-year totals, which don't name a specific month). Secondary
# Sales no longer lists literal month columns in required_columns at all
# (see SECONDARY_SALES_MONTH_FAMILIES/_FY_FAMILIES below), so there is
# nothing left to enumerate here for that slot.


@dataclass(frozen=True)
class MonthFamily:
    """A required set of CALENDAR months sharing a literal prefix/suffix,
    where the YEAR attached to each is a wildcard -- never checked, and
    may even differ between sibling members within one real file (see
    module docstring). `allow_trailing_closing` additionally strips an
    optional " Closing" suffix before matching, so "Apr'26" and
    "Apr'26 Closing" are recognized as the same family member."""

    months: tuple
    prefix: str = ""
    suffix: str = ""
    allow_trailing_closing: bool = False


@dataclass(frozen=True)
class FiscalYearFamily:
    """A required "F.Y <any 2-digit>-<any 2-digit><suffix>" column -- the
    specific fiscal-year range is a wildcard, never checked, only that a
    well-formed one exists for this suffix. `suffix_options` are tried in
    order (first match wins), so spelling variants (e.g. "Sup" vs
    "Support") are recognized as the same field."""

    suffix_options: tuple
    prefix: str = "F.Y "


@dataclass(frozen=True)
class ReviewSlotDef:
    """Static definition of one upload slot -- never mutated. Runtime state
    (uploaded/valid/filename/etc.) lives in database.models.ReviewFileSlot,
    keyed by slot_id."""

    slot_id: str
    display_name: str
    category: str
    subcategory: str
    source_name: str | None  # "Onyx" / "Guardians" / "Xandra", or None
    required_columns: list
    # Recognized but never checked -- present or absent, these never cause
    # a missing/unexpected failure and never affect order comparison. Empty
    # for every slot except the two explicit exceptions below.
    optional_columns: list = field(default_factory=list)
    # Required columns identifying a specific month -- still required, but
    # exempt from order checking (matched by set membership, any position)
    # and matched by logical month/year rather than literal text.
    month_columns: list = field(default_factory=list)
    # Literal column-name prefixes (e.g. "BM Visit Count ") whose matching
    # suffix is a month/year -- ANY actual column shaped
    # "<prefix><month>-<year>" is recognized as a member of this family,
    # even for a month not individually listed in required_columns, and is
    # never flagged unexpected. The specific months IN required_columns are
    # still required as normal.
    monthly_series_prefixes: list = field(default_factory=list)
    # Required MonthFamily/FiscalYearFamily members (see their docstrings
    # and the module docstring) -- required IN ADDITION TO required_columns,
    # each contributing its own "missing" entries when unsatisfied. Empty
    # for every slot except Secondary Sales.
    month_families: tuple = field(default_factory=tuple)
    fiscal_year_families: tuple = field(default_factory=tuple)

    @property
    def expected_column_order(self) -> list:
        return self.required_columns


def _secondary_sales_slot(source: str) -> ReviewSlotDef:
    return ReviewSlotDef(
        slot_id=f"opus_secondary_sales_{source.lower()}",
        display_name=source,
        category=OPUS_SUMMARY,
        subcategory="Secondary Sales",
        source_name=source,
        required_columns=SECONDARY_SALES_COLUMNS,
        month_families=(
            MonthFamily(suffix=" Pri", months=tuple(SECONDARY_SALES_MONTHS)),
            MonthFamily(suffix=" Sec", months=tuple(SECONDARY_SALES_MONTHS)),
            MonthFamily(suffix=" Sup", months=tuple(SECONDARY_SALES_MONTHS)),
            MonthFamily(months=tuple(SECONDARY_SALES_MONTHS), allow_trailing_closing=True),
        ),
        fiscal_year_families=(
            FiscalYearFamily(suffix_options=("Prim",)),
            FiscalYearFamily(suffix_options=("Sec",)),
            FiscalYearFamily(suffix_options=("Sup", "Support")),
        ),
    )


def _avg_calls_slot(source: str) -> ReviewSlotDef:
    return ReviewSlotDef(
        slot_id=f"coverage_avg_calls_{source.lower()}",
        display_name=source,
        category=COVERAGE_SUMMARY,
        subcategory="Avg. and Calls",
        source_name=source,
        required_columns=AVG_AND_CALLS_COLUMNS,
        optional_columns=AVG_AND_CALLS_OPTIONAL_COLUMNS,
    )


def _visits_support_slot(source: str) -> ReviewSlotDef:
    return ReviewSlotDef(
        slot_id=f"coverage_visits_support_{source.lower()}",
        display_name=source,
        category=COVERAGE_SUMMARY,
        subcategory="Visits and Support",
        source_name=source,
        required_columns=VISITS_AND_SUPPORT_COLUMNS,
        month_columns=VISITS_AND_SUPPORT_PLAIN_MONTH_COLUMNS + VISITS_AND_SUPPORT_BM_VISIT_COUNT_COLUMNS,
        monthly_series_prefixes=["BM Visit Count "],
    )


# The 12 required slots, in the exact hierarchy/display order the
# specification defines. This tuple is the single source of truth for slot
# order, count, and grouping -- the Uploads page renders directly from it,
# never from a hand-maintained UI-side list.
REVIEW_FILE_SLOTS: tuple[ReviewSlotDef, ...] = (
    ReviewSlotDef(
        slot_id="opus_annual_targets",
        display_name="Annual Targets",
        category=OPUS_SUMMARY,
        subcategory="Annual Targets",
        source_name=None,
        required_columns=ANNUAL_TARGETS_COLUMNS,
        month_columns=ANNUAL_TARGETS_MONTH_COLUMNS,
    ),
    ReviewSlotDef(
        slot_id="opus_primary_sales",
        display_name="Primary Sales",
        category=OPUS_SUMMARY,
        subcategory="Primary Sales",
        source_name=None,
        required_columns=PRIMARY_SALES_COLUMNS,
    ),
    ReviewSlotDef(
        slot_id="opus_last_year_primary_sales",
        display_name="Last Year Primary Sales",
        category=OPUS_SUMMARY,
        subcategory="Last Year Primary Sales",
        source_name=None,
        # Same report format as Primary Sales, minus the 4 columns the real
        # prior-year export doesn't carry -- still a separate slot, never
        # inferred as satisfying opus_primary_sales or vice versa; that
        # slot's own schema (PRIMARY_SALES_COLUMNS) is unchanged.
        required_columns=LAST_YEAR_PRIMARY_SALES_COLUMNS,
        optional_columns=LAST_YEAR_PRIMARY_SALES_OPTIONAL_COLUMNS,
    ),
    _secondary_sales_slot("Onyx"),
    _secondary_sales_slot("Guardians"),
    _secondary_sales_slot("Xandra"),
    _avg_calls_slot("Onyx"),
    _avg_calls_slot("Guardians"),
    _avg_calls_slot("Xandra"),
    _visits_support_slot("Onyx"),
    _visits_support_slot("Guardians"),
    _visits_support_slot("Xandra"),
)

TOTAL_REQUIRED_SLOTS = len(REVIEW_FILE_SLOTS)
assert TOTAL_REQUIRED_SLOTS == 12

# Convenience dict form, as specified: {slot_id: required_columns}. Derived
# from REVIEW_FILE_SLOTS, never maintained separately.
REVIEW_FILE_SCHEMAS: dict = {slot.slot_id: slot.required_columns for slot in REVIEW_FILE_SLOTS}

_SLOTS_BY_ID: dict = {slot.slot_id: slot for slot in REVIEW_FILE_SLOTS}


def get_slot_def(slot_id: str) -> ReviewSlotDef:
    slot = _SLOTS_BY_ID.get(slot_id)
    if slot is None:
        raise ValueError(f"Unknown Review System slot_id: {slot_id!r}")
    return slot
