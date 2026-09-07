"""A row with a missing Invoice Number must be rejected at validation time,
never reach payment_invoices with a NULL invoice_no -- see PaymentInvoice's
own docstring for why: UNIQUE(party_name, invoice_no, month) would be
silently unenforced for such a row (SQLite never treats two NULLs as equal
in a UNIQUE constraint).
"""

import pandas as pd

from app.payment_analytics_service import _parse_invoice_rows

ROW = {
    "Month": "Jan-26", "Party Name": "Acme Pharma", "Type": "Retail",
    "Inv. No.": "INV001", "LR Date": "01-01-2026", "Due Date": "15-01-2026", "Clear Date": "05-01-2026",
}


def _df(**overrides):
    row = {**ROW, **overrides}
    return pd.DataFrame([row])


def test_valid_row_is_accepted():
    invoices, errors = _parse_invoice_rows(_df())
    assert len(invoices) == 1 and not errors
    assert invoices[0]["invoice_no"] == "INV001"


def test_missing_invoice_number_is_rejected():
    invoices, errors = _parse_invoice_rows(_df(**{"Inv. No.": None}))
    assert invoices == []
    assert len(errors) == 1 and errors[0]["reason"] == "Missing Invoice Number"


def test_blank_invoice_number_is_rejected():
    invoices, errors = _parse_invoice_rows(_df(**{"Inv. No.": "   "}))
    assert invoices == []
    assert errors[0]["reason"] == "Missing Invoice Number"


def test_missing_party_name_still_rejected_before_invoice_number_check():
    invoices, errors = _parse_invoice_rows(_df(**{"Party Name": None, "Inv. No.": None}))
    assert invoices == []
    assert errors[0]["reason"] == "Missing Party Name"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Payment invoice validation: all checks passed")
