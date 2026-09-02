"""Shared "Date of Joining" (DOJ) monthly-eligibility rule for Work
Distribution -- the ONE place both RGD Coverage
(app/work_distribution_service.py) and Manager Work Allocation
(app/manager_work_allocation_service.py / _rbm_service.py) decide whether
an employee was actually active during a given calendar month, so the two
engines can never disagree about it.

DOJ lives on an employee's own employee_hierarchy row (see
app/hierarchy_parser.py's "doj" column, normalized to an ISO 'YYYY-MM-DD'
string at parse time). This module never touches the Organization Data
workbooks itself -- load_doj_by_code()/load_doj_by_name() below read the
already-parsed employee_hierarchy table via app.hierarchy_parser's own
bulk-lookup helpers (get_all_doj/get_doj_by_name), mirroring
rules/same_location.py's existing "one bulk query, not one per employee"
pattern (see app.hierarchy_parser.get_all_designations).

MONTHLY RULE, given an employee's DOJ and one calendar month (year, month):
- No DOJ on file (None) -> ACTIVE. Never invented or inferred -- this is
  the SAME outcome as an employee this module can't resolve a DOJ for,
  including every vacant hierarchy position (which never gets a hierarchy
  row at all -- see app.hierarchy_parser._is_vacant) and any BM/ABM/RBM
  whose name/code simply isn't found in the hierarchy. Existing behavior
  is completely unchanged in every one of these cases.
- DOJ falls AFTER the month's own last calendar day -> NOT_YET_JOINED.
  The month must be excluded entirely from that employee's own averages/
  benchmarks/trends/aggregates -- never counted as a zero.
- DOJ falls WITHIN the month (same year+month) -> STARTED_MONTH. Still
  fully eligible/included for that month, under whatever the existing
  business logic already does with that month's actual reported activity
  -- this label exists so a caller can choose to disclose the partial
  month, not to change what's counted (the "partial month" protection is
  automatic: an uploaded report only ever contains activity that actually
  happened, so there is no separate "assume they worked every day" logic
  anywhere in this app for this label to guard against).
- Otherwise (DOJ on/before the month's first day) -> ACTIVE.
"""

from calendar import monthrange
from datetime import date

import pandas as pd

from app.hierarchy_parser import get_all_doj, get_doj_by_name

ACTIVE = "ACTIVE"
STARTED_MONTH = "STARTED_MONTH"
NOT_YET_JOINED = "NOT_YET_JOINED"

# The exact status string every consumer stores/displays -- one literal,
# not re-typed at each call site.
NOT_YET_JOINED_LABEL = "NOT YET JOINED"


def parse_doj(value) -> date | None:
    """Parses a DOJ value (the ISO 'YYYY-MM-DD' string
    app.hierarchy_parser stores, or a blank/None) into a plain date.
    Anything blank or unparseable returns None -- treated identically to
    "no DOJ on file" by monthly_status() below, never guessed at."""
    if not value:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def monthly_status(doj: date | None, year: int | None, month: int | None) -> str:
    """One employee's eligibility for one calendar month -- see module
    docstring for the full rule. Returns ACTIVE (never restricts) if
    `year`/`month` themselves couldn't be determined by the caller (e.g.
    an unparseable period label) -- there is no month to compare DOJ
    against, so existing behavior is left completely unchanged rather than
    guessed at."""
    if doj is None or year is None or month is None:
        return ACTIVE
    month_end = date(year, month, monthrange(year, month)[1])
    if doj > month_end:
        return NOT_YET_JOINED
    if doj.year == year and doj.month == month:
        return STARTED_MONTH
    return ACTIVE


def is_eligible_for_month(doj: date | None, year: int | None, month: int | None) -> bool:
    """False only for NOT_YET_JOINED -- both ACTIVE and STARTED_MONTH are
    eligible (see module docstring)."""
    return monthly_status(doj, year, month) != NOT_YET_JOINED


def load_doj_by_code() -> dict[str, date]:
    """{employee_code: DOJ} for every hierarchy row with a DOJ on file --
    for Manager Work Allocation, whose ManagerWorkAllocationRecord rows
    carry the subordinate's own employee_code (team_emp_code). Blank/
    unparseable DOJ values are dropped rather than stored as None, so
    lookups can use a plain `dict.get(code)` and trust a hit is always a
    real date."""
    return {code: parsed for code, raw in get_all_doj().items() if (parsed := parse_doj(raw)) is not None}


def load_doj_by_name() -> dict[str, date]:
    """{normalized (TRIM/LOWER) employee_name: DOJ} for every hierarchy
    row with a DOJ on file -- for RGD Coverage, whose uploaded doctor rows
    only ever carry the BM/ABM's NAME (no employee_code), and as the
    fallback for Manager Work Allocation when a record has no
    team_emp_code on file."""
    return {name: parsed for name, raw in get_doj_by_name().items() if (parsed := parse_doj(raw)) is not None}


def resolve_doj(
    code: str | None, name: str | None, doj_by_code: dict[str, date], doj_by_name: dict[str, date]
) -> date | None:
    """Code-first, name-fallback DOJ lookup for one subordinate -- the ONE
    place both Manager Work Allocation engines (ABM/RBM) resolve a BM's
    DOJ, so a record missing team_emp_code (nullable -- see
    ManagerWorkAllocationRecord's own docstring) still resolves by name
    exactly the same way in both engines."""
    doj = doj_by_code.get(code) if code else None
    if doj is not None:
        return doj
    return doj_by_name.get((name or "").strip().lower())
