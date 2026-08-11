"""Region Suppression Rule -- an additional, independent business rule, not
a replacement for Hospital Suppression (see app/hospital_service.py).

Applies only to notifications: a flagged finding whose employee's Region is
on SUPPRESSED_REGIONS still went through the complete analysis (clustering,
threshold calculation) and still appears in the Findings page and Dashboard
statistics exactly as flagged -- only the automatic RBM/master email is
withheld. See app/notification_service.py's build_email_batch for where
this is applied: as of v1.3 it runs FIRST, before Hospital Suppression, so
a region-suppressed finding never incurs a hospital lookup.
"""

SUPPRESSED_REGIONS = {"kerala", "punjab"}


def _base_region(region: str) -> str:
    """The base region name, with any ' - Zone/City' suffix the source data
    uses dropped, normalized to lower-case. The suffix is separated by a
    hyphen (with or without surrounding spaces), matching the real region
    naming convention -- 'Gujarat - AHM', 'MP CG - CG', 'UP East - G/L',
    'Gujarat -Surat'. So 'Kerala - KOC' -> 'kerala', 'Punjab - LDH' ->
    'punjab', 'Gujarat - AHM' -> 'gujarat'. Only the part BEFORE the first
    hyphen is used, so this can never match an unrelated value that merely
    contains the word (e.g. 'Punjabi Bagh' -> 'punjabi bagh', 'West Punjab'
    -> 'west punjab' -- neither is a base region)."""
    return region.split("-", 1)[0].strip().lower()


def is_region_suppressed(region: str | None) -> bool:
    """True when a region's BASE name (before any ' - Zone' suffix) is on
    SUPPRESSED_REGIONS. Case/whitespace-insensitive and suffix-aware, so
    'Kerala', 'Kerala - KOC', ' kerala - KOC ', 'Punjab', and 'Punjab - LDH'
    all suppress, while 'Karnataka', 'Punjabi Bagh', and 'West Punjab' do
    not. This is the ONE canonical region-suppression check -- every caller
    (email, Master, HR, Location tab) goes through it."""
    return bool(region) and _base_region(region) in SUPPRESSED_REGIONS


def format_suppression_reason(region: str) -> str:
    return f"Employee's region '{region}' is subject to the Region Suppression Rule."
