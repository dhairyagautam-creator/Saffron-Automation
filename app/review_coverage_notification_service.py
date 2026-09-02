"""Coverage Summary's automated management notification email system --
builds one consolidated email per ABM, attaching every BM Coverage
Summary file (see app.review_coverage_service.generate_coverage_summary_bm_files)
for the BM(s) reporting to that ABM. Reuses the existing shared
infrastructure throughout, per explicit instruction, rather than building
a new email framework -- see app/notification_service.py (Path Validator)
for the reference architecture this mirrors:
- app/smtp_service.py for the actual SMTP send (the SAME shared sending
  module every other automated-email workflow in this app uses --
  extended, not duplicated).
- app/email_settings_service.py for the Gmail sender credentials -- the
  SAME single account used by the rest of the application's email
  functionality (Path Validator), never a second, independently-
  configured one (see app/review_coverage_email_settings_service.py,
  which owns only this workflow's own automatic-sending trigger flag).
- app/hierarchy_parser.py / app/hierarchy_service.py for hierarchy
  lookups (find_by_employee_code/find_by_employee_name, is_valid_recipient)
  -- there is exactly one hierarchy dataset in the application; Review
  System has no separate hierarchy of its own (see
  app/review_coverage_service.py's own docstring: BM identity is
  Employee Code, matched against employee_hierarchy the same way).
- app/review_coverage_email_template.py for HTML/text rendering (this
  workflow's own presentation layer).

BUSINESS RULE (genuinely Coverage-Summary-specific, not shared with Path
Validator, RGD Coverage, or Manager Work Allocation):

  One BM = one Coverage Summary .xlsx file (see
  app.review_coverage_service.generate_coverage_summary_bm_files).
  One ABM = one consolidated email, with every one of THEIR BM's own
  file attached -- never one email per BM, never BMs from different ABMs
  combined into the same email, never a file attached twice (enforced by
  grouping strictly on the resolved ABM's own email address as the group
  key; each BM's file is appended to exactly one group).

RECIPIENT RESOLUTION: for each BM, read that BM's OWN hierarchy row (by
Employee Code, falling back to name only if the code isn't found -- same
first-match convention as app.hierarchy_parser.find_by_employee_name's
own documented "may return more than one row" contract) and its
abm_code/abm_name fields (populated directly on every BM row by
app.hierarchy_parser -- one of the two rungs carried as a DIRECT
assignment, not a fallback chain -- see app.hierarchy_service's own
DIRECTLY_ASSIGNED_LEVELS). The resolved ABM row must pass
is_valid_recipient() (name + email both on file) to receive an email --
a vacant/missing/no-email ABM, or a BM with no hierarchy row at all
(itself vacant, or simply not found), is logged and left out of every
email rather than guessed at; no automatic escalation past the BM's own
direct ABM (Coverage Summary has no RBM/SM/AGM/GM fallback concept --
this is a one-rung lookup, not Path Validator's own FALLBACK_CHAINS
walk).

Does NOT modify Coverage Summary's own calculations, formatting, or
report structure -- every function here only reads already-generated
per-BM files and resolves/sends; no BM's own numbers are ever recomputed
here.
"""

from datetime import datetime
from pathlib import Path

from loguru import logger

from app.email_settings_service import get_settings
from app.hierarchy_parser import find_by_employee_code, find_by_employee_name
from app.hierarchy_service import is_valid_recipient
from app.review_coverage_email_template import render_html, render_text
from app.review_coverage_service import generate_coverage_summary_bm_files
from app.smtp_service import open_smtp_connection, send_via_connection
from database.connection import get_config_session
from database.models import ReviewCoverageEmailNotification

STATUS_DRAFT = "Draft"
STATUS_SENT = "Sent"
STATUS_FAILED = "Failed"

# Same rationale as app.work_distribution_notification_service.COMMIT_EVERY_N_EMAILS
# -- batched commits so a disk fsync isn't paid per email, but the send log
# still fills in during the run rather than only appearing once the whole
# batch is over.
COMMIT_EVERY_N_EMAILS = 3


# --- Hierarchy resolution ------------------------------------------------

def _resolve_bm_hierarchy_row(emp_code: str, name: str) -> dict | None:
    """Code-first, name-fallback lookup for the BM's OWN hierarchy row --
    mirrors app.doj_eligibility_service.resolve_doj's own code-first
    convention for the same reason: Employee Code is the reliable
    identity, name is a display-only fallback for whatever hierarchy rows
    lack a matching code."""
    row = find_by_employee_code(emp_code) if emp_code else None
    if row is not None:
        return row
    matches = find_by_employee_name(name) if name else []
    return matches[0] if matches else None


def _resolve_abm(bm_hierarchy_row: dict) -> dict | None:
    """The BM's own direct ABM (abm_code/abm_name, populated on every BM
    row by app.hierarchy_parser) -- one direct rung, no fallback beyond
    it (see module docstring)."""
    abm_code = bm_hierarchy_row.get("abm_code")
    abm_name = bm_hierarchy_row.get("abm_name")
    candidate = find_by_employee_code(abm_code) if abm_code else None
    if candidate is None and abm_name:
        matches = find_by_employee_name(abm_name)
        candidate = matches[0] if matches else None
    return candidate


# --- Batch build (routing + grouping) ------------------------------------

def build_notification_batch(division: str, bm_files: list | None = None) -> list:
    """Builds (but does not send) one consolidated draft per ABM, one
    attachment per BM reporting to them. `bm_files` defaults to a fresh
    call to generate_coverage_summary_bm_files(division) -- pass an
    already-built list (that function's own "files" key) to reuse files a
    caller already generated in the same run, or to test the grouping
    logic against fixed fixtures without touching disk.

    A BM whose hierarchy row can't be found, or whose resolved ABM is
    vacant/missing/no-email, is logged and left out of every email --
    never guessed at, never silently dropped without a trace.

    Returns a list of drafts: {recipient_name, recipient_email, division,
    bm_names, file_paths, subject, body, text_body, status}."""
    if bm_files is None:
        result = generate_coverage_summary_bm_files(division)
        if not result["success"]:
            logger.warning(
                f"Coverage Summary notification batch ({division}): file generation failed -- {result['errors']}"
            )
            return []
        bm_files = result["files"]

    groups: dict = {}
    unresolved_count = 0

    for bm in bm_files:
        hierarchy_row = _resolve_bm_hierarchy_row(bm["emp_code"], bm["name"])
        if hierarchy_row is None:
            unresolved_count += 1
            logger.warning(
                f"Coverage Summary notification ({division}): BM {bm['name']!r} ({bm['emp_code']!r}) "
                "not found in hierarchy. No email sent for this BM."
            )
            continue

        abm_row = _resolve_abm(hierarchy_row)
        if not is_valid_recipient(abm_row):
            unresolved_count += 1
            logger.warning(
                f"Coverage Summary notification ({division}): BM {bm['name']!r}'s ABM is vacant, "
                "missing, or has no email on file. No email sent for this BM."
            )
            continue

        email = abm_row["email"]
        group = groups.setdefault(email, {"name": abm_row["employee_name"], "bm_names": [], "file_paths": []})
        group["bm_names"].append(bm["name"])
        group["file_paths"].append(bm["file_path"])

    if unresolved_count:
        logger.warning(
            f"Coverage Summary notification batch ({division}): {unresolved_count} BM routing(s) "
            "could not be resolved -- see individual warnings above."
        )

    now_text = datetime.now().strftime("%d %b %Y, %I:%M %p")
    drafts = []
    for email, group in groups.items():
        bm_count = len(group["bm_names"])
        drafts.append({
            "recipient_name": group["name"],
            "recipient_email": email,
            "division": division,
            "bm_names": group["bm_names"],
            "file_paths": group["file_paths"],
            "subject": f"Saffron Automation - Coverage Summary ({division}, {bm_count} BM{'s' if bm_count != 1 else ''})",
            "body": render_html(group["name"], division, now_text, group["bm_names"]),
            "text_body": render_text(group["name"], division, now_text, group["bm_names"]),
            "status": STATUS_DRAFT,
        })
    return drafts


# --- Send ------------------------------------------------------------

def send_notification_batch(drafts: list, progress_callback=None) -> dict:
    """Sends every draft from build_notification_batch() over ONE shared,
    reused SMTP connection (app.smtp_service.open_smtp_connection/
    send_via_connection), exactly the same skeleton as
    app.work_distribution_notification_service.send_notification_batch:
    each draft sent inside its own try/except so one recipient's failure
    never stops the rest of the batch, one send-log row per attempt.

    Returns {'sent_count', 'failed_count', 'drafts'}."""
    progress_callback = progress_callback or (lambda stage, **kwargs: None)
    total = len(drafts)
    sent_count = 0
    failed_count = 0

    progress_callback("sending", label="Sending Coverage Summary notifications...", completed=0, total=total)

    connection = None
    sender_email = None
    connection_error = None
    if drafts:
        settings = get_settings()
        try:
            connection, sender_email = open_smtp_connection(settings["sender_email"], settings["app_password"])
        except Exception as exc:
            connection_error = exc

    session = get_config_session()
    try:
        for index, draft in enumerate(drafts, start=1):
            try:
                if connection_error is not None:
                    raise connection_error
                attachments = [(Path(fp).name, Path(fp).read_bytes()) for fp in draft["file_paths"]]
                send_via_connection(
                    connection, sender_email, draft["recipient_email"],
                    draft["subject"], draft["body"], draft.get("text_body"), attachments=attachments,
                )
            except Exception as exc:
                logger.error(f"Failed to send Coverage Summary notification to {draft['recipient_email']}: {exc}")
                draft["status"] = STATUS_FAILED
                session.add(ReviewCoverageEmailNotification(
                    division=draft["division"],
                    recipient_name=draft["recipient_name"],
                    recipient_email=draft["recipient_email"],
                    bm_names=", ".join(draft["bm_names"]),
                    bm_count=len(draft["bm_names"]),
                    subject=draft["subject"],
                    body=draft["body"],
                    status=STATUS_FAILED,
                    error_message=str(exc),
                    created_at=datetime.now(),
                ))
                failed_count += 1
            else:
                draft["status"] = STATUS_SENT
                session.add(ReviewCoverageEmailNotification(
                    division=draft["division"],
                    recipient_name=draft["recipient_name"],
                    recipient_email=draft["recipient_email"],
                    bm_names=", ".join(draft["bm_names"]),
                    bm_count=len(draft["bm_names"]),
                    subject=draft["subject"],
                    body=draft["body"],
                    status=STATUS_SENT,
                    created_at=datetime.now(),
                    sent_at=datetime.now(),
                ))
                sent_count += 1

            progress_callback("sending", label=draft["recipient_name"], completed=index, total=total)
            if index % COMMIT_EVERY_N_EMAILS == 0:
                session.commit()

        session.commit()
    finally:
        session.close()
        if connection is not None:
            try:
                connection.quit()
            except Exception:
                pass

    logger.info(f"Coverage Summary notification send complete: {sent_count} sent, {failed_count} failed")
    return {"sent_count": sent_count, "failed_count": failed_count, "drafts": drafts}


def get_recent_notifications(limit: int = 50) -> list:
    """Recent send-log rows, most recent first."""
    session = get_config_session()
    try:
        rows = session.query(ReviewCoverageEmailNotification).order_by(
            ReviewCoverageEmailNotification.created_at.desc()
        ).limit(limit).all()
        return [
            {
                "division": r.division or "",
                "recipient_name": r.recipient_name or "",
                "recipient_email": r.recipient_email or "",
                "bm_names": r.bm_names or "",
                "bm_count": r.bm_count,
                "subject": r.subject,
                "status": r.status,
                "created_at": r.created_at.strftime("%d %b %Y, %I:%M %p") if r.created_at else "",
            }
            for r in rows
        ]
    finally:
        session.close()
