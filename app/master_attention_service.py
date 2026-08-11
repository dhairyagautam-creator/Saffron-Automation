"""Founder-facing "who needs attention right now, and why?" aggregation for
the Path Validator Master page.

This is a read-only analytics layer: it consolidates the CURRENT dataset's
investigation_findings at the EMPLOYEE level -- one record per employee, with
every applicable finding type combined -- WITHOUT importing or re-running any
detector, and without touching the existing Findings pages, email pipeline,
or hierarchy/recipient logic. It only reads data those already produced.

Data-source seam (the important part for the future):

    Current dataset  -> get_all_findings(active import) -\
                                                          >-- build_attention_records() -> [EmployeeAttentionRecord] -> Master page
    Historical snap. -> snapshot's findings ------------/

`build_attention_records(findings)` is a pure function over a list of
InvestigationFinding-shaped rows plus a hierarchy resolver. Today
`get_current_attention_records()` feeds it the active session's findings; a
future Historical Data Uploads feature feeds it a snapshot's findings instead
-- the aggregation, severity, and the Master page that consumes these records
do not change.

Extensibility: adding a future finding type (Low Call Count, Repeat Offender)
is one entry in FINDING_TYPE_REGISTRY below -- an evidence/summary builder for
that rule_name. Nothing in the aggregation, severity, model, or Master page
needs to be rewritten; an unregistered rule is simply skipped, never crashes.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from loguru import logger

from app.findings_service import get_all_findings, parse_hours_worked_message
from app.hierarchy_parser import find_by_employee_code, find_by_employee_name
from app.session_state import get_active_import_id
from app.suppression_service import filter_actionable


class FindingType:
    """Applicable finding-type identifiers surfaced on the Master page.
    LOCATION and LOW_WORKING_HOURS are produced today; the other two are
    reserved names so the model/page already knows about them -- a future
    detector only needs a FINDING_TYPE_REGISTRY entry, nothing here moves."""

    LOCATION = "LOCATION"
    LOW_WORKING_HOURS = "LOW_WORKING_HOURS"
    LOW_CALL_COUNT = "LOW_CALL_COUNT"        # future -- not implemented now
    REPEAT_OFFENDER = "REPEAT_OFFENDER"      # future -- not implemented now


class Severity:
    CRITICAL = "Critical"
    ATTENTION = "Attention"
    WATCH = "Watch"


# Sort rank -- Critical first, then Attention, then Watch. Centralized here so
# the Master page sorts by SEVERITY_ORDER[record.severity] and never encodes
# the ordering itself.
SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.ATTENTION: 1, Severity.WATCH: 2}


@dataclass
class ApplicableFinding:
    """One finding TYPE that applies to an employee (all their findings of
    that type folded together)."""

    finding_type: str
    label: str
    count: int          # how many findings of this type (e.g. flagged days)
    summary: str        # short human label, e.g. "Location (2 days)"
    evidence: dict      # structured detail, reusing the detectors' own values


@dataclass
class EmployeeAttentionRecord:
    """One employee, appearing ONCE, with every applicable finding type
    combined -- the unit the Master page renders and ranks."""

    employee_code: str
    employee_name: str
    designation: str | None
    rbm_name: str | None
    applicable_findings: list[ApplicableFinding]
    severity: str
    summary: str
    evidence: dict = field(default_factory=dict)  # {finding_type: evidence dict}


# --- Per-rule evidence/summary builders (the extension point) --------------
#
# Each takes the list of ONE employee's findings for that rule and returns
# (evidence dict, short summary). They reuse the values the detectors already
# wrote -- location's structured columns, and the Low Working Hours message
# via the shared app.findings_service parser -- and compute nothing new.


def _location_evidence(findings: list) -> dict:
    return {
        "occurrences": [
            {
                "visit_date": f.visit_date,
                "concentration_percent": f.concentration_percent,
                "matched_visit_count": f.matched_visit_count,
                "valid_visit_count": f.valid_visit_count,
                "radius_meters": f.radius_meters,
                "threshold_percent": f.threshold_percent,
                "message": f.message,
            }
            for f in findings
        ]
    }


def _location_summary(findings: list) -> str:
    n = len(findings)
    return f"Location ({n} day{'s' if n != 1 else ''})"


def _hours_evidence(findings: list) -> dict:
    occurrences = []
    for f in findings:
        parsed = parse_hours_worked_message(f.message) or {}
        occurrences.append({"visit_date": f.visit_date, "message": f.message, **parsed})
    return {"occurrences": occurrences}


def _hours_summary(findings: list) -> str:
    return "Low Working Hours"


@dataclass(frozen=True)
class _FindingTypeSpec:
    finding_type: str
    label: str
    order: int
    evidence_fn: Callable[[list], dict]
    summary_fn: Callable[[list], str]


# rule_name (as stored on InvestigationFinding) -> how the Master page treats
# it. Add LOW_CALL_COUNT / REPEAT_OFFENDER here when their detectors exist.
FINDING_TYPE_REGISTRY: dict[str, _FindingTypeSpec] = {
    "SAME_LOCATION": _FindingTypeSpec(
        FindingType.LOCATION, "Location", 0, _location_evidence, _location_summary
    ),
    "HOURS_WORKED": _FindingTypeSpec(
        FindingType.LOW_WORKING_HOURS, "Low Working Hours", 1, _hours_evidence, _hours_summary
    ),
}

# finding_type -> display/sort order, derived once from the registry so each
# record's applicable_findings list is always in a stable, predictable order.
FINDING_TYPE_REGISTRY_ORDER = {spec.finding_type: spec.order for spec in FINDING_TYPE_REGISTRY.values()}


def compute_severity(applicable_findings: list[ApplicableFinding]) -> str:
    """The one place employee severity is decided -- deliberately simple, no
    scoring math, uses only counts already present in the data:

        Critical  -> more than one finding TYPE (several different problems)
        Attention -> a single type, flagged more than once (a repeated problem)
        Watch     -> a single type, flagged once (a one-off / borderline)

    (There was no existing per-employee attention level to reuse, so this is a
    new, centralized rule. Swap the body here to evolve ranking later; callers
    never change.)
    """
    if not applicable_findings:
        return Severity.WATCH
    if len(applicable_findings) >= 2:
        return Severity.CRITICAL
    total = sum(af.count for af in applicable_findings)
    return Severity.ATTENTION if total > 1 else Severity.WATCH


def _default_resolve_hierarchy(employee_code: str, employee_name: str) -> tuple[str | None, str | None]:
    """(designation, rbm_name) from Organization Data. Reuses the PRECOMPUTED
    `senior_name` field (app.hierarchy_service.compute_seniors) -- the exact
    RBM the email pipeline routes a BM/ABM to -- so the Master page and the
    emails can never disagree. Mirrors
    notification_service._resolve_employee_hierarchy_row's code-then-name
    lookup order. Returns (None, None) when Organization Data has no match, and
    (designation, None) when the resolved senior is Top Level / vacant."""
    row = find_by_employee_code(employee_code)
    if row is None:
        matches = find_by_employee_name(employee_name)
        row = matches[0] if matches else None
    if row is None:
        return None, None
    designation = row.get("designation")
    rbm_name = row.get("senior_name")
    if rbm_name == "Top Level" or not rbm_name:
        rbm_name = None
    return designation, rbm_name


def _record_summary(applicable_findings: list[ApplicableFinding]) -> str:
    """'Location (2 days) + Low Working Hours' -- built around the employee,
    listing each applicable type once, in registry order."""
    return " + ".join(af.summary for af in applicable_findings)


def build_attention_records(
    findings: list,
    resolve_hierarchy: Callable[[str, str], tuple[str | None, str | None]] | None = None,
) -> list[EmployeeAttentionRecord]:
    """Pure aggregation: group `findings` by employee, fold each employee's
    findings into one EmployeeAttentionRecord with all applicable types, a
    severity, a summary, and evidence. Independent of where `findings` came
    from (current dataset or a future historical snapshot) and of how
    hierarchy is resolved (`resolve_hierarchy` is injectable for testing;
    defaults to Organization Data). Records are returned sorted Critical ->
    Attention -> Watch, then by employee name."""
    resolve = resolve_hierarchy or _default_resolve_hierarchy

    # employee_code -> {"name": str, "by_rule": {rule_name: [findings]}}
    by_employee: dict[str, dict] = {}
    for finding in findings:
        spec = FINDING_TYPE_REGISTRY.get(finding.rule_name)
        if spec is None:
            # A rule with no Master-page spec yet -- skip it rather than crash,
            # so a newly added detector can't break this page before its spec
            # is registered.
            logger.debug(f"Master attention: no spec for rule '{finding.rule_name}', skipping finding")
            continue
        emp = by_employee.setdefault(finding.employee_code, {"name": finding.employee_name, "by_rule": {}})
        emp["by_rule"].setdefault(finding.rule_name, []).append(finding)

    records: list[EmployeeAttentionRecord] = []
    for employee_code, data in by_employee.items():
        applicable: list[ApplicableFinding] = []
        evidence_map: dict = {}
        for rule_name, rule_findings in data["by_rule"].items():
            spec = FINDING_TYPE_REGISTRY[rule_name]
            evidence = spec.evidence_fn(rule_findings)
            applicable.append(
                ApplicableFinding(
                    finding_type=spec.finding_type,
                    label=spec.label,
                    count=len(rule_findings),
                    summary=spec.summary_fn(rule_findings),
                    evidence=evidence,
                )
            )
            evidence_map[spec.finding_type] = evidence

        applicable.sort(key=lambda af: FINDING_TYPE_REGISTRY_ORDER.get(af.finding_type, 99))
        designation, rbm_name = resolve(employee_code, data["name"])
        severity = compute_severity(applicable)

        records.append(
            EmployeeAttentionRecord(
                employee_code=employee_code,
                employee_name=data["name"],
                designation=designation,
                rbm_name=rbm_name,
                applicable_findings=applicable,
                severity=severity,
                summary=_record_summary(applicable),
                evidence=evidence_map,
            )
        )

    records.sort(key=lambda r: (SEVERITY_ORDER.get(r.severity, 99), r.employee_name or ""))
    return records


def get_current_attention_records(statuses: tuple[str, ...] = ("Open",)) -> list[EmployeeAttentionRecord]:
    """The Master page's data source TODAY: the active-session findings,
    filtered to `statuses` (Open by default -- an actioned Reviewed/Ignored
    finding no longer "needs attention") AND to actionable findings only --
    region/hospital-suppressed findings are dropped here, at the business
    layer (via app.findings_service.is_finding_suppressed, the canonical
    rule), so suppressed employees never reach aggregation, KPIs, the table,
    or drill-down. Empty list when there's no active session. To switch to
    historical data later, call build_attention_records() with a snapshot's
    findings instead -- this function is the only thing that binds the
    aggregation to "current dataset"."""
    import_id = get_active_import_id()
    findings = [f for f in get_all_findings(import_id) if f.status in statuses]
    findings = filter_actionable(findings, import_id)  # canonical region + hospital suppression
    return build_attention_records(findings)
