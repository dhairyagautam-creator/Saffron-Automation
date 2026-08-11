"""Shared building blocks for BOTH Manager Work Allocation engines -- ABM
(app/manager_work_allocation_service.py) and RBM
(app/manager_work_allocation_rbm_service.py).

Redesigned 2026-08-05 for the rolling six-month history architecture: what
this module used to treat as "duplicate manager/subordinate rows to merge
by summing" were never duplicates at all -- each uploaded report already
contains several months of history, and a pair appearing more than once
simply means separate MONTHLY records for that pair. The two genuinely
shared pieces of the pipeline now live here:
1. `merge_same_month_duplicates` -- collapses a genuine intra-month
   duplicate (same pair, same month, split across >1 raw row) by summing,
   same as before, just correctly scoped to ALSO require the same month
   (previously it merged by Emp Code + Team Emp Code ALONE, which silently
   summed together what were actually separate months of history -- the
   root cause this redesign fixes).
2. `sync_rolling_window` -- the ONE place either engine ever writes to
   ManagerWorkAllocationRecord: upserts each new monthly record (an
   already-known month for a pair gets corrected/replaced, never
   duplicated), then trims that engine's own stored history down to the
   newest ROLLING_WINDOW_SIZE (6) DISTINCT months -- purely by month
   VALUE (see `parse_month`/`month_sort_key`), never by upload order,
   filename, or manual replacement, per explicit instruction. Both
   engines call this same function; only what they do with the RETAINED
   rows afterward differs (ABM averages per pair, RBM sums per pair --
   see each engine's own module docstring).

Both engines' business rules genuinely differ (ABM: rolling-average
day-count threshold per BM; RBM: BM-count-tiered missed-BM threshold,
worked-with-at-least-once coverage, summed across the window) -- see each
engine's own module docstring.
"""

import re
from collections import Counter

from loguru import logger

ROLLING_WINDOW_SIZE = 6

_MONTH_NAME_TO_NUMBER = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_NUMBER_TO_ABBR = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


def is_designation(value, target: str) -> bool:
    """True if a designation cell (Emp Designation / Team Emp Designation)
    matches `target` (e.g. "ABM", "BM", "RBM"), case/whitespace-insensitive."""
    return bool(value) and str(value).strip().upper() == target


def first_nonblank(values):
    for value in values:
        if value:
            return value
    return None


def group_by(items: list, key_fn) -> dict:
    """Groups `items` by `key_fn(item)`, preserving first-seen order --
    skips any item whose key is falsy (e.g. a blank string)."""
    groups: dict = {}
    order: list = []
    for item in items:
        key = key_fn(item)
        if not key:
            continue
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)
    return {key: groups[key] for key in order}


def _format_month_label(year: int, month_number: int) -> str:
    return f"{_MONTH_NUMBER_TO_ABBR[month_number]}-{year}"


def month_sort_key(year: int, month_number: int) -> int:
    """A single sortable integer -- e.g. 202607 for Jul-2026 -- used both
    for in-Python sorting and as ManagerWorkAllocationRecord's own
    month_sort_key column, so "the newest 6 months" is always a plain
    integer comparison, never a string-sort footgun (which would put
    "Oct-2026" before "Feb-2027", alphabetically)."""
    return year * 100 + month_number


def _expand_year(raw_year: int) -> int:
    """A 4-digit year passes through unchanged. A 2-digit year (e.g. the
    "26" in a real uploaded report's "Feb-26") is expanded using the same
    pivot convention Python's own `time.strptime`/`%y` uses: 00-68 ->
    2000-2068, 69-99 -> 1969-1999. Confirmed necessary against a real
    uploaded report (2026-08-05) whose Month column reads "Feb-26"/
    "Mar-26"/etc, not "Feb-2026" -- with a 4-digit-only year, EVERY row
    failed to parse and silently vanished in `merge_same_month_duplicates`,
    which is indistinguishable from "no data" downstream (Run Analysis
    reporting 0 ABM(s)/0 RBM(s) despite thousands of rows uploaded)."""
    if raw_year >= 100:
        return raw_year
    return 2000 + raw_year if raw_year <= 68 else 1900 + raw_year


def parse_month(value):
    """Parses a "Month" cell into (year, month_number, display_label), or
    None if unrecognized. Tolerant of the likely real-world spellings for
    this business report -- "Jul-2026", "July-2026", "Jul 2026",
    "July 2026", "2026-07", "07-2026", "07/2026", month name/abbreviation
    case-insensitive, hyphen/space/slash separator, AND a 2-digit year in
    any of these shapes ("Feb-26", confirmed against a real uploaded
    report -- see `_expand_year`). `display_label` is always normalized to
    "Mon-YYYY" (e.g. "Jul-2026") regardless of how the source file spelled
    it -- including expanding a 2-digit year to 4 digits -- so table
    columns/sorting stay consistent even if different uploads spell the
    same month differently.

    A cell that doesn't match any of these shapes returns None -- callers
    (see `merge_same_month_duplicates`) drop that row entirely rather than
    guessing at a position in the rolling window: there is no safe
    fallback for "some month we can't identify" that wouldn't risk
    corrupting the trim logic."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # "Jul-2026" / "July-2026" / "Jul 2026" / "July 2026" / "Jul/2026" / "Jul-26"
    m = re.match(r"^([A-Za-z]+)[\s\-/]+(\d{4}|\d{2})$", text)
    if m:
        month_number = _MONTH_NAME_TO_NUMBER.get(m.group(1).lower())
        if month_number:
            year = _expand_year(int(m.group(2)))
            return (year, month_number, _format_month_label(year, month_number))

    # "2026-07" / "2026/07" / "2026 07" (year-first numeric)
    m = re.match(r"^(\d{4})[\s\-/](\d{1,2})$", text)
    if m:
        year, month_number = int(m.group(1)), int(m.group(2))
        if 1 <= month_number <= 12:
            return (year, month_number, _format_month_label(year, month_number))

    # "07-2026" / "07/2026" / "07 2026" / "07-26" (month-first numeric)
    m = re.match(r"^(\d{1,2})[\s\-/](\d{4}|\d{2})$", text)
    if m:
        month_number, year = int(m.group(1)), _expand_year(int(m.group(2)))
        if 1 <= month_number <= 12:
            return (year, month_number, _format_month_label(year, month_number))

    return None


def log_designation_filter_diagnostics(
    engine_name: str, records: list, manager_designation: str, subordinate_designation: str, filtered_rows: list,
) -> None:
    """TEMPORARY debug aid for the "upload reports N records, then Run
    Analysis shows 0 ABM(s)/0 RBM(s)" symptom -- logs how many raw records
    came in vs. survived the (Emp Designation, Team Emp Designation)
    filter, and -- only when the filter zeroed out entirely -- a
    distinct-value dump of what designations were ACTUALLY present in this
    upload, so a real-world spelling/value mismatch (extra punctuation, a
    designation this filter doesn't expect, an empty column) is visible
    immediately from one log line instead of requiring a second
    investigation round-trip. Remove once the root cause behind any
    zero-count report is confirmed and fixed -- this is not meant to be
    permanent logging."""
    logger.info(
        f"Manager Work Allocation ({engine_name}) filter diagnostics: "
        f"{len(records)} raw record(s) in -> {len(filtered_rows)} "
        f"{manager_designation}/{subordinate_designation} row(s) after filter"
    )
    if records and not filtered_rows:
        emp_designations = Counter(r["emp_designation"] for r in records)
        team_designations = Counter(r["team_emp_designation"] for r in records)
        logger.warning(
            f"Manager Work Allocation ({engine_name}): filter matched ZERO of {len(records)} record(s) -- "
            f"distinct Emp Designation value(s) seen: {dict(emp_designations)}; "
            f"distinct Team Emp Designation value(s) seen: {dict(team_designations)}. "
            f"Expected exactly {manager_designation!r} / {subordinate_designation!r} "
            "(case/whitespace-insensitive -- see is_designation())."
        )


def merge_same_month_duplicates(rows: list) -> list:
    """Groups by (Emp Code, Team Emp Code, Month) -- rows sharing all
    three represent a genuine duplicate entry for the SAME manager/
    subordinate pair in the SAME month (a data-entry split, not a
    different month's own history) and are merged by SUMMING their joint
    days. Rows for the SAME pair in DIFFERENT months are NEVER merged
    here -- each stays its own distinct monthly record, which is what the
    rolling window is built from (see `sync_rolling_window`).

    Rows whose Month cell doesn't parse (see `parse_month`) are dropped
    entirely -- silently guessing a position for them in the rolling
    window would corrupt the trim logic for every pair, not just the one
    unparseable row.

    Returns one merged dict per (pair, month) group: division, month
    (normalized display label), month_sort_key, emp_code, emp_name,
    emp_designation, team_emp_code, team_emp_name, team_emp_designation,
    joint_days (SUM within that exact pair+month only)."""
    parsed_rows = []
    for r in rows:
        parsed = parse_month(r["month"])
        if parsed is None:
            continue
        year, month_number, label = parsed
        parsed_rows.append({**r, "_month_sort_key": month_sort_key(year, month_number), "_month_label": label})

    groups = group_by(parsed_rows, lambda r: (r["emp_code"], r["team_emp_code"], r["_month_sort_key"]))

    merged = []
    for group_rows in groups.values():
        merged.append({
            "division": first_nonblank(r["division"] for r in group_rows),
            "month": group_rows[0]["_month_label"],
            "month_sort_key": group_rows[0]["_month_sort_key"],
            "emp_code": first_nonblank(r["emp_code"] for r in group_rows),
            "emp_name": first_nonblank(r["emp_name"] for r in group_rows),
            "emp_designation": first_nonblank(r["emp_designation"] for r in group_rows),
            "team_emp_code": first_nonblank(r["team_emp_code"] for r in group_rows),
            "team_emp_name": first_nonblank(r["team_emp_name"] for r in group_rows),
            "team_emp_designation": first_nonblank(r["team_emp_designation"] for r in group_rows),
            "joint_days": sum(r["joint_days"] for r in group_rows),
        })
    return merged


def compute_retained_month_keys(all_month_keys, window_size: int = ROLLING_WINDOW_SIZE) -> set:
    """Given every distinct month_sort_key currently known (existing
    stored months UNION the new upload's own months), returns the set of
    keys to KEEP -- the newest `window_size` (default 6), purely by month
    VALUE. Anything not in the returned set is meant to be discarded from
    storage -- see `sync_rolling_window`."""
    ordered = sorted({k for k in all_month_keys if k is not None}, reverse=True)
    return set(ordered[:window_size])


def sync_rolling_window(
    session, model_cls, source_engine: str, new_monthly_records: list, now, window_size: int = ROLLING_WINDOW_SIZE,
) -> list:
    """The ONE place either engine ever writes to ManagerWorkAllocationRecord.

    1. UPSERTS each of `new_monthly_records` (see `merge_same_month_duplicates`'s
       own return shape) -- a (pair, month) already stored gets its
       joint_days/descriptive fields corrected in place; a new one is
       inserted. Re-uploading the same month's data again is therefore
       idempotent, not a duplicate.
    2. Recomputes the FULL set of distinct months now stored for this
       `source_engine`, and trims storage to the newest `window_size`
       (see `compute_retained_month_keys`) -- discarding anything older,
       regardless of which upload it came from or when.
    3. Returns EVERY retained row for this engine, ordered by
       month_sort_key ascending (oldest-to-newest) -- ready for the
       caller's own per-pair grouping/evaluation.

    Does not commit -- the caller controls the transaction boundary, same
    convention as every other *_service.py write in this app."""
    for record in new_monthly_records:
        existing = session.query(model_cls).filter_by(
            source_engine=source_engine, emp_code=record["emp_code"], team_emp_code=record["team_emp_code"],
            month_sort_key=record["month_sort_key"],
        ).first()
        if existing is not None:
            existing.joint_days = record["joint_days"]
            existing.division = record["division"] or existing.division
            existing.emp_name = record["emp_name"] or existing.emp_name
            existing.emp_designation = record["emp_designation"] or existing.emp_designation
            existing.month = record["month"]
            existing.team_emp_name = record["team_emp_name"] or existing.team_emp_name
            existing.team_emp_designation = record["team_emp_designation"] or existing.team_emp_designation
            existing.last_updated = now
        else:
            session.add(model_cls(
                source_engine=source_engine,
                division=record["division"] or None,
                emp_code=record["emp_code"] or None,
                emp_name=record["emp_name"],
                emp_designation=record["emp_designation"] or None,
                month=record["month"],
                month_sort_key=record["month_sort_key"],
                team_emp_code=record["team_emp_code"] or None,
                team_emp_name=record["team_emp_name"],
                team_emp_designation=record["team_emp_designation"] or None,
                joint_days=record["joint_days"],
                last_updated=now,
            ))
    session.flush()

    all_keys = [
        row[0] for row in session.query(model_cls.month_sort_key).filter(
            model_cls.source_engine == source_engine
        ).distinct().all()
    ]
    retained_keys = compute_retained_month_keys(all_keys, window_size)
    if retained_keys:
        session.query(model_cls).filter(
            model_cls.source_engine == source_engine,
            ~model_cls.month_sort_key.in_(retained_keys),
        ).delete(synchronize_session=False)

    return session.query(model_cls).filter(
        model_cls.source_engine == source_engine
    ).order_by(model_cls.month_sort_key.asc()).all()


def get_retained_month_range_label(session, model_cls, source_engine: str) -> str | None:
    """Human-readable "oldest – newest" label for whichever months are
    CURRENTLY retained in this engine's own rolling window (e.g.
    "Feb 2026 – Jul 2026") -- read straight from the persisted store, so it
    always reflects exactly what the last Run Analysis retained, never
    independently recomputed. A single-month window renders as just that
    one month ("Feb 2026"), no dash. None if this engine has no records at
    all yet (nothing uploaded/analyzed)."""
    rows = session.query(model_cls.month_sort_key, model_cls.month).filter(
        model_cls.source_engine == source_engine
    ).distinct().all()
    if not rows:
        return None
    ordered = sorted(rows, key=lambda r: r[0])
    oldest_label = ordered[0][1].replace("-", " ")
    newest_label = ordered[-1][1].replace("-", " ")
    return oldest_label if oldest_label == newest_label else f"{oldest_label} – {newest_label}"
