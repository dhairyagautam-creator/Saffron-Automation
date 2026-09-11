"""Item 3 verification (docs/INVENTORY_SYNC_CONTEXT.md follow-up): does the
Sales Report's retained/locked state actually survive an app restart?

Proven here with two GENUINELY SEPARATE OS processes sharing only a real,
file-backed SQLite database and retained-file directory (via
SAFFRON_DATA_DIR, see app/config.py) -- which is exactly what closing the
app and relaunching it does. An in-memory-DB pytest fixture (":memory:",
used by every other test in this module) could never prove this: it
doesn't survive a process exiting at all, so a false pass there would
prove nothing about the actual concern -- disk durability, not "does the
Python object still exist."

Process 1 does a real local upload via the exact function the real UI
calls (app.inventory_sync_service.upload_and_sync), then exits completely.
Process 2 -- a brand new interpreter, never having called upload itself --
reads the slot state and InventoryThreshold table back and must find the
data intact.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_UPLOAD_SCRIPT = """
import sys
sys.path.insert(0, r"{project_root}")
from database.connection import init_db
init_db()
from app.inventory_sync_service import upload_and_sync
from app.inventory_upload_service import SALES_REPORT_SLOT
result = upload_and_sync(SALES_REPORT_SLOT, r"{sales_csv}")
assert result["success"], result
print("UPLOAD_OK")
"""

_VERIFY_SCRIPT = """
import sys
sys.path.insert(0, r"{project_root}")
import json
from database.connection import init_db, get_config_session
init_db()
from app.inventory_upload_service import SALES_REPORT_SLOT, get_slot_state
from database.models import InventoryThreshold

state = get_slot_state(SALES_REPORT_SLOT)
session = get_config_session()
try:
    threshold_count = session.query(InventoryThreshold).count()
    threshold_row = session.query(InventoryThreshold).first()
finally:
    session.close()

# Also construct the REAL widget fresh in this brand-new process -- exactly
# what happens when a relaunched app opens the Uploads page -- and read
# back what it actually rendered, not just the underlying DB state.
import customtkinter as ctk
from ui.sales_upload_page import SalesUploadPage

root = ctk.CTk()
root.withdraw()
widget = SalesUploadPage(root)
root.update()
widget_file_label_text = widget.file_label.cget("text")
widget_status_label_text = widget.status_label.cget("text")
widget_remove_button_state = widget.remove_button.cget("state")
widget.destroy()
root.destroy()

print(json.dumps({{
    "uploaded": state["uploaded"],
    "filename": state["filename"],
    "file_path_exists": bool(state["file_path"]) and __import__("os").path.exists(state["file_path"]),
    "uploaded_at_is_none": state["uploaded_at"] is None,
    "thresholds_generated_at_is_none": state["thresholds_generated_at"] is None,
    "threshold_count": threshold_count,
    "threshold_item": threshold_row.item_name if threshold_row else None,
    "widget_file_label_text": widget_file_label_text,
    "widget_status_label_text": widget_status_label_text,
    "widget_remove_button_state": str(widget_remove_button_state),
}}))
"""


def _run(script: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT), timeout=60,
    )


def test_sales_report_state_survives_a_real_process_restart(tmp_path):
    data_dir = tmp_path / "restart_test_data"
    sales_csv = PROJECT_ROOT / "manual_test_fixtures" / "Sales_Report.csv"
    assert sales_csv.exists(), f"fixture missing at {sales_csv}"

    env = dict(os.environ)
    env["SAFFRON_DATA_DIR"] = str(data_dir)

    # "Close the app": process 1 uploads, then exits completely -- nothing
    # in its memory can leak into process 2.
    upload_result = _run(_UPLOAD_SCRIPT.format(project_root=PROJECT_ROOT, sales_csv=sales_csv), env)
    assert "UPLOAD_OK" in upload_result.stdout, f"stdout={upload_result.stdout!r} stderr={upload_result.stderr!r}"

    # "Relaunch the app": a BRAND NEW process, same SAFFRON_DATA_DIR, reads
    # state back without ever calling upload_and_sync itself.
    verify_result = _run(_VERIFY_SCRIPT.format(project_root=PROJECT_ROOT), env)
    assert verify_result.returncode == 0, f"stdout={verify_result.stdout!r} stderr={verify_result.stderr!r}"
    state = json.loads(verify_result.stdout.strip().splitlines()[-1])

    assert state["uploaded"] is True
    assert state["filename"] == "Sales_Report.csv"
    assert state["file_path_exists"] is True
    assert state["uploaded_at_is_none"] is False
    assert state["thresholds_generated_at_is_none"] is False
    assert state["threshold_count"] == 1
    assert state["threshold_item"] == "Widget 30-Pack"

    # The real widget, freshly constructed in the "relaunched" process,
    # rendered the correct persisted state -- not "No file selected".
    assert state["widget_file_label_text"] == "Sales_Report.csv"
    assert "validated successfully" in state["widget_status_label_text"]
    assert "1 threshold(s) generated" in state["widget_status_label_text"]
    assert state["widget_remove_button_state"] == "normal"
