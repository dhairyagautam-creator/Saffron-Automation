"""Manual two-identity cross-machine-equivalent test for the Inventory
sync slice (app/inventory_sync_service.py, app/inventory_upload_service.py).

NOT part of the automated test suite (pytest never collects this -- it
requires a real password, typed by a human, and makes real network calls
against the real Supabase project). Delete this file once you're done, or
leave it -- it does nothing on its own.

Why this exists instead of app/auth_service.sign_in(): that function
persists the session to a SINGLE, fixed OS keyring entry
(service="SaffronAutomationV2", account="supabase_session") -- not
namespaced by data directory at all. Using it here would let "Machine B"'s
sign-in silently overwrite "Machine A"'s in that one shared slot, and could
overwrite your REAL saved login on this machine. This script authenticates
in-memory only (client.auth.sign_in_with_password, same call
auth_service.sign_in() itself makes internally) and never calls the
keyring-persistence step -- it touches nothing outside the --data-dir you
give it, plus that one Supabase network round-trip.

Everything else config-derived (DATABASE_PATH, INVENTORY_UPLOADS_DIR,
REVIEW_UPLOADS_DIR, LOGS_DIR, REPORTS_DIR) is redirected under --data-dir
BEFORE any module that binds a path constant from app.config is imported --
two different --data-dir values are then as close to two separate machines
as this one disk can give you: no shared database file, no shared
retention folder, no shared credential-store entry.

Usage (run once per step -- there is no persistent session between runs,
by design; each invocation signs in fresh):

    python sign_in_for_test_inventory_sync.py --data-dir <path> --email <email> --action <action> [options]

Actions:
    status                          Print both slots' state + the real
                                     InventoryThreshold/Replenishment/CwhStock
                                     table contents on THIS data-dir.
    upload-sales --file <path>      Upload+sync a Sales Report (local
                                     process+retain, then push to Storage +
                                     the manifest).
    upload-inventory --file <path>  Upload+sync an Inventory Report.
    check                           Read-only: what's on the manifest that
                                     this data-dir hasn't applied yet.
    pull --slots sales_report inventory_report
                                     Apply pending updates for the given
                                     slot(s) (always processed sales_report
                                     before inventory_report regardless of
                                     the order given here).

You will be prompted for the password interactively (getpass -- not shown
on screen, never passed as a CLI argument, never typed by the assistant).
"""

import argparse
import getpass
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


def _isolate_data_dir(data_dir: Path) -> None:
    """Redirects every config-derived path constant to live under
    `data_dir`, BEFORE any module that binds one of these at import time
    is imported. Must run first, before any other project import."""
    import app.config as cfg

    data_dir.mkdir(parents=True, exist_ok=True)

    cfg.DATABASE_PATH = data_dir / "database" / "saffron_validator.db"
    cfg.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg.INVENTORY_UPLOADS_DIR = data_dir / "inventory_uploads"
    cfg.INVENTORY_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    cfg.REVIEW_UPLOADS_DIR = data_dir / "review_uploads"
    cfg.REVIEW_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOGS_DIR = data_dir / "logs"
    cfg.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    cfg.REPORTS_DIR = data_dir / "reports"
    cfg.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[isolation] DATABASE_PATH        = {cfg.DATABASE_PATH}")
    print(f"[isolation] INVENTORY_UPLOADS_DIR = {cfg.INVENTORY_UPLOADS_DIR}")
    print(f"[isolation] REVIEW_UPLOADS_DIR     = {cfg.REVIEW_UPLOADS_DIR}")


def _sign_in(email: str, password: str) -> None:
    """Authenticates the shared Supabase client in-memory (no keyring
    write -- see module docstring), then loads the real profile row +
    module permissions into app.rbac_state, exactly like the real app does
    after a successful login (app/rbac_service.load_profile_and_permissions),
    minus the session-persistence step."""
    from app import rbac_service
    from app.rbac_state import current_profile
    from app.supabase_client import get_supabase_client

    client = get_supabase_client()
    print(f"[auth] Signing in as {email!r}...")
    response = client.auth.sign_in_with_password({"email": email, "password": password})
    session, user = response.session, response.user
    if session is None or user is None:
        print("[auth] FAILED -- no session/user returned. Wrong password, or account issue.")
        sys.exit(1)
    print(f"[auth] Supabase auth OK -- user id={user.id} email={user.email}")

    result = rbac_service.load_profile_and_permissions(user.id, user.email)
    if not result.success:
        print(f"[auth] Profile load FAILED: {result.error_message}")
        sys.exit(1)

    profile = current_profile()
    print(
        f"[auth] Profile loaded -- is_super_admin={profile.is_super_admin} "
        f"module_keys={sorted(profile.module_keys)} full_name={profile.full_name!r}"
    )


def _printable(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "df"}


def _run_action(args) -> None:
    from database.connection import init_db

    init_db()

    if args.action == "status":
        _print_status()
    elif args.action == "upload-sales":
        from app.inventory_sync_service import upload_and_sync
        from app.inventory_upload_service import SALES_REPORT_SLOT

        result = upload_and_sync(SALES_REPORT_SLOT, args.file)
        print(json.dumps(_printable(result), indent=2, default=str))
    elif args.action == "upload-inventory":
        from app.inventory_sync_service import upload_and_sync
        from app.inventory_upload_service import INVENTORY_REPORT_SLOT

        result = upload_and_sync(INVENTORY_REPORT_SLOT, args.file)
        print(json.dumps(_printable(result), indent=2, default=str))
    elif args.action == "check":
        from app.inventory_sync_service import check_for_updates

        result = check_for_updates()
        print(json.dumps(result, indent=2, default=str))
    elif args.action == "pull":
        from app.inventory_sync_service import apply_pending_updates

        if not args.slots:
            print("--slots is required for 'pull' (e.g. --slots sales_report inventory_report)")
            sys.exit(1)
        result = apply_pending_updates(args.slots)
        print(json.dumps(result, indent=2, default=str))


def _print_status() -> None:
    from app.inventory_sync_service import display_name_for
    from app.inventory_upload_service import get_all_slot_states
    from database.connection import get_config_session, to_local
    from database.models import CwhStock, InventoryReplenishment, InventoryThreshold

    print("\n=== Slot states ===")
    for slot_id, state in get_all_slot_states().items():
        print(f"--- {slot_id} ---")
        print(f"  uploaded: {state['uploaded']}")
        print(f"  filename: {state['filename']}")
        resolved_name = display_name_for(state["uploaded_by"]) if state["uploaded_by"] else None
        print(f"  uploaded_by: {state['uploaded_by']!r} -> resolved name: {resolved_name!r}")
        print(f"  uploaded_at (local): {to_local(state['uploaded_at'])}")
        print(f"  only_on_this_machine: {state['only_on_this_machine']}")
        print(f"  thresholds_generated_at (local): {to_local(state['thresholds_generated_at'])}")

    session = get_config_session()
    try:
        print("\n=== InventoryThreshold ===")
        for row in session.query(InventoryThreshold).order_by(InventoryThreshold.item_name).all():
            print(f"  {row.branch_location} / {row.item_name}: sales={row.previous_month_sales} "
                  f"packed_threshold={row.packed_threshold}")
        print("\n=== InventoryReplenishment ===")
        for row in session.query(InventoryReplenishment).order_by(InventoryReplenishment.item_name).all():
            print(f"  {row.branch_location} / {row.item_name}: closing={row.closing_stock} "
                  f"threshold={row.packed_threshold} deficit={row.stock_deficit} status={row.status}")
        print("\n=== CwhStock ===")
        for row in session.query(CwhStock).order_by(CwhStock.item_name).all():
            print(f"  {row.item_name}: closing={row.closing_stock} status={row.status}")
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="Isolated data directory for this 'machine'.")
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--action", required=True,
        choices=["status", "upload-sales", "upload-inventory", "check", "pull"],
    )
    parser.add_argument("--file", help="File path for upload-sales/upload-inventory.")
    parser.add_argument("--slots", nargs="+", help="Slot ids for 'pull' (sales_report, inventory_report).")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    _isolate_data_dir(data_dir)

    password = getpass.getpass(f"Password for {args.email}: ")
    _sign_in(args.email, password)

    _run_action(args)


if __name__ == "__main__":
    main()
