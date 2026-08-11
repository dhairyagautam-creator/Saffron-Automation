"""Sends email via Gmail's SMTP server (smtp.gmail.com:587, STARTTLS).

Credentials come from app/email_settings_service.py (Sender Gmail Address +
Gmail App Password, set on the Settings page) by default — never hardcoded
here. A caller from a DIFFERENT module with its own, separately-configured
sender credentials (see app/inventory_email_settings_service.py, Milestone
56) can pass `sender_email`/`app_password` explicitly to bypass that
default entirely; every existing Path Validator call site passes neither
and is completely unaffected.

Messages are built as a real multipart/related MIME message: a
multipart/alternative part (plain text + HTML, per RFC 2046 — email clients
that can render HTML use it, older/text-only clients fall back to the plain
text version) plus the Saffron logo attached inline and referenced from the
HTML via `cid:` (see app/email_template.py, which owns the actual markup),
plus, since Milestone 56, an optional list of file attachments (e.g. an
Inventory Replenishment Excel export) added as real MIMEApplication parts —
this is the ONE shared MIME-construction/sending module for the whole
application; a new report-sending module should extend the parameters
here rather than hand-rolling its own smtplib/MIME code.
"""

import smtplib
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from loguru import logger

from app.config import ASSETS_DIR
from app.email_settings_service import get_settings
from app.email_template import LOGO_CID

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

LOGO_PATH = ASSETS_DIR / "saffron_logo.png"


class SmtpNotConfigured(Exception):
    """Raised when sender email or app password hasn't been set yet."""


def _get_credentials() -> tuple:
    settings = get_settings()
    sender_email = settings["sender_email"].strip()
    app_password = settings["app_password"].strip()
    if not sender_email or not app_password:
        raise SmtpNotConfigured(
            "Email settings aren't configured — set a sender address and app password on the Settings page."
        )
    return sender_email, app_password


def _attach_files(message: MIMEMultipart, attachments: list | None) -> None:
    """Adds each (filename, bytes) pair in `attachments` as a real
    MIMEApplication attachment -- the general-purpose attachment mechanism
    every future report-sending module (Threshold, CWH, Employee Detector,
    Scheduled Reports -- see app/inventory_notification_service.py's
    module docstring) reuses, rather than each building its own. Does
    nothing if `attachments` is None/empty, so this is a no-op for every
    existing caller that never passes it."""
    for filename, file_bytes in attachments or []:
        part = MIMEApplication(file_bytes, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        message.attach(part)


def _build_message(
    sender_email: str,
    to_address: str,
    subject: str,
    html_body: str,
    text_body: str | None,
    attachments: list | None = None,
) -> MIMEMultipart:
    message = MIMEMultipart("related")
    message["Subject"] = subject
    message["From"] = sender_email
    message["To"] = to_address

    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(text_body or html_body, "plain"))
    alternative.attach(MIMEText(html_body, "html"))
    message.attach(alternative)

    if LOGO_PATH.exists():
        with open(LOGO_PATH, "rb") as f:
            logo = MIMEImage(f.read())
        logo.add_header("Content-ID", f"<{LOGO_CID}>")
        logo.add_header("Content-Disposition", "inline", filename=LOGO_PATH.name)
        message.attach(logo)

    _attach_files(message, attachments)

    return message


def open_smtp_connection(sender_email: str | None = None, app_password: str | None = None) -> tuple[smtplib.SMTP, str]:
    """Open, STARTTLS, and log in once. Returns (connection, sender_email)
    so a whole batch of emails can be sent through the same connection
    instead of reconnecting and re-authenticating per message — see
    send_via_connection / app.notification_service.send_all_emails.

    Pass `sender_email`/`app_password` explicitly to use a DIFFERENT,
    already-configured credential pair instead of Path Validator's own
    Settings-page credentials (see app/inventory_email_settings_service.py)
    -- omit both (the default) to keep the exact existing behavior."""
    if sender_email is None or app_password is None:
        sender_email, app_password = _get_credentials()
    sender_email = sender_email.strip()
    app_password = app_password.strip()
    if not sender_email or not app_password:
        raise SmtpNotConfigured("Sender email and app password must both be provided.")

    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15)
    server.starttls()
    server.login(sender_email, app_password)
    return server, sender_email


def send_via_connection(
    connection: smtplib.SMTP,
    sender_email: str,
    to_address: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    attachments: list | None = None,
) -> None:
    """Send one message through an already-open, already-authenticated
    connection (see open_smtp_connection). `attachments`, if given, is a
    list of (filename: str, file_bytes: bytes) pairs added as real
    attachments (see _attach_files) -- omit for a plain email exactly like
    every existing Path Validator call. Raises on any failure."""
    message = _build_message(sender_email, to_address, subject, html_body, text_body, attachments)
    connection.sendmail(sender_email, [to_address], message.as_string())
    logger.info(f"Sent email to {to_address}: {subject}" + (f" ({len(attachments)} attachment(s))" if attachments else ""))


def send_email(
    to_address: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    attachments: list | None = None,
    sender_email: str | None = None,
    app_password: str | None = None,
) -> None:
    """Send a single HTML email (with a plain-text fallback part and the
    Saffron logo embedded inline) via Gmail SMTP, opening and closing its
    own connection. For sending more than one message, use
    open_smtp_connection + send_via_connection instead so the connection is
    reused. Raises on any failure."""
    connection, resolved_sender = open_smtp_connection(sender_email, app_password)
    try:
        send_via_connection(connection, resolved_sender, to_address, subject, html_body, text_body, attachments)
    finally:
        connection.quit()


def test_connection(sender_email: str | None = None, app_password: str | None = None) -> tuple:
    """Attempt to connect, authenticate, and send a test email to the
    sender's own address. Returns (success, message). Pass
    `sender_email`/`app_password` to test a DIFFERENT credential pair
    (e.g. Inventory's own Settings page) instead of Path Validator's --
    omit both to test Path Validator's own saved credentials, exactly as
    before."""
    if sender_email is None or app_password is None:
        try:
            sender_email, app_password = _get_credentials()
        except SmtpNotConfigured as exc:
            return False, str(exc)

    try:
        send_email(
            sender_email,
            "Saffron Automation - Test Connection",
            "This is a test email confirming your Gmail SMTP settings are working correctly.",
            sender_email=sender_email,
            app_password=app_password,
        )
    except SmtpNotConfigured as exc:
        return False, str(exc)
    except smtplib.SMTPAuthenticationError:
        return False, "Authentication failed — check the sender address and app password."
    except (smtplib.SMTPException, OSError) as exc:
        return False, f"Could not connect or send: {exc}"

    logger.info(f"Test connection succeeded: sent test email to {sender_email}")
    return True, f"Test email sent successfully to {sender_email}."
