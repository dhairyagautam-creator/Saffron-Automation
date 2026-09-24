"""Trailing blank columns after the last warehouse group (a common Excel
artifact -- the saved used-range extends past the real data with no header,
label, or values) must not reject an otherwise-valid new-format Inventory
Report. Regression test for a real rejected upload: the last branch's
forward-filled name absorbed unrelated blank columns and was flagged
malformed even though its own two real columns (Closing, Transit) were
fine -- see app.excel_validation._detect_new_format_groups.
"""

import openpyxl

from app.excel_validation import validate_new_format_inventory_report

HEADER = ["Product", "CWH", "AHMEDABAD", "RAIPUR", None]
FIELDS = [None, "Closing", "Closing", "Closing", "Transit"]
DATA = [["Widget", 10, 20, 5, 1]]


def _write_workbook(path, header, fields, data, trailing_blank_cols=0):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header + [None] * trailing_blank_cols)
    ws.append(fields + [None] * trailing_blank_cols)
    for row in data:
        ws.append(row + [None] * trailing_blank_cols)
    wb.save(path)


def test_trailing_blank_columns_do_not_reject_valid_file(tmp_path):
    path = tmp_path / "stock.xlsx"
    _write_workbook(path, HEADER, FIELDS, DATA, trailing_blank_cols=5)

    result = validate_new_format_inventory_report(str(path))

    assert result["success"] is True, result["error"]
    assert len(result["df"]) == 3  # CWH, AHMEDABAD, RAIPUR rows for the one product


def test_without_trailing_blank_columns_still_works(tmp_path):
    path = tmp_path / "stock.xlsx"
    _write_workbook(path, HEADER, FIELDS, DATA, trailing_blank_cols=0)

    result = validate_new_format_inventory_report(str(path))

    assert result["success"] is True, result["error"]
    assert len(result["df"]) == 3


def test_genuinely_malformed_group_is_still_rejected(tmp_path):
    """A real gap -- a named branch column with no Closing/Transit label at
    all under it -- must still be rejected; the fix only ignores columns
    that are blank in BOTH the identity row and the field row."""
    path = tmp_path / "stock.xlsx"
    header = ["Product", "CWH", "BROKEN"]
    fields = [None, "Closing", "NotALabel"]
    _write_workbook(path, header, fields, [["Widget", 10, 5]])

    result = validate_new_format_inventory_report(str(path))

    assert result["success"] is False
    assert "Could not detect any warehouse column groups" in result["error"]
