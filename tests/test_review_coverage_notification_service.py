"""Tests for the Coverage Summary automated-email workflow
(app.review_coverage_notification_service) -- BM-wise file grouping into
ABM-wise emails, hierarchy-driven recipient resolution, and the send
pipeline. Hierarchy lookups (find_by_employee_code/find_by_employee_name)
are monkeypatched to a fixed in-memory map -- the DOJ-eligibility work
already exercises the REAL employee_hierarchy read/write path end to end
(see test_hierarchy_parser_doj.py), so these tests focus on this module's
own grouping/resolution logic in isolation, mirroring
test_manager_work_allocation_doj.py's own "stub the DOJ lookup, test the
grouping" philosophy.

build_notification_batch() is exercised with a directly-injected
`bm_files` list (bypassing generate_coverage_summary_bm_files() and any
disk I/O) -- see that function's own docstring for why this parameter
exists.
"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.review_coverage_notification_service as notif
from database.connection import Base


def _hierarchy_row(code, name, email, abm_code=None, abm_name=None):
    return {
        "employee_code": code, "employee_name": name, "email": email,
        "abm_code": abm_code, "abm_name": abm_name,
    }


def _use_hierarchy(monkeypatch, rows_by_code: dict, rows_by_name: dict | None = None):
    rows_by_name = rows_by_name or {}
    monkeypatch.setattr(notif, "find_by_employee_code", lambda code: rows_by_code.get(code))
    monkeypatch.setattr(notif, "find_by_employee_name", lambda name: rows_by_name.get(name, []))


def _bm_file(code, name, path="/tmp/x.xlsx"):
    return {"emp_code": code, "name": name, "file_path": path}


# --- ABM grouping ------------------------------------------------------

def test_abm_with_four_bms_one_email_four_attachments(monkeypatch):
    hierarchy = {
        "A1": _hierarchy_row("A1", "Amit ABM", "amit@x.com"),
        "B1": _hierarchy_row("B1", "BM One", "b1@x.com", abm_code="A1"),
        "B2": _hierarchy_row("B2", "BM Two", "b2@x.com", abm_code="A1"),
        "B3": _hierarchy_row("B3", "BM Three", "b3@x.com", abm_code="A1"),
        "B4": _hierarchy_row("B4", "BM Four", "b4@x.com", abm_code="A1"),
    }
    _use_hierarchy(monkeypatch, hierarchy)
    bm_files = [_bm_file(c, hierarchy[c]["employee_name"]) for c in ("B1", "B2", "B3", "B4")]

    drafts = notif.build_notification_batch("Xandra", bm_files=bm_files)

    assert len(drafts) == 1
    assert drafts[0]["recipient_email"] == "amit@x.com"
    assert len(drafts[0]["file_paths"]) == 4
    assert len(drafts[0]["bm_names"]) == 4


def test_abm_with_three_bms_one_email_three_attachments(monkeypatch):
    hierarchy = {
        "A1": _hierarchy_row("A1", "Amit ABM", "amit@x.com"),
        "B1": _hierarchy_row("B1", "BM One", "b1@x.com", abm_code="A1"),
        "B2": _hierarchy_row("B2", "BM Two", "b2@x.com", abm_code="A1"),
        "B3": _hierarchy_row("B3", "BM Three", "b3@x.com", abm_code="A1"),
    }
    _use_hierarchy(monkeypatch, hierarchy)
    bm_files = [_bm_file(c, hierarchy[c]["employee_name"]) for c in ("B1", "B2", "B3")]

    drafts = notif.build_notification_batch("Xandra", bm_files=bm_files)

    assert len(drafts) == 1
    assert len(drafts[0]["file_paths"]) == 3


def test_abm_with_one_bm_one_email_one_attachment(monkeypatch):
    hierarchy = {
        "A1": _hierarchy_row("A1", "Amit ABM", "amit@x.com"),
        "B1": _hierarchy_row("B1", "BM One", "b1@x.com", abm_code="A1"),
    }
    _use_hierarchy(monkeypatch, hierarchy)
    bm_files = [_bm_file("B1", "BM One")]

    drafts = notif.build_notification_batch("Xandra", bm_files=bm_files)

    assert len(drafts) == 1
    assert len(drafts[0]["file_paths"]) == 1


def test_cross_abm_isolation_never_grouped_together(monkeypatch):
    hierarchy = {
        "A1": _hierarchy_row("A1", "Amit ABM", "amit@x.com"),
        "A2": _hierarchy_row("A2", "Anjali ABM", "anjali@x.com"),
        "B1": _hierarchy_row("B1", "BM One", "b1@x.com", abm_code="A1"),
        "B2": _hierarchy_row("B2", "BM Two", "b2@x.com", abm_code="A2"),
    }
    _use_hierarchy(monkeypatch, hierarchy)
    bm_files = [_bm_file("B1", "BM One"), _bm_file("B2", "BM Two")]

    drafts = notif.build_notification_batch("Xandra", bm_files=bm_files)

    assert len(drafts) == 2
    by_recipient = {d["recipient_email"]: d for d in drafts}
    assert by_recipient["amit@x.com"]["bm_names"] == ["BM One"]
    assert by_recipient["anjali@x.com"]["bm_names"] == ["BM Two"]
    # No attachment ever appears in the wrong ABM's draft.
    assert "BM Two" not in by_recipient["amit@x.com"]["bm_names"]
    assert "BM One" not in by_recipient["anjali@x.com"]["bm_names"]


def test_recipient_correctness_matches_hierarchy(monkeypatch):
    hierarchy = {
        "A1": _hierarchy_row("A1", "Amit ABM", "amit@x.com"),
        "B1": _hierarchy_row("B1", "BM One", "b1@x.com", abm_code="A1"),
    }
    _use_hierarchy(monkeypatch, hierarchy)
    drafts = notif.build_notification_batch("Xandra", bm_files=[_bm_file("B1", "BM One")])
    assert drafts[0]["recipient_name"] == "Amit ABM"
    assert drafts[0]["recipient_email"] == "amit@x.com"


def test_no_attachment_duplicated_across_drafts(monkeypatch):
    hierarchy = {
        "A1": _hierarchy_row("A1", "Amit ABM", "amit@x.com"),
        "B1": _hierarchy_row("B1", "BM One", "b1@x.com", abm_code="A1"),
    }
    _use_hierarchy(monkeypatch, hierarchy)
    drafts = notif.build_notification_batch("Xandra", bm_files=[_bm_file("B1", "BM One", path="/tmp/b1.xlsx")])
    all_paths = [p for d in drafts for p in d["file_paths"]]
    assert all_paths == ["/tmp/b1.xlsx"]  # exactly once, nowhere else


# --- Edge cases ----------------------------------------------------------

def test_bm_missing_from_hierarchy_is_unresolved_not_dropped_silently(monkeypatch):
    """Unresolved -- excluded from every draft, but via the logged
    "unresolved" path (see the WARNING lines in this test's own captured
    stderr), never a crash or a silent mis-route. loguru writes straight
    to stderr in this app rather than through stdlib `logging`, so
    pytest's `caplog` fixture can't observe it -- the behavioral outcome
    (no draft produced) is what this test asserts."""
    _use_hierarchy(monkeypatch, {})  # nobody found
    drafts = notif.build_notification_batch("Xandra", bm_files=[_bm_file("B1", "Ghost BM")])
    assert drafts == []


def test_vacant_or_missing_abm_mapping_is_unresolved(monkeypatch):
    """BM's own abm_code is missing entirely (e.g. hierarchy_parser never
    populated it -- vacant ABM slot) -- BM must be excluded, not
    misrouted anywhere."""
    hierarchy = {"B1": _hierarchy_row("B1", "BM One", "b1@x.com", abm_code=None, abm_name=None)}
    _use_hierarchy(monkeypatch, hierarchy)
    drafts = notif.build_notification_batch("Xandra", bm_files=[_bm_file("B1", "BM One")])
    assert drafts == []


def test_abm_with_no_email_is_unresolved(monkeypatch):
    hierarchy = {
        "A1": _hierarchy_row("A1", "Amit ABM", ""),  # on file, but no email
        "B1": _hierarchy_row("B1", "BM One", "b1@x.com", abm_code="A1"),
    }
    _use_hierarchy(monkeypatch, hierarchy)
    drafts = notif.build_notification_batch("Xandra", bm_files=[_bm_file("B1", "BM One")])
    assert drafts == []


def test_duplicate_hierarchy_records_uses_first_match(monkeypatch):
    """BM's ABM is resolved by NAME (no abm_code on file) and multiple
    hierarchy rows share that name -- first match wins, same convention
    as app.hierarchy_parser.find_by_employee_name's own documented
    contract."""
    first = _hierarchy_row("A1", "Amit ABM", "first@x.com")
    second = _hierarchy_row("A2", "Amit ABM", "second@x.com")
    hierarchy_by_code = {"B1": _hierarchy_row("B1", "BM One", "b1@x.com", abm_code=None, abm_name="Amit ABM")}
    _use_hierarchy(monkeypatch, hierarchy_by_code, rows_by_name={"Amit ABM": [first, second]})
    drafts = notif.build_notification_batch("Xandra", bm_files=[_bm_file("B1", "BM One")])
    assert drafts[0]["recipient_email"] == "first@x.com"


def test_bm_with_no_coverage_data_never_reaches_this_module(monkeypatch):
    """An empty bm_files list (e.g. the roster had nobody applicable) ->
    no drafts, no error."""
    _use_hierarchy(monkeypatch, {})
    assert notif.build_notification_batch("Xandra", bm_files=[]) == []


# --- Send pipeline ---------------------------------------------------------

def _in_memory_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_send_notification_batch_attaches_correct_files_and_logs(monkeypatch, tmp_path):
    monkeypatch.setattr("database.connection._ConfigSession", _in_memory_session_factory())

    sent = []

    def fake_open_connection(sender_email, app_password):
        return object(), sender_email

    def fake_send_via_connection(connection, sender_email, to_address, subject, html_body, text_body=None, attachments=None):
        sent.append({"to": to_address, "attachments": attachments})

    monkeypatch.setattr(notif, "get_settings", lambda: {"sender_email": "s@x.com", "app_password": "pw"})
    monkeypatch.setattr(notif, "open_smtp_connection", fake_open_connection)
    monkeypatch.setattr(notif, "send_via_connection", fake_send_via_connection)

    file1 = tmp_path / "Coverage Summary - BM One.xlsx"
    file1.write_bytes(b"fake-xlsx-bytes")

    drafts = [{
        "recipient_name": "Amit ABM", "recipient_email": "amit@x.com", "division": "Xandra",
        "bm_names": ["BM One"], "file_paths": [str(file1)],
        "subject": "Saffron Automation - Coverage Summary (Xandra, 1 BM)",
        "body": "<html></html>", "text_body": "text", "status": notif.STATUS_DRAFT,
    }]

    result = notif.send_notification_batch(drafts)

    assert result["sent_count"] == 1
    assert result["failed_count"] == 0
    assert len(sent) == 1
    assert sent[0]["to"] == "amit@x.com"
    [(filename, data)] = sent[0]["attachments"]
    assert filename == "Coverage Summary - BM One.xlsx"
    assert data == b"fake-xlsx-bytes"

    logged = notif.get_recent_notifications()
    assert len(logged) == 1
    assert logged[0]["status"] == notif.STATUS_SENT
    assert logged[0]["bm_names"] == "BM One"


def test_send_notification_batch_records_failure_without_stopping_batch(monkeypatch, tmp_path):
    monkeypatch.setattr("database.connection._ConfigSession", _in_memory_session_factory())
    monkeypatch.setattr(notif, "get_settings", lambda: {"sender_email": "s@x.com", "app_password": "pw"})
    monkeypatch.setattr(notif, "open_smtp_connection", lambda sender_email, app_password: (object(), sender_email))

    def failing_send(*args, **kwargs):
        raise RuntimeError("smtp boom")

    monkeypatch.setattr(notif, "send_via_connection", failing_send)

    file1 = tmp_path / "Coverage Summary - BM One.xlsx"
    file1.write_bytes(b"data")
    drafts = [{
        "recipient_name": "Amit ABM", "recipient_email": "amit@x.com", "division": "Xandra",
        "bm_names": ["BM One"], "file_paths": [str(file1)],
        "subject": "s", "body": "b", "text_body": "t", "status": notif.STATUS_DRAFT,
    }]

    result = notif.send_notification_batch(drafts)
    assert result["sent_count"] == 0
    assert result["failed_count"] == 1
    logged = notif.get_recent_notifications()
    assert logged[0]["status"] == notif.STATUS_FAILED
