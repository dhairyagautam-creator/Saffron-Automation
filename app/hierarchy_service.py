"""Centralized reporting-hierarchy resolution -- the ONE place in the
application that decides who an employee's "Senior" (final escalation
recipient) is. Both the Organization Data table
(ui/organization_data_page.py, via the senior_* columns
app.hierarchy_parser.refresh_hierarchy() writes using this module) and the
email notification system (app/notification_service.py, reading those same
columns) end up showing the exact same person, because they read the exact
same precomputed value -- there is no second, separate fallback
implementation anywhere else in the codebase.

REDESIGNED 2026-07-28 to fix a real routing bug and its root cause (BM was
routing to the ABM instead of the RBM). SIMPLIFIED FURTHER the same day,
per explicit instruction, to get basic routing verified before any
fallback-beyond-RBM logic is trusted: BM and ABM validations now escalate
ONLY to their own directly-assigned RBM -- no Senior RBM, no SM, no AGM,
no GM. If that RBM is vacant, missing, null, or simply has no email on
file, NO ONE ELSE is tried -- the finding is Unresolved (see
app/notification_service.py's "RBM is Vacant. Email not sent." log/report
message), not silently escalated further. This is a deliberate, temporary
narrowing ("ignore the fallback system for now") -- restoring the SRRBM/SM
/AGM/GM fallback for BM/ABM later is a one-line change to FALLBACK_CHAINS
below, not a redesign, if/when that's asked for again.

In practice this is the ONLY resolution that ever matters for email
routing: rules/same_location.py's ANALYZABLE_DESIGNATIONS = {"BM", "ABM"}
means no other designation is ever flagged by the rule engine, so no other
designation's chain is ever consulted for an actual notification. RBM's/
SRRBM's/SM's/AGM's own chains below are UNCHANGED from the previous
redesign (still resolve to whoever is above them) -- purely for the
Organization Data table's own Senior column on those rows, not touched
because touching them wasn't asked for and has zero effect on emails.

THE ROUTING MODEL (internal design, not shown in any UI) -- BM's and ABM's
fixed escalation sequence, current as of the 2026-07-28 simplification:

    BM   or   ABM
     |         |
     v         v
        RBM
         |
         v
    (stop -- no further escalation. RBM vacant/missing/no-email means
     Unresolved, not "try Senior RBM/SM/AGM/GM")

This is a FIXED sequence, walked in order, not a recursive "keep climbing
until someone answers" search: for BM/ABM there is exactly one rung to
check (RBM). If that specific person is unavailable (vacant, missing, no
email, or simply doesn't exist in the data), STOP -- do not move to any
other rung.

ABM and RBM are the only two rungs carried as a direct field on an
employee's own hierarchy row (abm_code/abm_name, rbm_code/rbm_name, set by
app.hierarchy_parser from row-order tracking) -- both are still populated
and available exactly as before; BM's chain simply never reads its own
abm_code/abm_name, since ABM isn't in BM's routing sequence (unchanged
from the prior redesign -- BM never routed to its own ABM even before
today's further simplification).

A separate, company-specific override (Xandra division, BM HQ in
Muzaffarnagar/Saharanpur/Dehradun -> routes straight to a named Senior RBM)
lives entirely in app/notification_service.py, deliberately isolated there
-- it inspects finding-level HQ/division data this module has no access to,
and bypasses this module's routing entirely for those three HQs rather than
composing with it. Nothing here needs to know about it, and nothing there
needs to know about this module's internals beyond is_valid_recipient()."""

# The ONE routing table every escalation follows: for each starting
# designation, the exact, fully-listed sequence of rungs to try, in order.
# NOT derived from a shared ordering/slice -- each entry is independently
# correct and readable on its own. Add a new starting designation here (its
# own explicit list) rather than trying to derive one from another's.
#
# BM/ABM deliberately list ONLY "RBM" -- per explicit 2026-07-28
# instruction ("ignore the fallback system for now... completely disable
# all fallback logic"), restoring SRRBM/SM/AGM/GM as fallback rungs for
# these two is a one-line edit here, not a redesign, when that's asked
# for again. RBM/SRRBM/SM/AGM/GM's own chains are untouched (see module
# docstring for why -- never consulted for actual email routing).
FALLBACK_CHAINS: dict[str, list[str]] = {
    "BM": ["RBM"],
    "ABM": ["RBM"],
    "RBM": ["SRRBM", "SM", "AGM", "GM"],
    "SRRBM": ["SM", "AGM", "GM"],
    "SM": ["AGM", "GM"],
    "AGM": ["GM"],
    "GM": [],
}

# The safest default for a designation that isn't recognized/blank in the
# source data -- BM's chain (currently just RBM; see module docstring).
_DEFAULT_CHAIN = FALLBACK_CHAINS["BM"]

# ABM and RBM are the only two rungs carried as a direct field on an
# employee's own hierarchy row (abm_code/abm_name, rbm_code/rbm_name) --
# everything else in a chain is a designation lookup scoped to the same
# division + source_sheet instead (see module docstring). Unchanged by the
# 2026-07-28 routing fix: this describes how a rung's data is looked up,
# not which rungs a given designation's chain visits.
DIRECTLY_ASSIGNED_LEVELS = {"ABM", "RBM"}


def is_valid_recipient(row: dict | None) -> bool:
    """A hierarchy row is a valid, available Senior only if it has both a
    name and an email address. Vacant rows never reach here (see
    app.hierarchy_parser._is_vacant); a nonexistent row (None) is
    obviously unavailable. This is the ONE definition of "available" used
    everywhere -- there is no separate copy of this check anywhere else."""
    if not row:
        return False
    name = row.get("employee_name")
    email = row.get("email")
    return bool(name and str(name).strip()) and bool(email and str(email).strip())


def fallback_chain_for(designation: str | None) -> list[str]:
    """The fixed, ordered list of rungs to try for `designation` -- e.g.
    fallback_chain_for("BM") == ["RBM"] and fallback_chain_for("ABM") ==
    ["RBM"] (never "ABM" itself, see module docstring -- and, as of the
    2026-07-28 simplification, never SRRBM/SM/AGM/GM either). A
    blank/unrecognized designation gets the safest default (BM's own
    chain). fallback_chain_for("GM") == [] -- GM is the top of the chain,
    by design, not a data gap."""
    normalized = (designation or "").strip().upper()
    return FALLBACK_CHAINS.get(normalized, _DEFAULT_CHAIN)


def _resolve_direct_candidate(hierarchy_row: dict, level: str, by_code: dict, by_name: dict) -> dict | None:
    code = hierarchy_row.get(f"{level.lower()}_code")
    name = hierarchy_row.get(f"{level.lower()}_name")
    candidate = by_code.get(code) if code else None
    if candidate is None and name:
        candidate = by_name.get(str(name).strip().lower())
    return candidate


def resolve_senior_from_maps(
    hierarchy_row: dict, by_code: dict, by_name: dict, by_division_sheet_designation: dict
) -> dict | None:
    """Pure, DB-free resolution -- given pre-built lookup maps (see
    build_lookup_maps() below), walk `hierarchy_row`'s FIXED chain (see
    fallback_chain_for()) and return the first valid rung's hierarchy row,
    or None if nobody valid is found anywhere in the sequence. Used for
    bulk resolution (every employee in one Organization Data refresh)
    without one SQL round-trip per employee per rung -- see
    compute_seniors()."""
    division = hierarchy_row.get("division")
    source_sheet = hierarchy_row.get("source_sheet")

    for level in fallback_chain_for(hierarchy_row.get("designation")):
        if level in DIRECTLY_ASSIGNED_LEVELS:
            candidate = _resolve_direct_candidate(hierarchy_row, level, by_code, by_name)
        else:
            candidate = by_division_sheet_designation.get((division, source_sheet, level))
        if is_valid_recipient(candidate):
            return candidate

    return None


def build_lookup_maps(rows: list[dict]) -> tuple[dict, dict, dict]:
    """Build the three lookup maps resolve_senior_from_maps() needs, from a
    full list of hierarchy rows (dicts) -- first-match-wins per key,
    matching the semantics of app.hierarchy_parser's
    find_by_employee_code/find_by_employee_name/find_by_designation (each
    of which returns the first matching row)."""
    by_code: dict = {}
    by_name: dict = {}
    by_division_sheet_designation: dict = {}

    for row in rows:
        code = row.get("employee_code")
        if code and code not in by_code:
            by_code[code] = row

        name = row.get("employee_name")
        if name:
            by_name.setdefault(str(name).strip().lower(), row)

        designation = (row.get("designation") or "").strip().upper()
        key = (row.get("division"), row.get("source_sheet"), designation)
        by_division_sheet_designation.setdefault(key, row)

    return by_code, by_name, by_division_sheet_designation


def compute_seniors(rows: list[dict]) -> None:
    """Mutates `rows` in place, adding senior_code/senior_name/senior_email/
    senior_designation to every row -- the single computation the whole
    application relies on. Called once, from
    app.hierarchy_parser.refresh_hierarchy(), right after parsing and
    before writing to the employee_hierarchy table; every other part of the
    app (the Organization Data table, the email notification system) just
    reads the resulting stored columns instead of re-deriving anything, so
    the table and the emails can never disagree.

    Never writes Python None -- always a string (possibly empty), so a
    round trip through SQLite/pandas.read_sql_table can never turn a
    missing Senior into a literal "NaN" in the UI (see
    ui/organization_data_page.py's _load_hierarchy_from_db)."""
    by_code, by_name, by_division_sheet_designation = build_lookup_maps(rows)

    for row in rows:
        chain = fallback_chain_for(row.get("designation"))
        if not chain:
            # Top of the chain (GM) -- there is nothing above by design,
            # not a data gap.
            row["senior_code"] = ""
            row["senior_name"] = "Top Level"
            row["senior_email"] = ""
            row["senior_designation"] = ""
            continue

        senior = resolve_senior_from_maps(row, by_code, by_name, by_division_sheet_designation)
        row["senior_code"] = senior.get("employee_code", "") if senior else ""
        row["senior_name"] = senior.get("employee_name", "") if senior else ""
        row["senior_email"] = senior.get("email", "") if senior else ""
        row["senior_designation"] = senior.get("designation", "") if senior else ""


def resolve_senior(hierarchy_row: dict | None) -> dict | None:
    """Single-row, DB-backed resolution -- for the rare on-demand case
    where a caller has one employee's hierarchy row and wants their Senior
    without a full Organization Data refresh. Prefer reading the
    precomputed senior_name/senior_email columns (populated by
    compute_seniors() above) wherever they're already available -- this is
    here for completeness, not the primary path. Uses the exact same
    FALLBACK_CHAINS/is_valid_recipient/fallback_chain_for rules as
    compute_seniors(), just backed by live queries instead of in-memory
    maps."""
    if not hierarchy_row:
        return None

    # Deferred import: app.hierarchy_parser calls compute_seniors() (above)
    # during refresh_hierarchy(), so a module-level import here would be
    # circular. By the time this function is actually called, both modules
    # are fully loaded.
    from app.hierarchy_parser import find_by_designation, find_by_employee_code, find_by_employee_name

    division = hierarchy_row.get("division")
    source_sheet = hierarchy_row.get("source_sheet")

    for level in fallback_chain_for(hierarchy_row.get("designation")):
        if level in DIRECTLY_ASSIGNED_LEVELS:
            code = hierarchy_row.get(f"{level.lower()}_code")
            name = hierarchy_row.get(f"{level.lower()}_name")
            candidate = find_by_employee_code(code) if code else None
            if candidate is None and name:
                matches = find_by_employee_name(name)
                candidate = matches[0] if matches else None
        else:
            candidate = find_by_designation(division, source_sheet, level)
        if is_valid_recipient(candidate):
            return candidate

    return None
