"""Renders Work Distribution's automated management notification email --
one HTML document (+ plain-text companion) per RECIPIENT, covering however
many flagged employees route to them across RGD Coverage and/or Manager
Work Allocation. EMPLOYEE-FIRST layout (redesigned 2026-08-06, replacing
the original per-module-section layout): one compact card per distinct
employee, never one card per (employee, module) pair -- an employee flagged
under more than one module gets a single card listing each failed module as
its own short entry within that one card's `reasons` list. Cards are given
to this module pre-sorted and pre-merged by
app/work_distribution_notification_service.py; this module only renders
them.

Deliberately concise, per explicit instruction: a manager should understand
why an employee was flagged within 15-20 seconds, so each reason entry
shows only the module, a one-line explanation, the key actual-vs-required
figures, and the recommended action -- no reporting-hierarchy line, no
full monthly-trend table (a compact one-line trend summary instead), no
duplicate card per module.

Reuses app/email_template.py's exact brand colors/fonts/logo-CID (imported,
never redefined) and the identical inline-CSS-everywhere approach for the
same reason that module's own docstring gives: consistent rendering in
Gmail and Outlook, which strip external/embedded stylesheets to varying
degrees. The overall document skeleton (branded header with logo, summary
box, footer) mirrors render_manager_email_html's shape closely so both
notification systems look like one product -- only the body content differs.

Pure presentation -- grouping flagged employees by recipient, merging an
employee's multiple failed-module entries into one card, and resolving
each reason's actual-values/threshold all happen in
app/work_distribution_notification_service.py; this module only renders
already-resolved data. Every value that originated from uploaded data or the
hierarchy workbook is HTML-escaped before being placed in markup.

Footer text is fixed and deliberately never mentions how this system is
built (no AI/LLM/prompt references) -- see render_html's own footer block.
"""

from html import escape as esc

from app.email_template import (
    BORDER,
    BRAND_PRIMARY,
    BRAND_PRIMARY_DARK,
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


def _actual_values_line(actual_values: list[tuple[str, str]], escape: bool = True) -> str:
    """`escape=True` (the default, used by render_html's own markup) HTML-
    escapes the joined line; `escape=False` (used by render_text's plain-
    text output) returns it raw -- a value like "Doctors with <2 Visits"
    must stay literal in a plain-text email, not become "&lt;2"."""
    if not actual_values:
        return ""
    text = " · ".join(f"{label}: {value}" for label, value in actual_values)
    return esc(text) if escape else text


def _monthly_trend_line(monthly_trend: dict | None) -> str:
    """A single compact inline line -- "Jun: 4, Jul: 3, Aug: 2" -- rather
    than a bordered table, per the "no giant tables unless they genuinely
    improve readability" instruction. Only rendered when `monthly_trend` is
    given (Manager Work Allocation reasons only; RGD Coverage has no
    month-over-month concept). A month with no recorded value renders as an
    em dash, never an invented zero."""
    if not monthly_trend or not monthly_trend.get("months"):
        return ""
    months = monthly_trend["months"]
    values = monthly_trend.get("values", {})
    text = ", ".join(f"{month}: {values.get(month) or '—'}" for month in months)
    return (
        f'<div style="font-size:12px;color:{TEXT_SECONDARY};font-family:{FONT_STACK};margin-top:4px;">'
        f"Monthly Trend — {esc(text)}</div>"
    )


def _reason_block(reason: dict) -> str:
    """One failed module's worth of detail within an employee's card -- the
    module tag (+ analysis period), a one-line explanation, the key
    actual-value(s), the required threshold, an optional monthly-trend
    line, and the recommended action. Deliberately terse: this is one of
    possibly several reason blocks stacked inside one employee card, so
    every line here competes for the reader's 15-20 second budget."""
    module_line = reason["module"]
    if reason.get("analysis_period"):
        module_line += f" — {reason['analysis_period']}"

    actual_values_html = (
        f'<div style="font-size:12px;color:{TEXT_PRIMARY};font-family:{FONT_STACK};margin-top:4px;">'
        f"{_actual_values_line(reason['actual_values'])}</div>"
    ) if reason.get("actual_values") else ""

    threshold_html = (
        f'<div style="font-size:12px;color:{TEXT_SECONDARY};font-family:{FONT_STACK};margin-top:2px;">'
        f"Required: {esc(reason['required_threshold'])}</div>"
    ) if reason.get("required_threshold") else ""

    action_html = (
        f'<div style="font-size:12px;color:{TEXT_PRIMARY};font-family:{FONT_STACK};margin-top:4px;font-style:italic;">'
        f"Recommended: {esc(reason['recommended_action'])}</div>"
    ) if reason.get("recommended_action") else ""

    return f"""
    <div style="margin-top:10px;padding-top:10px;border-top:1px dashed {BORDER};">
      <div style="font-size:12px;font-weight:bold;color:{BRAND_PRIMARY_DARK};font-family:{FONT_STACK};">
        {esc(module_line)}
      </div>
      <div style="font-size:13px;color:{TEXT_PRIMARY};font-family:{FONT_STACK};margin-top:2px;">
        {esc(reason['reason'])}
      </div>
      {actual_values_html}
      {threshold_html}
      {_monthly_trend_line(reason.get("monthly_trend"))}
      {action_html}
    </div>
    """


def _employee_card(employee: dict) -> str:
    """One employee's card -- name/designation/FLAGGED badge, a single
    compact identity line (code/division/HQ), then one `_reason_block` per
    failed module. An employee flagged under two modules gets ONE card with
    two reason blocks, never two cards (see module docstring)."""
    facts = " · ".join(filter(None, [
        f"Code: {employee['employee_code']}" if employee.get("employee_code") else "",
        f"Division: {employee['division']}" if employee.get("division") else "",
        f"HQ: {employee['hq']}" if employee.get("hq") else "",
    ]))
    facts_html = (
        f'<div style="font-size:12px;color:{TEXT_SECONDARY};font-family:{FONT_STACK};margin-top:2px;">{esc(facts)}</div>'
        if facts else ""
    )

    reasons_html = "".join(_reason_block(reason) for reason in employee["reasons"])

    return f"""
    <div style="border:1px solid {BORDER};border-radius:6px;padding:16px 20px;margin-bottom:14px;">
      <div style="font-size:15px;font-weight:bold;color:{TEXT_PRIMARY};font-family:{FONT_STACK};">
        {esc(employee['employee_name'])}
        <span style="color:{TEXT_SECONDARY};font-weight:normal;">({esc(employee.get('designation', ''))})</span>
        <span style="background-color:{BRAND_PRIMARY_SOFT};color:{BRAND_PRIMARY_DARK};font-size:11px;
                     font-weight:bold;padding:2px 8px;border-radius:10px;margin-left:8px;">FLAGGED</span>
      </div>
      {facts_html}
      {reasons_html}
    </div>
    """


def render_html(recipient_label: str, generated_at: str, employees: list[dict]) -> str:
    """Full HTML document for one recipient's consolidated notification --
    header (with logo), summary card, one card per employee (see
    module docstring -- employee-first, never one card per module), and
    footer. `employees` is a pre-sorted, pre-merged list from
    app/work_distribution_notification_service.py's build_notification_batch();
    this function only renders it."""
    summary_cells = [
        ("Recipient", recipient_label),
        ("Employees Flagged", str(len(employees))),
        ("Generated", generated_at),
    ]

    cards_html = "".join(_employee_card(employee) for employee in employees)

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
                  <div style="color:{BRAND_PRIMARY_SOFT};font-size:14px;margin-top:2px;font-family:{FONT_STACK};">Work Distribution Review Notification</div>
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
                The following employees in your reporting line have been flagged by Saffron Automation's
                Work Distribution module for managerial review.
              </p>

              {_summary_box(summary_cells)}

              <p style="font-size:13px;color:{TEXT_SECONDARY};background-color:{SURFACE};
                        border-left:3px solid {BRAND_PRIMARY};padding:10px 14px;line-height:1.5;
                        margin:0 0 8px 0;font-family:{FONT_STACK};">
                Each entry below reflects an employee who did not meet the current coverage or
                joint-working targets for their designation. Please review the details and take
                appropriate action.
              </p>

              <div class="sv-table-wrap">
              {cards_html}
              </div>

              <p style="font-size:13px;color:{TEXT_PRIMARY};line-height:1.5;margin:8px 0 0 0;font-family:{FONT_STACK};">
                Please review these findings and take appropriate action if necessary.
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


def render_text(recipient_label: str, generated_at: str, employees: list[dict]) -> str:
    """Plain-text companion for the multipart/alternative fallback --
    employee-first, mirroring render_html's own card-per-employee,
    reason-per-failed-module structure."""
    lines = [
        f"Dear {recipient_label},",
        "",
        "The following employees in your reporting line have been flagged by Saffron Automation's "
        "Work Distribution module for managerial review.",
        "",
        f"Recipient: {recipient_label}",
        f"Employees Flagged: {len(employees)}",
        f"Generated: {generated_at}",
        "",
    ]

    for e in employees:
        lines.append(f"{e['employee_name']} ({e.get('designation', '')})")
        facts = " · ".join(filter(None, [
            f"Code: {e['employee_code']}" if e.get("employee_code") else "",
            f"Division: {e['division']}" if e.get("division") else "",
            f"HQ: {e['hq']}" if e.get("hq") else "",
        ]))
        if facts:
            lines.append(f"  {facts}")
        for r in e["reasons"]:
            module_line = r["module"] + (f" — {r['analysis_period']}" if r.get("analysis_period") else "")
            lines.append(f"  - {module_line}: {r['reason']}")
            if r.get("actual_values"):
                lines.append(f"      {_actual_values_line(r['actual_values'], escape=False)}")
            if r.get("required_threshold"):
                lines.append(f"      Required: {r['required_threshold']}")
            monthly_trend = r.get("monthly_trend")
            if monthly_trend and monthly_trend.get("months"):
                values = monthly_trend.get("values", {})
                trend_text = ", ".join(f"{m}={values.get(m) or '—'}" for m in monthly_trend["months"])
                lines.append(f"      Monthly Trend: {trend_text}")
            if r.get("recommended_action"):
                lines.append(f"      Recommended: {r['recommended_action']}")
        lines.append("")

    lines.extend([
        "Please review these findings and take appropriate action if necessary.",
        "",
        "Saffron Automation",
        "Saffron Formulations",
        f"Generated on {generated_at}",
        "This is an automated notification generated by the Saffron Automation System.",
    ])
    return "\n".join(lines)
