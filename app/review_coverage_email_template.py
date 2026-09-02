"""Renders the Coverage Summary automated-email workflow's own
notification -- one HTML document (+ plain-text companion) per ABM,
listing the BM Coverage Summary files attached to that one email (see
app/review_coverage_notification_service.py for the actual attachment
grouping; this module only renders already-resolved data).

Reuses app/email_template.py's exact brand colors/fonts/logo-CID
(imported, never redefined) and the overall document skeleton, same
reasoning as app/work_distribution_email_template.py's own docstring:
consistent rendering in Gmail/Outlook, and every notification email in
this application looking like one product. Deliberately much simpler
than either of those two templates -- there is no finding/KPI content
here at all, just "here are your BMs' attached Coverage Summary files."

Every value that originated from uploaded data or the hierarchy workbook
is HTML-escaped before being placed in markup.
"""

from html import escape as esc

from app.email_template import (
    BORDER,
    BRAND_PRIMARY,
    BRAND_PRIMARY_SOFT,
    FONT_STACK,
    LOGO_CID,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WHITE,
)


def _summary_box(cells: list[tuple[str, str]]) -> str:
    label_cells = "".join(
        f'<td style="font-size:12px;color:{TEXT_SECONDARY};padding:0 20px 6px 0;'
        f'font-family:{FONT_STACK};">{esc(label)}</td>'
        for label, _ in cells
    )
    value_cells = "".join(
        f'<td style="font-size:15px;color:{TEXT_PRIMARY};font-weight:bold;padding:0 20px 0 0;'
        f'font-family:{FONT_STACK};">{esc(value)}</td>'
        for _, value in cells
    )
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background-color:{BRAND_PRIMARY_SOFT};border-radius:6px;margin:18px 0;">
      <tr>
        <td style="padding:16px 20px;">
          <table role="presentation" cellpadding="0" cellspacing="0"><tr>{label_cells}</tr>
          <tr>{value_cells}</tr></table>
        </td>
      </tr>
    </table>
    """


def render_html(recipient_label: str, division: str, generated_at: str, bm_names: list[str]) -> str:
    """Full HTML document for one ABM's consolidated Coverage Summary
    email -- header (with logo), summary card, a bulleted list of the
    attached BM names, and footer. `bm_names` is display order as
    attached (one Coverage Summary .xlsx per name); this function only
    renders it."""
    summary_cells = [
        ("Recipient", recipient_label),
        ("Division", division),
        ("BMs Attached", str(len(bm_names))),
        ("Generated", generated_at),
    ]

    bm_list_html = "".join(
        f'<li style="font-size:13px;color:{TEXT_PRIMARY};font-family:{FONT_STACK};margin-bottom:4px;">{esc(name)}</li>'
        for name in bm_names
    )

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Saffron Automation Notification</title>
<style>
  @media only screen and (max-width: 480px) {{
    .sv-container {{ width: 100% !important; }}
    .sv-padded {{ padding: 16px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background-color:{SURFACE};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{SURFACE};">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" class="sv-container" width="640" cellpadding="0" cellspacing="0"
               style="width:640px;max-width:100%;background-color:{WHITE};border:1px solid {BORDER};border-radius:8px;overflow:hidden;font-family:{FONT_STACK};">
          <tr>
            <td style="background-color:{BRAND_PRIMARY};padding:20px 32px;">
              <table role="presentation" cellpadding="0" cellspacing="0"><tr>
                <td style="vertical-align:middle;padding-right:12px;">
                  <img src="cid:{LOGO_CID}" width="40" height="40" alt="Saffron"
                       style="display:block;width:40px;height:40px;border-radius:6px;">
                </td>
                <td style="vertical-align:middle;">
                  <div style="color:{WHITE};font-size:22px;font-weight:bold;font-family:{FONT_STACK};">Saffron Automation</div>
                  <div style="color:{BRAND_PRIMARY_SOFT};font-size:14px;margin-top:2px;font-family:{FONT_STACK};">Coverage Summary</div>
                </td>
              </tr></table>
            </td>
          </tr>
          <tr>
            <td class="sv-padded" style="padding:24px 32px;">
              <p style="font-size:14px;color:{TEXT_PRIMARY};margin:0 0 12px 0;font-family:{FONT_STACK};">
                Dear {esc(recipient_label)},
              </p>
              <p style="font-size:14px;color:{TEXT_PRIMARY};line-height:1.5;margin:0;font-family:{FONT_STACK};">
                Attached are the {division} Coverage Summary files for each BM reporting to you.
              </p>

              {_summary_box(summary_cells)}

              <p style="font-size:13px;color:{TEXT_SECONDARY};font-family:{FONT_STACK};margin:0 0 8px 0;">
                Attached files:
              </p>
              <ul style="margin:0 0 12px 0;padding-left:20px;">
                {bm_list_html}
              </ul>
            </td>
          </tr>
          <tr>
            <td style="background-color:{SURFACE};padding:16px 32px;border-top:1px solid {BORDER};">
              <div style="font-size:12px;color:{TEXT_SECONDARY};font-family:{FONT_STACK};">Saffron Automation</div>
              <div style="font-size:12px;color:{TEXT_SECONDARY};font-family:{FONT_STACK};">Saffron Formulations</div>
              <div style="font-size:11px;color:{TEXT_MUTED};margin-top:6px;font-family:{FONT_STACK};">
                Generated on {esc(generated_at)}
              </div>
              <div style="font-size:11px;color:{TEXT_MUTED};margin-top:2px;font-family:{FONT_STACK};">
                This is an automated notification generated by the Saffron Automation System.
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def render_text(recipient_label: str, division: str, generated_at: str, bm_names: list[str]) -> str:
    """Plain-text companion for the multipart/alternative fallback."""
    lines = [
        f"Dear {recipient_label},",
        "",
        f"Attached are the {division} Coverage Summary files for each BM reporting to you.",
        "",
        f"Recipient: {recipient_label}",
        f"Division: {division}",
        f"BMs Attached: {len(bm_names)}",
        f"Generated: {generated_at}",
        "",
        "Attached files:",
    ]
    lines.extend(f"  - {name}" for name in bm_names)
    lines.extend([
        "",
        "Saffron Automation",
        "Saffron Formulations",
        f"Generated on {generated_at}",
        "This is an automated notification generated by the Saffron Automation System.",
    ])
    return "\n".join(lines)
