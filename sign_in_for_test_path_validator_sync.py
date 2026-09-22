"""Manual two-identity cross-machine-equivalent test for the Path
Validator sync slice (app/path_validator_sync_service.py,
app/path_validator_upload_service.py, app/hierarchy_upload_service.py).

Byte-for-byte the same isolation discipline as
sign_in_for_test_work_distribution_sync.py -- see that file's own
docstring (and sign_in_for_test_inventory_sync.py's, the original) for why
this exists instead of app/auth_service.sign_in() and exactly how
--data-dir isolation works. This is the Path Validator equivalent, scoped
to 6 slots.

NOT part of the automated test suite. Requires a real password, typed by a
human, and makes real network calls against the real Supabase project.

Usage (run once per step -- each invocation signs in fresh):

    python sign_in_for_test_path_validator_sync.py --data-dir <path> --email <email> --action <action> [options]

Actions:
    status                       Print all 6 slots' state, the hard
                                  hierarchy gate's current open/closed
                                  state, and this data-dir's real
                                  raw_visits/import_history/active_session
                                  table contents.
    upload-daily-report --division <Onyx|Guardians|Xandra> --file <path>
    upload-hierarchy --division <...> --file <path>
                                  Upload+sync one slot (local retain+
                                  validate, then push to Storage + the
                                  manifest).
    check                        Read-only: what's on the manifest that
                                  this data-dir hasn't applied yet.
    pull --slots <slot_id> [<slot_id> ...]
                                  Apply pending updates for the given
                                  slot(s) (e.g. --slots daily_report_onyx
                                  hierarchy_guardians). A pull that
                                  completes all 3 daily-report divisions
                                  AND has the hierarchy gate open
                                  auto-runs analysis -- see `status`
                                  afterward to confirm.

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
    cfg.PATH_VALIDATOR_UPLOADS_DIR = data_dir / "path_validator_uploads"
    cfg.PATH_VALIDATOR_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    cfg.HIERARCHY_UPLOADS_DIR = data_dir / "hierarchy_uploads"
    cfg.HIERARCHY_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOGS_DIR = data_dir / "logs"
    cfg.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    cfg.REPORTS_DIR = data_dir / "reports"
    cfg.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[isolation] DATABASE_PATH              = {cfg.DATABASE_PATH}")
    print(f"[isolation] PATH_VALIDATOR_UPLOADS_DIR  = {cfg.PATH_VALIDATOR_UPLOADS_DIR}")
    print(f"[isolation] HIERARCHY_UPLOADS_DIR       = {cfg.HIERARCHY_UPLOADS_DIR}")


def _sign_in(email: str, password: str) -> None:
    """Authenticates the shared Supabase client in-memory (no keyring
    write -- see module docstring), then loads the real profile row +
    module permissions, exactly like the real app does after a successful
    login, minus the session-persistence step."""
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
        return

    if args.action in ("upload-daily-report", "upload-hierarchy"):
        if not args.division or not args.file:
            print("--division and --file are required for an upload-* action")
            sys.exit(1)
        from app.path_validator_sync_service import upload_and_sync
        from app.path_validator_upload_service import slot_id_for as report_slot_id_for
        from app.hierarchy_upload_service import slot_id_for as hierarchy_slot_id_for

        slot_id = (
            hierarchy_slot_id_for(args.division)
            if args.action == "upload-hierarchy"
            else report_slot_id_for(args.division)
        )
        result = upload_and_sync(slot_id, args.file)
        print(json.dumps(_printable(result), indent=2, default=str))
    elif args.action == "check":
        from app.path_validator_sync_service import check_for_updates

        result = check_for_updates()
        print(json.dumps(result, indent=2, default=str))
    elif args.action == "pull":
        from app.path_validator_sync_service import apply_pending_updates

        if not args.slots:
            print("--slots is required for 'pull' (e.g. --slots daily_report_onyx hierarchy_guardians)")
            sys.exit(1)
        result = apply_pending_updates(args.slots)
        print(json.dumps(result, indent=2, default=str))


def _print_status() -> None:
    from app.hierarchy_upload_service import get_all_slot_states as get_all_hierarchy_slot_states
    from app.path_validator_sync_service import display_name_for, hierarchy_gate_open
    from app.path_validator_upload_service import get_all_slot_states as get_all_report_slot_states
    from app.session_state import get_active_import
    from database.connection import get_config_session, to_local
    from database.models import ImportHistory

    print("\n=== Daily report slot states (3 divisions) ===")
    for slot_id, state in get_all_report_slot_states().items():
        print(f"--- {slot_id} ---")
        print(f"  uploaded: {state['uploaded']}, filename: {state['filename']}")
        resolved_name = display_name_for(state["uploaded_by"]) if state["uploaded_by"] else None
        print(f"  uploaded_by: {state['uploaded_by']!r} -> resolved name: {resolved_name!r}")
        print(f"  uploaded_at (local): {to_local(state['uploaded_at'])}")
        print(f"  only_on_this_machine: {state['only_on_this_machine']}")

    print("\n=== Hierarchy slot states (module_key=employee_module) ===")
    for slot_id, state in get_all_hierarchy_slot_states("employee_module").items():
        print(f"--- {slot_id} ---")
        print(f"  uploaded: {state['uploaded']}, filename: {state['filename']}")

    print(f"\n=== Hard hierarchy gate ===\n  OPEN (analysis can run)" if hierarchy_gate_open() else "\n=== Hard hierarchy gate ===\n  CLOSED (employee_hierarchy_path_validator has zero rows -- analysis blocked)")

    active = get_active_import()
    print("\n=== Active session / import history ===")
    print(f"  active import: {active.id if active else None} ({active.file_name if active else 'none'})")
    session = get_config_session()
    try:
        for row in session.query(ImportHistory).order_by(ImportHistory.id).all():
            print(
                f"  import_id={row.id}: '{row.file_name}' -- {row.rows_imported} row(s), "
                f"{row.duplicates_removed} duplicate(s), imported_at={to_local(row.imported_at)}"
            )
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="Isolated data directory for this 'machine'.")
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--action", required=True,
        choices=["status", "upload-daily-report", "upload-hierarchy", "check", "pull"],
    )
    parser.add_argument("--division", choices=["Onyx", "Guardians", "Xandra"], help="Division for an upload-* action.")
    parser.add_argument("--file", help="File path for an upload-* action.")
    parser.add_argument("--slots", nargs="+", help="Slot ids for 'pull' (e.g. daily_report_onyx hierarchy_guardians).")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    _isolate_data_dir(data_dir)

    password = getpass.getpass(f"Password for {args.email}: ")
    _sign_in(args.email, password)

    _run_action(args)


if __name__ == "__main__":
    main()
