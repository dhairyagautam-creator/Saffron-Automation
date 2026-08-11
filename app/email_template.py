"""Renders the branded manager notification email — both the HTML version
and a plain-text companion for the multipart/alternative fallback (see
app/smtp_service.py, which assembles the actual MIME message and embeds the
logo referenced here as `cid:saffron_logo`).

Pure presentation — everything here just formats already-resolved data into
markup/text. Grouping findings by manager, resolving addresses, and deciding
what goes into each employee's section all happen in
app/notification_service.py; this module only renders that data.

Every value that originated from user-uploaded data (employee names, HQ,
manager name, resolved addresses, ...) is HTML-escaped before being placed
in the markup, since it isn't safe to trust as raw HTML.

Styling is inline throughout (plus a small <style> block used only for a
mobile media query) so the email renders consistently in Gmail and Outlook,
which strip or ignore external/embedded stylesheets to varying degrees. The
employee table is a real HTML <table> — never markdown or padded plain text.
Colors mirror the brand palette in ui/theme.py (Saffron Formulations orange).
"""

from html import escape as esc

# Mirrors ui/theme.py's Color class — duplicated here rather than imported
# so the email layer doesn't depend on the desktop UI layer.
BRAND_PRIMARY = "#F2811A"
BRAND_PRIMARY_DARK = "#C25D0C"
BRAND_PRIMARY_SOFT = "#FDECDD"
TEXT_PRIMARY = "#2B2B2B"
TEXT_SECONDARY = "#6B7280"
TEXT_MUTED = "#9AA0AA"
BORDER = "#E4E6EA"
SURFACE = "#F6F7F9"
WHITE = "#FFFFFF"

FONT_STACK = "Arial, Helvetica, sans-serif"

# Referenced by app/smtp_service.py, which attaches assets/saffron_logo.png
# inline with this Content-ID.
LOGO_CID = "saffron_logo"

TABLE_COLUMNS = [
    ("employee_name", "Employee Name"),
    ("employee_code", "Employee Code"),
    ("designation", "Designation"),
    ("hq", "HQ"),
    ("rule_label", "Rule Triggered"),
    # Holds the concentration sentence for a location finding, or the
    # worked/required/short summary for a Low Working Hours finding -- one
    # neutral header that reads correctly for both rule families.
    ("concentration_text", "Summary"),
    ("visit_date_text", "Date"),
]

# Master email only (see notification_service._build_consolidated_email's
# show_division param) -- per-RBM emails never show this column, since an
# RBM's own team is implicitly a single division already. Division slots in
# right after Employee Code, alongside the other identifying finding details.
TABLE_COLUMNS_WITH_DIVISION = (
    TABLE_COLUMNS[:2] + [("division", "Division")] + TABLE_COLUMNS[2:]
)


def concentration_sentence(concentration_percent: float, radius_meters: int) -> str:
    """'90% of calls were made within a 200 meter radius.' — always a
    sentence, never a bare number, per the required table format."""
    pct_text = f"{concentration_percent:g}"
    return f"{pct_text}% of calls were made within a {radius_meters} meter radius."


def occurrence_bullets(matched: int, valid: int, radius_meters: int, threshold_percent: float, date_text: str | None = None) -> list[str]:
    """The three explanation lines shown under an employee's heading. If
    `date_text` is given (an employee flagged on more than one date), each
    line is prefixed with that date so multiple occurrences stay distinct."""
    prefix = f"On {date_text}: " if date_text else ""
    threshold_text = f"{threshold_percent:g}"
    return [
        f"{prefix}{matched} out of {valid} valid GPS-tagged calls were recorded within "
        f"approximately {radius_meters} meters of the same location.",
        f"This exceeded the configured concentration threshold of {threshold_text}%.",
        "The activity was automatically flagged for managerial review.",
    ]


def _summary_box(cells: list[tuple[str, str]]) -> str:
    """A row of label/value pairs in a tinted card — used both for the
    standard Manager/Date/Employees Flagged/Rules Triggered summary and,
    on the master email, the extra Total Employees Flagged/Total RBMs
    Affected/Analysis Timestamp/Workbook Name summary underneath it."""
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


def _table_header_row(columns: list[tuple[str, str]]) -> str:
    cells = "".join(
        f'<th style="background-color:{BRAND_PRIMARY};color:{WHITE};text-align:left;'
        f'padding:10px 12px;border:1px solid {BRAND_PRIMARY_DARK};font-size:13px;'
        f'font-family:{FONT_STACK};">{esc(label)}</th>'
        for _, label in columns
    )
    return f"<tr>{cells}</tr>"


def _table_data_row(row: dict, index: int, columns: list[tuple[str, str]]) -> str:
    bg = WHITE if index % 2 == 0 else SURFACE
    cells = "".join(
        f'<td style="padding:10px 12px;border:1px solid {BORDER};font-size:13px;'
        f'text-align:left;color:{TEXT_PRIMARY};font-family:{FONT_STACK};background-color:{bg};">'
        f"{esc(str(row.get(key, '')))}</td>"
        for key, _ in columns
    )
    return f"<tr>{cells}</tr>"


def _findings_table(table_rows: list[dict], columns: list[tuple[str, str]]) -> str:
    rows_html = "".join(_table_data_row(row, i, columns) for i, row in enumerate(table_rows))
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="border-collapse:collapse;margin:20px 0;border:1px solid {BORDER};">
      {_table_header_row(columns)}
      {rows_html}
    </table>
    """


def _address_list(addresses: list[str]) -> str:
    if not addresses:
        return (
            f'<div style="font-size:13px;color:{TEXT_MUTED};font-family:{FONT_STACK};">'
            "No resolved addresses available.</div>"
        )
    items = "".join(
        f'<li style="margin-bottom:4px;font-size:13px;color:{TEXT_PRIMARY};'
        f'font-family:{FONT_STACK};">{esc(address)}</li>'
        for address in addresses
    )
    return f'<ul style="margin:6px 0 0 18px;padding:0;">{items}</ul>'


def _bullet_list(lines: list[str]) -> str:
    items = "".join(
        f'<li style="margin-bottom:4px;font-size:13px;color:{TEXT_PRIMARY};line-height:1.5;'
        f'font-family:{FONT_STACK};">{esc(line)}</li>'
        for line in lines
    )
    return f'<ul style="margin:6px 0 0 18px;padding:0;">{items}</ul>'


def _labeled_block(title: str, lines: list[str]) -> str:
    return f"""
      <div style="font-size:13px;color:{TEXT_PRIMARY};margin-top:12px;font-family:{FONT_STACK};">
        <strong>{esc(title)}</strong>
      </div>
      {_bullet_list(lines)}
    """


def _working_hours_lines(wh: dict) -> list[str]:
    """The five Working Hours detail lines for one HR occurrence, prefixed
    with the date only when an employee has more than one (wh['date_text']
    is None otherwise -- see notification_service._build_consolidated_email)."""
    header = f"On {wh['date_text']}:" if wh.get("date_text") else None
    lines = [
        f"First call: {wh['first_call']}",
        f"Last call: {wh['last_call']}",
        f"Hours worked: {wh['hours_worked']}",
        f"Required: {wh['required']}",
        f"Short by: {wh['short_by']}",
    ]
    return ([header] + lines) if header else lines


def _employee_section(section: dict) -> str:
    """One employee, rendered around the employee (not the detector): an
    optional Status line, then only the sections that apply -- Location
    (with its Frequent Visit Locations), Working Hours, and, when both were
    detected, a Combined interpretation."""
    parts: list[str] = []

    status_label = section.get("status_label")
    status_html = (
        f'<div style="font-size:12px;color:{TEXT_SECONDARY};font-family:{FONT_STACK};margin-bottom:8px;">'
        f"Status: {esc(status_label)}</div>"
        if status_label else ""
    )

    if section["occurrences"]:
        location_lines: list[str] = []
        for occurrence in section["occurrences"]:
            location_lines.extend(occurrence)
        parts.append(_labeled_block("Location", location_lines))
        parts.append(
            f'<div style="font-size:13px;color:{TEXT_PRIMARY};margin-top:12px;font-family:{FONT_STACK};">'
            f"<strong>Frequent Visit Locations</strong></div>"
        )
        parts.append(_address_list(section["addresses"]))

    for wh in section.get("working_hours", []):
        parts.append(_labeled_block("Working Hours", _working_hours_lines(wh)))

    if section.get("combined_lines"):
        parts.append(_labeled_block("Combined interpretation", section["combined_lines"]))

    return f"""
    <div style="border:1px solid {BORDER};border-radius:6px;padding:16px 20px;margin-bottom:14px;">
      <div style="font-size:15px;font-weight:bold;color:{TEXT_PRIMARY};font-family:{FONT_STACK};margin-bottom:4px;">
        {esc(section['employee_name'])}
        <span style="color:{TEXT_SECONDARY};font-weight:normal;">({esc(section['employee_code'])})</span>
      </div>
      {status_html}
      {"".join(parts)}
    </div>
    """


def render_manager_email_html(
    recipient_label: str,
    summary_pairs: list[tuple[str, str]],
    generated_at: str,
    table_rows: list[dict],
    employee_sections: list[dict],
    extra_summary_pairs: list[tuple[str, str]] | None = None,
    show_division: bool = False,
) -> str:
    """Build the full HTML document for one recipient's consolidated
    notification email — header (with logo), summary card(s), findings
    table, per-employee explanation sections, and footer.

    Used for both the per-RBM emails and the master gddesk@ rollup: pass
    `extra_summary_pairs` for the master email's additional Total Employees
    Flagged / Total RBMs Affected / Analysis Timestamp / Workbook Name box,
    and `show_division=True` (master email only) to add the Division column
    to the findings table.
    """
    sections_html = "".join(_employee_section(section) for section in employee_sections)
    extra_summary_html = _summary_box(extra_summary_pairs) if extra_summary_pairs else ""
    table_columns = TABLE_COLUMNS_WITH_DIVISION if show_division else TABLE_COLUMNS

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
    .sv-table-wrap {{ overflow-x: auto !important; }}
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
                  <div style="color:{BRAND_PRIMARY_SOFT};font-size:14px;margin-top:2px;font-family:{FONT_STACK};">Activity Review Notification</div>
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
                The following employees have been flagged by Saffron Automation for managerial review.
              </p>

              {_summary_box(summary_pairs)}
              {extra_summary_html}

              <p style="font-size:13px;color:{TEXT_SECONDARY};background-color:{SURFACE};
                        border-left:3px solid {BRAND_PRIMARY};padding:10px 14px;line-height:1.5;
                        margin:0 0 8px 0;font-family:{FONT_STACK};">
                Saffron Automation detected an unusual concentration of visit locations for the
                employees listed below. This notification is intended for managerial review —
                please assess whether the recorded activity reflects genuine field visits.
              </p>

              <div class="sv-table-wrap">
              {_findings_table(table_rows, table_columns)}
              </div>

              <p style="font-size:14px;color:{TEXT_PRIMARY};font-weight:bold;margin:20px 0 10px 0;font-family:{FONT_STACK};">
                Employee Details
              </p>

              {sections_html}

              <p style="font-size:13px;color:{TEXT_PRIMARY};line-height:1.5;margin:8px 0 0 0;font-family:{FONT_STACK};">
                Please review these activities and take appropriate action if necessary.
              </p>
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
                This is an automated notification generated by Saffron Automation.
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def render_manager_email_text(
    recipient_label: str,
    summary_pairs: list[tuple[str, str]],
    generated_at: str,
    table_rows: list[dict],
    employee_sections: list[dict],
    extra_summary_pairs: list[tuple[str, str]] | None = None,
    show_division: bool = False,
) -> str:
    """Plain-text companion for the multipart/alternative fallback — read
    by the rare client that can't render HTML. Not the primary experience,
    just a readable degrade."""
    lines = [
        f"Dear {recipient_label},",
        "",
        "The following employees have been flagged by Saffron Automation for managerial review.",
        "",
    ]
    lines.extend(f"{label}: {value}" for label, value in summary_pairs)
    if extra_summary_pairs:
        lines.append("")
        lines.extend(f"{label}: {value}" for label, value in extra_summary_pairs)
    lines.extend(
        [
            "",
            "Saffron Automation detected an unusual concentration of visit locations for the "
            "employees listed below. This notification is intended for managerial review.",
            "",
            "Findings:",
        ]
    )
    for row in table_rows:
        division_text = f", {row.get('division', '')}" if show_division else ""
        lines.append(
            f"- {row['employee_name']} ({row['employee_code']}){division_text}, {row['designation']}, {row['hq']}, "
            f"{row['rule_label']}, {row['visit_date_text']}: {row['concentration_text']}"
        )

    lines.append("")
    lines.append("Employee Details")
    for section in employee_sections:
        lines.append("")
        lines.append(f"{section['employee_name']} ({section['employee_code']})")
        if section.get("status_label"):
            lines.append(f"Status: {section['status_label']}")
        if section["occurrences"]:
            lines.append("Location:")
            for occurrence in section["occurrences"]:
                for line in occurrence:
                    lines.append(f"- {line}")
            lines.append("Frequent Visit Locations:")
            if section["addresses"]:
                lines.extend(f"  - {address}" for address in section["addresses"])
            else:
                lines.append("  - No resolved addresses available.")
        for wh in section.get("working_hours", []):
            lines.append("Working Hours:")
            for line in _working_hours_lines(wh):
                lines.append(f"- {line}")
        if section.get("combined_lines"):
            lines.append("Combined interpretation:")
            for line in section["combined_lines"]:
                lines.append(f"- {line}")

    lines.extend(
        [
            "",
            "Please review these activities and take appropriate action if necessary.",
            "",
            "Saffron Automation",
            "Saffron Formulations",
            f"Generated on {generated_at}",
            "This is an automated notification generated by Saffron Automation.",
        ]
    )
    return "\n".join(lines)
