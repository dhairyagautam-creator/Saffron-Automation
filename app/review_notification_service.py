"""Review System's automated management notification email system --
builds one consolidated email per ABM, attaching one combined workbook per
BM reporting to them: an Opus Summary sheet (that BM's own HQ section), a
Coverage Summary sheet, and an RGD Visit and Support sheet, in that order,
in ONE .xlsx file. Supersedes the previous Coverage-Summary-only
per-BM-file workflow (formerly app.review_coverage_service's
generate_coverage_summary_bm_files + this module's own Coverage-only
naming) now that all three of Review System's report types are attached
together. Reuses the existing shared infrastructure throughout, same as
before:
- app/smtp_service.py for the actual SMTP send (the SAME shared sending
  module every other automated-email workflow in this app uses).
- app/email_settings_service.py for the Gmail sender credentials -- the
  SAME single account used by the rest of the application's email
  functionality (Path Validator), never a second, independently-
  configured one.
- app/hierarchy_parser.py / app/hierarchy_service.py for hierarchy
  lookups (find_by_employee_code/find_by_employee_name, is_valid_recipient)
  -- there is exactly one hierarchy dataset in the application; Review
  System has no separate hierarchy of its own.
- app/review_coverage_email_template.py for HTML/text rendering (this
  workflow's own presentation layer, now describing all three attached
  reports rather than Coverage Summary alone).

RECIPIENT WORKFLOW IS UNCHANGED from the Coverage-only version: for each
BM, read that BM's OWN hierarchy row (by Employee Code, falling back to
name) and its abm_code/abm_name fields (populated directly on every BM
row by app.hierarchy_parser). The resolved ABM row must pass
is_valid_recipient() (name + email both on file) to receive an email --
a vacant/missing/no-email ABM, or a BM with no hierarchy row at all, is
logged and left out of every email rather than guessed at; no automatic
escalation past the BM's own direct ABM. Only the FILE'S CONTENTS and the
SENDING ENTRY POINT changed in this rework -- who receives what did not.

BUSINESS RULE (still genuinely Review-System-specific, not shared with
Path Validator, RGD Coverage, or Manager Work Allocation):

  One BM = one combined .xlsx file (Opus Summary + Coverage Summary + RGD
  Visit and Support, one sheet each -- see generate_review_bm_files).
  One ABM = one consolidated email, with every one of THEIR BM's own
  file attached -- never one email per BM, never BMs from different ABMs
  combined into the same email, never a file attached twice.

SEND SCOPE: "Send Emails" now sends for ALL THREE divisions in a single
action (build_notification_batch_all_divisions) rather than the
currently-selected division alone -- the button is disabled entirely
(see all_divisions_ready()) unless every division has all three report
types generated and ready; there is no partial-readiness send.

EMAIL AUTHORITY / SEND HISTORY: this module now participates in the same
Phase 2 email-authority machinery as Path Validator and Work Distribution
-- app.module_data_version_service.bump_data_version("review_system") is
called from each of the three generation entry points
(generate_coverage_summary, generate_opus_summary, generate_rgd_summary)
whenever any of them regenerates, for any division (ONE flat flag for the
whole module, not per-division -- see send_button_state/
all_divisions_data_state below), and app.email_send_history_service's
shared, cross-machine email_send_history table is the sole source of
"last sent"/resend-confirm state via get_last_send/record_send. The
previous LOCAL, per-attempt review_coverage_email_notifications table
(ReviewCoverageEmailNotification) is no longer written to by this
module -- its historical rows are left untouched (see
get_recent_notifications, kept only for reading whatever was already
recorded before this rework)."""

from pathlib import Path

import openpyxl
from loguru import logger

from app.config import REVIEW_UPLOADS_DIR
from app.email_send_history_service import get_last_send, record_send
from app.email_settings_service import get_settings
from app.hierarchy_parser import find_by_employee_code, find_by_employee_name
from app.hierarchy_service import is_valid_recipient
from app.module_data_version_service import get_data_version
from app.review_coverage_email_template import render_html, render_text

# Review System's own hierarchy table (module_registry's canonical key
# for this module) -- see app/hierarchy_parser.py's HIERARCHY_TABLES.
_MODULE_KEY = "review_system"
from app.review_coverage_service import (
    COVERAGE_REPORT_MONTHS,
    DIVISIONS,
    _HQ_SPELLING_ALIASES,
    _compute_coverage_blocks,
    _norm as _coverage_norm,
    _safe_filename_component,
    coverage_prerequisites_ready,
)
from app.review_opus_service import (
    OPUS_HQ_BLOCKS_BY_DIVISION,
    OPUS_REPORT_MONTHS,
    compute_opus_blocks_by_hq,
    opus_prerequisites_ready,
)
from app.review_opus_service import _write_sheet as _write_opus_sheet
from app.review_coverage_service import _write_sheet as _write_coverage_sheet
from app.review_rgd_service import _load_rgd_rows, _rows_for_bm, rgd_prerequisites_ready
from app.review_rgd_service import _write_sheet as _write_rgd_sheet
from app.smtp_service import open_smtp_connection, send_via_connection
from database.connection import get_config_session, to_local, utcnow
from database.models import ReviewCoverageEmailNotification

STATUS_DRAFT = "Draft"
STATUS_SENT = "Sent"
STATUS_FAILED = "Failed"


# --- Hierarchy resolution (unchanged from the Coverage-only version) ----

def _resolve_bm_hierarchy_row(emp_code: str, name: str) -> dict | None:
    """Code-first, name-fallback lookup for the BM's OWN hierarchy row --
    mirrors app.doj_eligibility_service.resolve_doj's own code-first
    convention for the same reason: Employee Code is the reliable
    identity, name is a display-only fallback for whatever hierarchy rows
    lack a matching code."""
    row = find_by_employee_code(_MODULE_KEY, emp_code) if emp_code else None
    if row is not None:
        return row
    matches = find_by_employee_name(_MODULE_KEY, name) if name else []
    return matches[0] if matches else None


def _resolve_abm(bm_hierarchy_row: dict) -> dict | None:
    """The BM's own direct ABM (abm_code/abm_name, populated on every BM
    row by app.hierarchy_parser) -- one direct rung, no fallback beyond
    it (see module docstring)."""
    abm_code = bm_hierarchy_row.get("abm_code")
    abm_name = bm_hierarchy_row.get("abm_name")
    candidate = find_by_employee_code(_MODULE_KEY, abm_code) if abm_code else None
    if candidate is None and abm_name:
        matches = find_by_employee_name(_MODULE_KEY, abm_name)
        candidate = matches[0] if matches else None
    return candidate


# --- Combined per-BM file generation --------------------------------------

def _generated_output_dir() -> Path:
    out = REVIEW_UPLOADS_DIR / "generated_reports"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _bm_files_output_dir(division: str) -> Path:
    out = _generated_output_dir() / "review_bm_files" / division.strip().lower()
    out.mkdir(parents=True, exist_ok=True)
    return out


def _division_reports_ready(division: str) -> bool:
    """True only if all three of Opus Summary, Coverage Summary, and RGD
    Visit and Support are generatable for `division` right now -- their
    own source files uploaded and valid, and (Opus only) a Region/HQ
    mapping already built for this division (see
    app/review_opus_mapping.py). Used to gate the "Send Emails" button
    (see all_divisions_ready) -- per spec (Q5), a division with only
    some of its three reports ready blocks the send entirely rather than
    sending a partial file."""
    coverage_ready, _ = coverage_prerequisites_ready(division)
    if not coverage_ready:
        return False
    if OPUS_HQ_BLOCKS_BY_DIVISION.get(division) is None:
        return False
    opus_ready, _ = opus_prerequisites_ready(division)
    if not opus_ready:
        return False
    rgd_ready, _ = rgd_prerequisites_ready(division)
    return rgd_ready


def all_divisions_ready() -> bool:
    """True only if EVERY division has all three report types ready --
    since Send Emails now sends for all three divisions in one action
    (Q2), a single not-ready division blocks the whole action (Q5)."""
    return all(_division_reports_ready(division) for division in DIVISIONS)


def _resolve_opus_block_for_bm(opus_by_hq: dict, hq_name: str):
    """Looks up a BM's own Opus Summary HQ block by the bare "Reporting
    HQ" spelling Coverage Summary already resolved for that BM
    (app.review_coverage_service.ComputedBmBlock.hq), applying the EXACT
    same 3-step normalization app.review_coverage_service._hq_is_applicable
    already uses to match that same bare spelling against HQ
    Distribution's canonical names: as written, with " POOL" appended,
    and through _HQ_SPELLING_ALIASES. Returns None if no Opus Summary
    section resolves for this HQ (caller writes an explicit placeholder
    sheet rather than silently omitting the file -- see
    generate_review_bm_files)."""
    hq_norm = _coverage_norm(hq_name)
    if hq_norm in opus_by_hq:
        return opus_by_hq[hq_norm]
    pooled = f"{hq_norm} POOL"
    if pooled in opus_by_hq:
        return opus_by_hq[pooled]
    alias = _HQ_SPELLING_ALIASES.get(hq_norm)
    if alias is not None and alias in opus_by_hq:
        return opus_by_hq[alias]
    return None


def generate_review_bm_files(division: str, report_progress=None) -> dict:
    """Generates ONE combined .xlsx per BM for `division` -- three sheets
    in order Opus Summary, Coverage Summary, RGD Visit and Support (Q4),
    each written by that report's own already-tested _write_sheet helper
    against a single-BM (or single-HQ, for Opus) slice of the same
    computation each report's own combined-workbook generator uses, so a
    BM's numbers here can never disagree with the combined workbooks on
    the File Preview page.

    Full-replace per division: every existing file under this division's
    own per-BM output folder is deleted before regenerating, same
    "an upload/run is a complete snapshot" convention the underlying
    per-report generators already use for their own combined workbooks.

    A BM whose sanitized display name collides with an earlier BM's in
    the SAME run (two different Employee Codes, same Name) is
    disambiguated by appending that BM's own Employee Code in
    parentheses, logged as a warning -- never silently overwriting one
    BM's file with another's.

    A BM whose own HQ has no matching Opus Summary section (see
    _resolve_opus_block_for_bm) still gets a file -- its Opus Summary
    sheet carries a single explanatory cell instead of a data block,
    mirroring how Opus Summary's own combined workbook already renders an
    unresolved HQ block rather than omitting it -- logged as a warning so
    it's auditable, never silently dropped.

    Returns:
        {
            "success": bool,
            "division": str,
            "files": [{"emp_code": str, "name": str, "file_path": str}, ...],
            "errors": [str],
        }
    """
    if report_progress:
        report_progress(0, f"Checking {division} prerequisites...")

    if division not in DIVISIONS:
        return {"success": False, "division": division, "files": [], "errors": [f"Unknown division {division!r}."]}

    if not _division_reports_ready(division):
        return {
            "success": False, "division": division, "files": [],
            "errors": [
                f"Opus Summary, Coverage Summary, and RGD Visit and Support must all be "
                f"ready for {division} before its BM files can be generated."
            ],
        }

    try:
        if report_progress:
            report_progress(10, "Computing Coverage Summary...")
        coverage_blocks = _compute_coverage_blocks(division)

        if report_progress:
            report_progress(40, "Computing Opus Summary...")
        opus_by_hq = compute_opus_blocks_by_hq(division)

        if report_progress:
            report_progress(70, "Loading RGD Visit and Support...")
        rgd_rows = _load_rgd_rows(division)

        if report_progress:
            report_progress(85, "Writing per-BM workbooks...")
        out_dir = _bm_files_output_dir(division)
        for old_file in out_dir.glob("*.xlsx"):
            old_file.unlink()

        files = []
        used_filenames: dict[str, str] = {}  # sanitized filename stem -> emp_code that claimed it
        for block in coverage_blocks:
            stem = _safe_filename_component(block.name) or block.emp_code
            claimed_by = used_filenames.get(stem)
            if claimed_by is not None and claimed_by != block.emp_code:
                logger.warning(
                    f"Review Summary BM files ({division}): two BMs share the display name "
                    f"{block.name!r} ({claimed_by!r} and {block.emp_code!r}) -- disambiguating "
                    f"{block.emp_code!r}'s filename with its own Employee Code."
                )
                stem = f"{stem} ({block.emp_code})"
            used_filenames[stem] = block.emp_code

            opus_block = _resolve_opus_block_for_bm(opus_by_hq, block.hq)
            if opus_block is None:
                logger.warning(
                    f"Review Summary BM files ({division}): BM {block.name!r} ({block.emp_code!r})'s "
                    f"HQ {block.hq!r} has no matching Opus Summary section -- writing an "
                    "unresolved placeholder sheet for this BM rather than skipping the file."
                )

            wb = openpyxl.Workbook()
            opus_ws = wb.active
            opus_ws.title = "OPUS SUMMARY"
            if opus_block is not None:
                _write_opus_sheet(opus_ws, [opus_block], OPUS_REPORT_MONTHS)
            else:
                opus_ws.cell(
                    row=1, column=1,
                    value=f"No Opus Summary section could be resolved for HQ {block.hq!r}.",
                )

            coverage_ws = wb.create_sheet("COVERAGE SUMMARY")
            _write_coverage_sheet(coverage_ws, [block], COVERAGE_REPORT_MONTHS)

            rgd_ws = wb.create_sheet("RGD VISIT AND SUPPORT")
            _write_rgd_sheet(rgd_ws, _rows_for_bm(rgd_rows, block.emp_code))

            out_path = out_dir / f"Review Summary - {stem}.xlsx"
            wb.save(out_path)
            files.append({"emp_code": block.emp_code, "name": block.name, "file_path": str(out_path)})

    except Exception as exc:
        logger.exception(f"Review Summary per-BM file generation failed for {division}")
        return {"success": False, "division": division, "files": [], "errors": [f"Generation failed: {exc!r}"]}

    if report_progress:
        report_progress(100, "Done.")

    logger.info(f"Review Summary per-BM files generated for {division}: {len(files)} file(s) -> {out_dir}")
    return {"success": True, "division": division, "files": files, "errors": []}


# --- Batch build (routing + grouping) -- unchanged recipient workflow ----

def build_notification_batch(division: str, bm_files: list | None = None) -> list:
    """Builds (but does not send) one consolidated draft per ABM, one
    combined 3-sheet attachment per BM reporting to them. `bm_files`
    defaults to a fresh call to generate_review_bm_files(division) --
    pass an already-built list (that function's own "files" key) to
    reuse files a caller already generated in the same run, or to test
    the grouping logic against fixed fixtures without touching disk.

    A BM whose hierarchy row can't be found, or whose resolved ABM is
    vacant/missing/no-email, is logged and left out of every email --
    never guessed at, never silently dropped without a trace.

    Returns a list of drafts: {recipient_name, recipient_email, division,
    bm_names, file_paths, subject, body, text_body, status}."""
    if bm_files is None:
        result = generate_review_bm_files(division)
        if not result["success"]:
            logger.warning(
                f"Review Summary notification batch ({division}): file generation failed -- {result['errors']}"
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
                f"Review Summary notification ({division}): BM {bm['name']!r} ({bm['emp_code']!r}) "
                "not found in hierarchy. No email sent for this BM."
            )
            continue

        abm_row = _resolve_abm(hierarchy_row)
        if not is_valid_recipient(abm_row):
            unresolved_count += 1
            logger.warning(
                f"Review Summary notification ({division}): BM {bm['name']!r}'s ABM is vacant, "
                "missing, or has no email on file. No email sent for this BM."
            )
            continue

        email = abm_row["email"]
        group = groups.setdefault(email, {"name": abm_row["employee_name"], "bm_names": [], "file_paths": []})
        group["bm_names"].append(bm["name"])
        group["file_paths"].append(bm["file_path"])

    if unresolved_count:
        logger.warning(
            f"Review Summary notification batch ({division}): {unresolved_count} BM routing(s) "
            "could not be resolved -- see individual warnings above."
        )

    now_text = utcnow().strftime("%d %b %Y, %I:%M %p")
    drafts = []
    for email, group in groups.items():
        bm_count = len(group["bm_names"])
        drafts.append({
            "recipient_name": group["name"],
            "recipient_email": email,
            "division": division,
            "bm_names": group["bm_names"],
            "file_paths": group["file_paths"],
            "subject": f"Saffron Automation - Review Summary ({division}, {bm_count} BM{'s' if bm_count != 1 else ''})",
            "body": render_html(group["name"], division, now_text, group["bm_names"]),
            "text_body": render_text(group["name"], division, now_text, group["bm_names"]),
            "status": STATUS_DRAFT,
        })
    return drafts


def build_notification_batch_all_divisions(progress_callback=None) -> list:
    """Builds one batch across ALL THREE divisions in a single call (Q2:
    Send Emails now sends for every division at once, not just the
    currently-selected one) -- concatenates each division's own
    build_notification_batch() drafts. Every draft already carries its
    own `division` field, so nothing downstream needs a separate
    division parameter. Callers are expected to have already confirmed
    all_divisions_ready() before calling this -- it does not re-check."""
    progress_callback = progress_callback or (lambda stage, **kwargs: None)
    drafts = []
    for division in DIVISIONS:
        progress_callback("building", label=f"Building {division}...")
        drafts.extend(build_notification_batch(division))
    return drafts


# --- Send Emails button state (Phase 2 email authority, module-wide) -----

def send_button_state(has_data: bool, changed_since_last_send: bool) -> str:
    """Pure: the Findings-equivalent page's "Send Emails" button state --
    "no_data" | "new_data" | "resend_confirm". Same contract as
    app.work_distribution_notification_service.send_button_state -- takes
    already-fetched values, no DB access of its own (see
    all_divisions_data_state()). Callers must ALSO check
    all_divisions_ready() separately (see that function's own docstring)
    -- this function only knows about "has any data ever been generated"
    and "has it changed since the last send," not about per-division
    readiness."""
    if not has_data:
        return "no_data"
    if changed_since_last_send:
        return "new_data"
    return "resend_confirm"


def all_divisions_data_state() -> tuple[bool, bool, dict | None]:
    """(has_data, changed_since_last_send, last_send) for
    send_button_state() above, plus the always-visible status line.
    has_data: the module-wide review_system data_version counter (Q7 --
    ONE flat flag, not per-division; bumped by any of
    generate_coverage_summary/generate_opus_summary/generate_rgd_summary
    regenerating, for any division) has been bumped at least once.
    changed_since_last_send: mismatch (either direction) between that
    local counter and the shared email_send_history table's most
    recently recorded data_version for "review_system". `last_send` is
    that shared row regardless of whether it matches -- for the status
    line, which states a plain fact independent of the changed/unchanged
    judgment."""
    current_version = get_data_version(_MODULE_KEY)
    if current_version == 0:
        return False, False, None

    last_send = get_last_send(_MODULE_KEY)
    changed = last_send is None or last_send["data_version"] != str(current_version)
    return True, changed, last_send


# --- Send --------------------------------------------------------------

def send_notification_batch(drafts: list, progress_callback=None) -> dict:
    """Sends every draft from build_notification_batch_all_divisions()
    over ONE shared, reused SMTP connection
    (app.smtp_service.open_smtp_connection/send_via_connection), exactly
    the same skeleton as app.work_distribution_notification_service.
    send_notification_batch: each draft sent inside its own try/except so
    one recipient's failure never stops the rest of the batch.

    Q9: no longer writes to the local review_coverage_email_notifications
    table -- this module relies entirely on the shared, cross-machine
    email_send_history table (record_send/get_last_send) for "last
    sent"/resend-confirm state, exactly matching Work Distribution's own
    pattern. Historical rows already in review_coverage_email_notifications
    are left untouched (see get_recent_notifications below, kept only for
    reading them).

    Returns {'sent_count', 'failed_count', 'drafts'}."""
    progress_callback = progress_callback or (lambda stage, **kwargs: None)
    total = len(drafts)
    sent_count = 0
    failed_count = 0

    progress_callback("sending", label="Sending Review Summary notifications...", completed=0, total=total)

    connection = None
    sender_email = None
    connection_error = None
    if drafts:
        settings = get_settings()
        try:
            connection, sender_email = open_smtp_connection(settings["sender_email"], settings["app_password"])
        except Exception as exc:
            connection_error = exc

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
                logger.error(f"Failed to send Review Summary notification to {draft['recipient_email']}: {exc}")
                draft["status"] = STATUS_FAILED
                draft["error_message"] = str(exc)
                failed_count += 1
            else:
                draft["status"] = STATUS_SENT
                sent_count += 1

            progress_callback("sending", label=draft["recipient_name"], completed=index, total=total)
    finally:
        if connection is not None:
            try:
                connection.quit()
            except Exception:
                pass

    logger.info(f"Review Summary notification send complete: {sent_count} sent, {failed_count} failed")

    if sent_count > 0:
        record_send(_MODULE_KEY, str(get_data_version(_MODULE_KEY)))

    return {"sent_count": sent_count, "failed_count": failed_count, "drafts": drafts}


def get_recent_notifications(limit: int = 50) -> list:
    """Recent send-log rows from the now-write-only-in-the-past local
    review_coverage_email_notifications table, most recent first -- this
    module no longer adds new rows here (see send_notification_batch's
    own docstring, Q9); kept only so any rows recorded before this
    rework remain readable."""
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
                "created_at": to_local(r.created_at).strftime("%d %b %Y, %I:%M %p") if r.created_at else "",
            }
            for r in rows
        ]
    finally:
        session.close()
