"""Tests for the centralized hierarchy fallback rule
(app.hierarchy_service) and its integration with the Organization Data
parser (app.hierarchy_parser). Pure in-memory rows/grids -- no database or
Excel file needed, since compute_seniors()/resolve_senior_from_maps() are
DB-free by design (see app/hierarchy_service.py).

The email notification system (app.notification_service) is not
independently re-tested here for the general routing rule: it reads the
exact same senior_name/senior_email columns these tests verify, computed
by the exact same function (compute_seniors), so there is no second
implementation whose correctness could diverge -- see
app/notification_service.py's build_email_batch and its module docstring.
The Xandra HQ override (a separate, isolated business rule that bypasses
this module's routing entirely) is tested directly against
app.notification_service in test_notification_routing.py instead, since it
needs a real employee_hierarchy table (it looks a recipient up by name via
app.hierarchy_parser.find_by_employee_name, not the in-memory maps here).

REDESIGNED 2026-07-28 alongside app/hierarchy_service.py: BM must escalate
straight to RBM, never ABM (restoring the Version 1.0 rule) -- see
test_bm_never_routes_to_abm_even_when_abm_is_valid below, the direct
regression test for that bug.

SIMPLIFIED FURTHER the same day, per explicit follow-up instruction:
"ignore the fallback system for now" -- BM and ABM now route ONLY to
their own RBM, with NO further fallback to Senior RBM/SM/AGM/GM at all.
If that RBM is vacant/missing/null/no-email, the result is a blank
Senior (Unresolved), never an escalation further up. See
test_bm_with_rbm_vacant_has_no_senior_no_fallback and
test_abm_with_rbm_vacant_has_no_senior_no_fallback below for the direct
regression tests. RBM/SRRBM/SM/AGM's own escalation (irrelevant to actual
email routing, since only BM/ABM are ever flagged findings -- see
rules/same_location.py's ANALYZABLE_DESIGNATIONS) is UNCHANGED and still
tested further down this file.
"""

from app.hierarchy_service import compute_seniors, fallback_chain_for
from app.hierarchy_parser import _parse_sheet


def test_fallback_chain_for_bm_is_rbm_only_no_fallback():
    """The routing model itself, tested directly: BM's fixed chain is
    exactly ["RBM"] -- no ABM (restored Version 1.0 rule), and no
    SRRBM/SM/AGM/GM fallback either (2026-07-28 simplification)."""
    assert fallback_chain_for("BM") == ["RBM"]


def test_fallback_chain_for_abm_is_rbm_only_no_fallback():
    """Same simplification applies to ABM: exactly ["RBM"], no further
    fallback."""
    assert fallback_chain_for("ABM") == ["RBM"]


def test_fallback_chain_for_other_designations_unchanged():
    """RBM/SRRBM/SM/AGM/GM's own escalation is untouched by the BM/ABM
    simplification -- verified directly against the routing model. (Never
    consulted for actual email routing, since only BM/ABM are ever
    flagged findings -- this only matters for the Organization Data
    table's Senior column on those rows.)"""
    assert fallback_chain_for("RBM") == ["SRRBM", "SM", "AGM", "GM"]
    assert fallback_chain_for("SRRBM") == ["SM", "AGM", "GM"]
    assert fallback_chain_for("SM") == ["AGM", "GM"]
    assert fallback_chain_for("AGM") == ["GM"]
    assert fallback_chain_for("GM") == []


def test_fallback_chain_for_unrecognized_designation_defaults_safely():
    """A blank/unrecognized designation falls back to the safest chain
    (BM's own, currently just RBM)."""
    assert fallback_chain_for(None) == ["RBM"]
    assert fallback_chain_for("") == ["RBM"]
    assert fallback_chain_for("SOMETHING_UNKNOWN") == ["RBM"]


def _row(code, name, designation, division="Onyx", sheet="Zone A", email=None, abm=None, rbm=None):
    """Build a minimal hierarchy row dict, as app.hierarchy_parser would
    produce it (before senior_* columns are added by compute_seniors)."""
    abm = abm or (None, None)
    rbm = rbm or (None, None)
    return {
        "employee_code": code,
        "employee_name": name,
        "designation": designation,
        "mobile": None,
        "email": email if email is not None else f"{code}@example.com",
        "abm_code": abm[0],
        "abm_name": abm[1],
        "rbm_code": rbm[0],
        "rbm_name": rbm[1],
        "division": division,
        "source_sheet": sheet,
    }


def test_bm_routes_to_rbm_when_rbm_exists():
    """Validation scenario: BM with a valid RBM -> Email goes to RBM.
    No ABM involved at all here."""
    rows = [
        _row("R1", "Rita RBM", "RBM"),
        _row("B1", "Bilal BM", "BM", rbm=("R1", "Rita RBM")),
    ]
    compute_seniors(rows)
    bm = next(r for r in rows if r["employee_code"] == "B1")
    assert bm["senior_name"] == "Rita RBM"
    assert bm["senior_email"] == "R1@example.com"


def test_abm_routes_to_rbm_when_rbm_exists():
    """Validation scenario: ABM with a valid RBM -> Email goes to RBM.
    Same simplified rule as BM -- ABM's own escalation now also stops at
    RBM, no further fallback."""
    rows = [
        _row("R1", "Rita RBM", "RBM"),
        _row("A1", "Amit ABM", "ABM", rbm=("R1", "Rita RBM")),
    ]
    compute_seniors(rows)
    abm = next(r for r in rows if r["employee_code"] == "A1")
    assert abm["senior_name"] == "Rita RBM"
    assert abm["senior_email"] == "R1@example.com"


def test_bm_never_routes_to_abm_even_when_abm_is_valid():
    """THE core regression test for the reported bug: a BM with BOTH a
    valid, available ABM (name + email on file) AND a valid RBM must
    still route to the RBM -- the ABM must never be consulted at all for
    a BM's escalation, regardless of whether it's available. Before the
    2026-07-28 redesign, app.hierarchy_service.LEVELS/levels_above() tried
    ABM first for a BM and would have returned Amit ABM here instead."""
    rows = [
        _row("A1", "Amit ABM", "ABM"),
        _row("R1", "Rita RBM", "RBM"),
        _row("B1", "Bilal BM", "BM", abm=("A1", "Amit ABM"), rbm=("R1", "Rita RBM")),
    ]
    compute_seniors(rows)
    bm = next(r for r in rows if r["employee_code"] == "B1")
    assert bm["senior_name"] == "Rita RBM"
    assert bm["senior_name"] != "Amit ABM"


def test_bm_with_only_abm_on_file_has_no_senior_abm_is_never_tried():
    """If the ONLY thing above a BM is an ABM (no RBM/SRRBM/SM/AGM/GM
    anywhere), the BM must NOT resolve to that ABM -- ABM is simply not in
    BM's routing sequence at all, so this is a genuine data gap (blank),
    not "fall back to the ABM since nothing else exists"."""
    rows = [
        _row("A1", "Amit ABM", "ABM"),
        _row("B1", "Bilal BM", "BM", abm=("A1", "Amit ABM")),
    ]
    compute_seniors(rows)
    bm = next(r for r in rows if r["employee_code"] == "B1")
    assert bm["senior_name"] == ""
    assert bm["senior_email"] == ""


def test_bm_with_rbm_pointing_at_nonexistent_employee_has_no_senior():
    """BM's own rbm_code/rbm_name point at a code, but that employee
    simply doesn't exist in the hierarchy (e.g. vacant at parse time, so
    never made it into `rows` at all). 2026-07-28 simplification: this
    must NOT fall through to Senior RBM (or anyone else) -- a blank
    Senior is the correct, deliberate outcome now."""
    rows = [
        _row("S1", "Sunita SRRBM", "SRRBM"),
        _row("B1", "Bilal BM", "BM", rbm=("R1", "Rita RBM")),
    ]
    compute_seniors(rows)
    bm = next(r for r in rows if r["employee_code"] == "B1")
    assert bm["senior_name"] == ""
    assert bm["senior_email"] == ""


def test_bm_with_rbm_vacant_has_no_senior_no_fallback():
    """Validation scenario (2026-07-28 simplified rule): RBM vacant ->
    NO ONE else is tried. RBM is on file as a row but has no email (the
    "vacant/unavailable" signal is_valid_recipient checks) -- must NOT
    fall through to Senior RBM, SM, AGM, or GM, even though all four
    exist and are valid in this test's data."""
    rows = [
        _row("G1", "Gita GM", "GM"),
        _row("AG1", "Anil AGM", "AGM"),
        _row("S1", "Sunita SRRBM", "SRRBM"),
        _row("M1", "Manoj SM", "SM"),
        _row("R1", "Rita RBM", "RBM", email=""),
        _row("B1", "Bilal BM", "BM", rbm=("R1", "Rita RBM")),
    ]
    compute_seniors(rows)
    bm = next(r for r in rows if r["employee_code"] == "B1")
    assert bm["senior_name"] == ""
    assert bm["senior_email"] == ""


def test_abm_with_rbm_vacant_has_no_senior_no_fallback():
    """Same simplified rule applied to ABM: RBM vacant -> no fallback to
    Senior RBM/SM/AGM/GM, even though all exist and are valid here."""
    rows = [
        _row("G1", "Gita GM", "GM"),
        _row("AG1", "Anil AGM", "AGM"),
        _row("S1", "Sunita SRRBM", "SRRBM"),
        _row("M1", "Manoj SM", "SM"),
        _row("R1", "Rita RBM", "RBM", email=""),
        _row("A1", "Amit ABM", "ABM", rbm=("R1", "Rita RBM")),
    ]
    compute_seniors(rows)
    abm = next(r for r in rows if r["employee_code"] == "A1")
    assert abm["senior_name"] == ""
    assert abm["senior_email"] == ""


def test_bm_with_no_rbm_at_all_has_no_senior_gm_is_not_used_as_fallback():
    """A BM with no RBM assigned at all, but a GM present in the same
    zone -- under the OLD (pre-2026-07-28) fallback design this would
    have resolved all the way up to the GM. Under the simplified rule,
    GM must NOT be used as a fallback for BM at all."""
    rows = [
        _row("G1", "Gita GM", "GM"),
        _row("B1", "Bilal BM", "BM"),
    ]
    compute_seniors(rows)
    bm = next(r for r in rows if r["employee_code"] == "B1")
    assert bm["senior_name"] == ""
    assert bm["senior_email"] == ""


def test_employee_already_rbm_reports_to_srrbm():
    rows = [
        _row("S1", "Sunita SRRBM", "SRRBM"),
        _row("R1", "Rita RBM", "RBM"),
    ]
    compute_seniors(rows)
    rbm = next(r for r in rows if r["employee_code"] == "R1")
    assert rbm["senior_name"] == "Sunita SRRBM"


def test_employee_already_srrbm_reports_to_sm():
    rows = [
        _row("M1", "Manoj SM", "SM"),
        _row("S1", "Sunita SRRBM", "SRRBM"),
    ]
    compute_seniors(rows)
    srrbm = next(r for r in rows if r["employee_code"] == "S1")
    assert srrbm["senior_name"] == "Manoj SM"


def test_employee_already_sm_reports_to_agm():
    rows = [
        _row("AG1", "Anil AGM", "AGM"),
        _row("M1", "Manoj SM", "SM"),
    ]
    compute_seniors(rows)
    sm = next(r for r in rows if r["employee_code"] == "M1")
    assert sm["senior_name"] == "Anil AGM"


def test_employee_already_agm_reports_to_gm():
    rows = [
        _row("G1", "Gita GM", "GM"),
        _row("AG1", "Anil AGM", "AGM"),
    ]
    compute_seniors(rows)
    agm = next(r for r in rows if r["employee_code"] == "AG1")
    assert agm["senior_name"] == "Gita GM"


def test_gm_has_no_senior_shows_top_level_never_blank_or_nan():
    rows = [_row("G1", "Gita GM", "GM")]
    compute_seniors(rows)
    gm = rows[0]
    assert gm["senior_name"] == "Top Level"
    assert gm["senior_email"] == ""
    # Never None -- a None here is exactly what turns into a literal "NaN"
    # once written to SQLite and read back via pandas.read_sql_table.
    assert gm["senior_name"] is not None
    assert gm["senior_email"] is not None


def test_no_valid_senior_anywhere_is_blank_not_none_or_nan():
    # A BM whose entire chain above them has no email on file anywhere --
    # a genuine data gap (unlike a GM's "Top Level", which is expected).
    rows = [
        _row("R1", "Rita RBM", "RBM", email=""),
        _row("B1", "Bilal BM", "BM", rbm=("R1", "Rita RBM")),
    ]
    compute_seniors(rows)
    bm = next(r for r in rows if r["employee_code"] == "B1")
    assert bm["senior_name"] == ""
    assert bm["senior_email"] == ""
    assert bm["senior_name"] is not None


def test_rung_with_no_email_is_skipped_like_vacant():
    """A rung that's ON FILE (a real row exists) but has no email must be
    skipped exactly like a vacant/missing one -- exercised here via RBM's
    OWN escalation (RBM -> Senior RBM), since RBM's chain is unaffected by
    the BM/ABM-specific simplification (only BM/ABM were narrowed to
    RBM-only; RBM's own chain above it is untouched)."""
    rows = [
        _row("S1", "Sunita SRRBM", "SRRBM"),
        _row("R1", "Rita RBM", "RBM", email=""),  # on file, but no email
    ]
    compute_seniors(rows)
    rbm = next(r for r in rows if r["employee_code"] == "R1")
    assert rbm["senior_name"] == "Sunita SRRBM"


def test_srrbm_sm_agm_gm_are_scoped_by_division_and_sheet():
    """Two zones, each with their own SM -- an RBM in Zone A must never
    resolve to Zone B's SM. Tested via RBM's own escalation (RBM ->
    Senior RBM -> SM -> AGM -> GM), which is unaffected by the BM/ABM
    -specific 2026-07-28 simplification (BM/ABM no longer reach SM, or
    any division/sheet-scoped lookup, at all -- their chain is RBM
    only)."""
    rows = [
        _row("M_A", "Manoj SM (Zone A)", "SM", division="Onyx", sheet="Zone A"),
        _row("M_B", "Meera SM (Zone B)", "SM", division="Onyx", sheet="Zone B"),
        _row("R1", "Rita RBM", "RBM", division="Onyx", sheet="Zone A"),
    ]
    compute_seniors(rows)
    rbm = next(r for r in rows if r["employee_code"] == "R1")
    assert rbm["senior_name"] == "Manoj SM (Zone A)"


# --- Parser-level: the vacant-reset bug fix ---------------------------------


def _grid(rows):
    header = ["Emp Code", "Name", "Designation", "Mobile", "Email-Id"]
    return [header] + rows


def test_vacant_abm_row_resets_current_abm_for_parser():
    """A BM listed under a vacant ABM must come out with no abm_code/name
    at all -- not silently inherit the PREVIOUS ABM's identity from an
    earlier section under the same RBM. This is the root-cause fix in
    app.hierarchy_parser._parse_sheet: vacant rows used to leave the
    current-ABM/current-RBM tracker untouched instead of resetting it."""
    grid = _grid(
        [
            ["R1", "Rita RBM", "RBM", None, "r1@example.com"],
            ["A1", "Amit ABM", "ABM", None, "a1@example.com"],
            ["B1", "Bilal BM (under Amit)", "BM", None, "b1@example.com"],
            ["A2", "Vacant", "ABM", None, None],
            ["B2", "Bashir BM Under Open Slot", "BM", None, "b2@example.com"],
        ]
    )
    records, stats = _parse_sheet(grid, "Onyx", "Sheet1")
    by_code = {r["employee_code"]: r for r in records}

    assert by_code["B1"]["abm_code"] == "A1"
    # The BM after the vacant ABM row must have NO abm_code -- it must not
    # inherit A1 (the previous section's ABM) just because current_abm was
    # never reset.
    assert by_code["B2"]["abm_code"] is None
    assert by_code["B2"]["rbm_code"] == "R1"
    assert stats["vacant_ignored"] == 1


def test_vacant_abm_then_compute_seniors_falls_through_to_rbm():
    """End-to-end: a BM under a vacant ABM resolves straight to RBM -- true
    both because the ABM slot is vacant AND because BM's fixed chain
    (app.hierarchy_service.FALLBACK_CHAINS["BM"]) never tries ABM at all,
    per the restored Version 1.0 rule."""
    grid = _grid(
        [
            ["R1", "Rita RBM", "RBM", None, "r1@example.com"],
            ["A1", "Vacant", "ABM", None, None],
            ["B1", "Bashir BM", "BM", None, "b1@example.com"],
        ]
    )
    records, _ = _parse_sheet(grid, "Onyx", "Sheet1")
    compute_seniors(records)
    bm = next(r for r in records if r["employee_code"] == "B1")
    assert bm["abm_code"] is None
    assert bm["senior_name"] == "Rita RBM"
