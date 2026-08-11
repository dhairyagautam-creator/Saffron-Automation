"""Inventory Automated Email System -- Version 2.0, Milestone 56.

Built using app/notification_service.py (Path Validator's Automated Email
System) as the explicit reference architecture, per instruction: same
skeleton (build the full batch in memory first, open ONE shared SMTP
connection, send each draft in its own try/except so one failure never
stops the batch, log one row per attempt, report progress via a
send_state-style module so the UI can poll it), only the report-generation
logic is different.

What's DELIBERATELY NOT copied from Path Validator, and why: Path
Validator resolves recipients dynamically at send time by walking the
employee hierarchy (app.hierarchy_service.compute_seniors(),
FALLBACK_CHAINS, "RBM is Vacant" handling) -- Inventory has no per-employee
hierarchy to resolve FROM. Its recipients are a small, manually-configured
list instead (app/inventory_email_recipients_service.py, the Automated
Emails page) -- "resolve a recipient" here just means "look up which
configured recipients have this row's Division checked," a static
lookup, never a runtime chain-walk. There is therefore no "Unresolved"
status equivalent to Path Validator's -- every configured recipient IS a
resolved recipient by definition. The one Inventory-specific status this
module DOES need is "Skipped - No Data": a recipient whose Division
filter currently matches zero replenishment-required rows is never sent
an empty attachment, but the attempt is still logged so it's auditable.

Reusable reporting framework (per explicit instruction -- Threshold
Reports, CWH Reports, Employee Detector Reports, Path Validator Reports,
and Scheduled Reports are all named as FUTURE consumers of this same
shape): the module is split into a generic half and a Replenishment-
specific half so adding a new report type later never touches the
generic half at all --

  - `build_email_batch_for_report(report_type, rows)` -- generic: given
    ANY list of dicts that each carry a "division" key, groups them by
    which configured recipient(s) should receive them. Knows nothing
    about Replenishment specifically.
  - `send_report_batch(drafts, columns, headings, ...)` -- generic:
    given a batch (from the function above) plus the report's own
    column shape, generates each recipient's Excel attachment via
    app.table_export_service.write_rows_to_excel() -- the EXACT SAME
    function the Replenishment page's manual Export button calls, so
    there is only ONE export implementation anywhere in the
    application -- and sends it via app.smtp_service (also the exact
    same module Path Validator's own sending uses, extended in this
    milestone with optional attachments/credentials, not duplicated).
  - `build_inventory_replenishment_email_batch()` /
    `send_inventory_replenishment_emails()` -- the ONLY report actually
    wired up today: two thin, ~5-line wrappers supplying Replenishment's
    own data source and column shape to the two generic functions above.
    A future Threshold report is the same two-function shape again, with
    Threshold's own data/columns swapped in -- no changes needed here.

Automated workflow (see ui/inventory_upload_page.py for the actual
trigger): Inventory Upload -> Inventory Processing Complete -> Generate
Replenishment Table -> for each configured recipient, apply their
Division filter -> reuse the Export Framework to generate a filtered
Excel -> send via the architecture described above. Each recipient
receives ONLY the rows for their own configured Division(s).
"""

import os
import tempfile
from datetime import datetime

from loguru import logger

from app.inventory_email_recipients_service import get_all_recipients
from app.inventory_email_settings_service import get_settings
from app.inventory_send_state import update_progress
from app.replenishment_service import (
    REPLENISHMENT_REPORT_COLUMNS,
    REPLENISHMENT_REPORT_HEADINGS,
    REPLENISHMENT_REPORT_SHORTAGE_FILL,
    REPLENISHMENT_REPORT_SHORTAGE_TEXT,
    get_replenishment_required,
)
from app.smtp_service import open_smtp_connection, send_via_connection
from app.table_export_service import RowStyle, default_export_filename, write_rows_to_excel
from database.connection import get_config_session
from database.models import InventoryEmailNotification

# Same rationale as app.notification_service.COMMIT_EVERY_N_EMAILS: batched
# commits so a disk fsync isn't paid per email, but the send log still
# fills in during the run (visible to a live send-log poll) rather than
# only appearing once the whole batch is already over.
COMMIT_EVERY_N_EMAILS = 3

STATUS_DRAFT = "Draft"
STATUS_SENT = "Sent"
STATUS_FAILED = "Failed"
STATUS_SKIPPED_NO_DATA = "Skipped - No Data"


def _inventory_report_row_style(row: dict) -> RowStyle | None:
    """Mirrors ui.inventory_replenishment_page._replenishment_row_style()
    -- the CWH-shortage row highlight -- so the emailed attachment looks
    identical to what a manual Export from the Replenishment page would
    produce, not just the same numbers with the color stripped out."""
    if row.get("cwh_shortage"):
        return RowStyle(fill_color=REPLENISHMENT_REPORT_SHORTAGE_FILL, font_color=REPLENISHMENT_REPORT_SHORTAGE_TEXT)
    return None


def _build_email_content(report_type: str, recipient_name: str, divisions: list[str], row_count: int) -> tuple[str, str, str]:
    """(subject, html_body, text_body) for one recipient's report email.
    Deliberately simple, unbranded-beyond-the-automatic-inline-logo markup
    -- app.smtp_service._build_message() already embeds the Saffron logo
    inline for every email regardless of caller, so this doesn't need its
    own template module the way Path Validator's finding emails do (see
    app/email_template.py, which is finding-content-specific and not
    reused here)."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    division_text = ", ".join(divisions)
    subject = f"Saffron Automation - Inventory {report_type} Report ({date_str})"
    html_body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #2B2B2B; font-size: 14px;">
        <p>Hello {recipient_name},</p>
        <p>Attached is the Inventory {report_type} report for: <b>{division_text}</b>.</p>
        <p><b>{row_count}</b> product(s) currently require replenishment for your configured division(s).</p>
        <p style="color: #6B7280; font-size: 12px;">
            This is an automated message generated after the latest Inventory Report upload.
        </p>
    </body></html>
    """
    text_body = (
        f"Hello {recipient_name},\n\n"
        f"Attached is the Inventory {report_type} report for: {division_text}.\n"
        f"{row_count} product(s) currently require replenishment for your configured division(s).\n\n"
        "This is an automated message generated after the latest Inventory Report upload."
    )
    return subject, html_body, text_body


def build_email_batch_for_report(report_type: str, rows: list[dict]) -> list[dict]:
    """GENERIC across report types -- see module docstring. `rows` must be
    a list of dicts each carrying a "division" key (every Inventory report
    row shape does: Replenishment, Threshold, and CWH rows all carry
    Division already). Returns one draft per CONFIGURED RECIPIENT (not one
    per division) -- a recipient with multiple divisions checked gets
    every one of their rows combined into a single email, never split.

    A recipient whose filter matches zero rows still gets a draft here
    (status STATUS_SKIPPED_NO_DATA) rather than being silently omitted --
    see send_report_batch(), which is what actually decides not to send
    it; this function's job is only to compute what EACH recipient's
    dataset would be, never to decide who to skip."""
    recipients = get_all_recipients()
    drafts = []
    for recipient in recipients:
        filtered_rows = [row for row in rows if row.get("division") in recipient["divisions"]]
        subject, html_body, text_body = _build_email_content(
            report_type, recipient["name"], recipient["divisions"], len(filtered_rows)
        )
        drafts.append(
            {
                "recipient_id": recipient["id"],
                "recipient_name": recipient["name"],
                "recipient_email": recipient["email"],
                "divisions": recipient["divisions"],
                "rows": filtered_rows,
                "row_count": len(filtered_rows),
                "subject": subject,
                "body": html_body,
                "text_body": text_body,
                "status": STATUS_DRAFT if filtered_rows else STATUS_SKIPPED_NO_DATA,
            }
        )
    return drafts


def _generate_attachment(rows: list[dict], columns: tuple, headings: dict, sheet_title: str, row_style_fn) -> tuple[str, bytes]:
    """(filename, file_bytes) for one recipient's report attachment --
    generated via app.table_export_service.write_rows_to_excel(), the
    EXACT SAME function the manual Export button on the report's own page
    calls. Writes to a real temporary file (write_rows_to_excel()'s public
    contract takes a file path, not a byte buffer, matching every other
    caller -- see app/table_export_service.py) and reads the bytes back
    for the email attachment; the temp file is removed immediately after,
    it never lingers on disk."""
    filename = default_export_filename(f"Inventory_{sheet_title}")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = os.path.join(tmp_dir, filename)
        result = write_rows_to_excel(rows, columns, headings, tmp_path, sheet_title=sheet_title, row_style_fn=row_style_fn)
        if not result.success:
            raise RuntimeError(f"Failed to generate report attachment: {result.error_message}")
        with open(tmp_path, "rb") as f:
            file_bytes = f.read()
    return filename, file_bytes


def send_report_batch(
    drafts: list[dict],
    columns: tuple,
    headings: dict,
    *,
    report_type: str = "Replenishment",
    sheet_title: str = "Report",
    row_style_fn=None,
    progress_callback=None,
) -> dict:
    """GENERIC across report types -- see module docstring. Sends every
    non-skipped draft from build_email_batch_for_report() over ONE shared,
    reused SMTP connection (app.smtp_service.open_smtp_connection/
    send_via_connection -- Path Validator's own sending module, extended
    in this milestone with optional attachments/credentials, never
    duplicated), generating each recipient's own Excel attachment via
    _generate_attachment() just before sending it. Each draft is sent
    inside its own try/except -- one recipient's failure never stops the
    rest of the batch, exactly like app.notification_service.send_all_emails.

    Returns {'sent_count', 'failed_count', 'skipped_count', 'drafts'}."""
    progress_callback = progress_callback or (lambda stage, **kwargs: None)

    sendable_drafts = [d for d in drafts if d["status"] != STATUS_SKIPPED_NO_DATA]
    total_drafts = len(drafts)
    sent_count = 0
    failed_count = 0
    skipped_count = total_drafts - len(sendable_drafts)

    progress_callback("sending", label=f"Sending Inventory {report_type} reports...", completed=0, total=total_drafts)
    update_progress("sending", completed=0, total=total_drafts)

    connection = None
    sender_email = None
    connection_error = None
    if sendable_drafts:
        settings = get_settings()
        try:
            connection, sender_email = open_smtp_connection(settings["sender_email"], settings["app_password"])
        except Exception as exc:
            connection_error = exc

    session = get_config_session()
    try:
        for index, draft in enumerate(drafts, start=1):
            if draft["status"] == STATUS_SKIPPED_NO_DATA:
                session.add(
                    InventoryEmailNotification(
                        report_type=report_type,
                        recipient_name=draft["recipient_name"],
                        recipient_email=draft["recipient_email"],
                        divisions=",".join(draft["divisions"]),
                        subject=draft["subject"],
                        body=draft["body"],
                        row_count=0,
                        status=STATUS_SKIPPED_NO_DATA,
                        created_at=datetime.now(),
                    )
                )
            else:
                try:
                    if connection_error is not None:
                        raise connection_error
                    filename, file_bytes = _generate_attachment(
                        draft["rows"], columns, headings, sheet_title, row_style_fn
                    )
                    send_via_connection(
                        connection,
                        sender_email,
                        draft["recipient_email"],
                        draft["subject"],
                        draft["body"],
                        draft.get("text_body"),
                        attachments=[(filename, file_bytes)],
                    )
                except Exception as exc:
                    logger.error(f"Failed to send Inventory {report_type} report to {draft['recipient_email']}: {exc}")
                    draft["status"] = STATUS_FAILED
                    session.add(
                        InventoryEmailNotification(
                            report_type=report_type,
                            recipient_name=draft["recipient_name"],
                            recipient_email=draft["recipient_email"],
                            divisions=",".join(draft["divisions"]),
                            subject=draft["subject"],
                            body=draft["body"],
                            row_count=draft["row_count"],
                            status=STATUS_FAILED,
                            error_message=str(exc),
                            created_at=datetime.now(),
                        )
                    )
                    failed_count += 1
                else:
                    draft["status"] = STATUS_SENT
                    session.add(
                        InventoryEmailNotification(
                            report_type=report_type,
                            recipient_name=draft["recipient_name"],
                            recipient_email=draft["recipient_email"],
                            divisions=",".join(draft["divisions"]),
                            subject=draft["subject"],
                            body=draft["body"],
                            row_count=draft["row_count"],
                            status=STATUS_SENT,
                            created_at=datetime.now(),
                            sent_at=datetime.now(),
                        )
                    )
                    sent_count += 1

            progress_callback("sending", label=draft["recipient_name"], completed=index, total=total_drafts)
            update_progress("sending", label=draft["recipient_name"], completed=index, total=total_drafts)

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

    logger.info(
        f"Inventory {report_type} report send complete: {sent_count} sent, {failed_count} failed, "
        f"{skipped_count} skipped (no data)"
    )
    return {"sent_count": sent_count, "failed_count": failed_count, "skipped_count": skipped_count, "drafts": drafts}


# --- Replenishment-specific wrappers -- the ONLY report wired up today ---


def build_inventory_replenishment_email_batch(replenishment_rows: list[dict] | None = None) -> list[dict]:
    """Thin Replenishment-specific wrapper over build_email_batch_for_report()
    above. `replenishment_rows` defaults to a fresh
    app.replenishment_service.get_replenishment_required() call (the SAME
    data source the Replenishment page's manual Export button reads) --
    a caller that already has the current rows (e.g. right after an
    Inventory Report upload) can pass them directly instead of triggering
    a second read."""
    if replenishment_rows is None:
        replenishment_rows = get_replenishment_required()
    return build_email_batch_for_report("Replenishment", replenishment_rows)


def send_inventory_replenishment_emails(replenishment_rows: list[dict] | None = None, progress_callback=None) -> dict:
    """Thin Replenishment-specific wrapper over send_report_batch() above
    -- builds the batch, then sends it using Replenishment's own column
    shape (REPLENISHMENT_REPORT_COLUMNS/HEADINGS, imported from
    app.replenishment_service -- the exact same constants
    ui/inventory_replenishment_page.py's manual Export button uses) and
    row highlight (_inventory_report_row_style)."""
    drafts = build_inventory_replenishment_email_batch(replenishment_rows)
    return send_report_batch(
        drafts,
        REPLENISHMENT_REPORT_COLUMNS,
        REPLENISHMENT_REPORT_HEADINGS,
        report_type="Replenishment",
        sheet_title="Replenishment",
        row_style_fn=_inventory_report_row_style,
        progress_callback=progress_callback,
    )
