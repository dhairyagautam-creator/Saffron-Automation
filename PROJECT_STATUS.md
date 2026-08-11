# Saffron Automation — Project Status

**This folder (`C:\Users\Hp\OneDrive\Desktop\Saffron Automation v2.1 - Development`, renamed from
`2.0 dev` on 2026-08-05) is the canonical, active Development project.**
If you're a fresh Claude session reading this: read `PROJECT_CONTEXT.md` in this same root first, and
don't go searching other Desktop folders named "Saffron Automation ..." — they are older, stale
snapshots. See "Known lookalike folders" below.

Last audited: 2026-08-05.

## Current modules

Registered in `ui/main_window.py`, gated per role by `app/permissions.py` (except where noted):

| Module | Entry point | Permission-gated? |
|---|---|---|
| Path Validator | `ui/path_validator_module.py` | Yes (`can_access_employee_module`) |
| Inventory Monitoring | `ui/inventory_module.py` | Yes (`can_access_inventory_module`) |
| Payment Analytics | `ui/payment_analytics_module.py` | Yes (`can_access_payments_module`) |
| **Work Distribution** | `ui/work_distribution_module.py` | **No — see Outstanding TODOs** |
| User Management | `ui/user_management_page.py` | Yes (`can_access_user_management`) |

### Work Distribution (detail)

Two independent engines, same uploaded data/parser/hierarchy, sharing the "RGD Coverage" vs.
"Manager Work Allocation" tab split on Findings/Employee Details:

- **RGD Coverage** — `app/work_distribution_service.py`, `app/work_distribution_parser.py`. Doctor-coverage
  findings, unchanged by the 2026-08-05 rolling-window redesign (it was never row-per-month in the first
  place).
- **Manager Work Allocation — ABM** — `app/manager_work_allocation_service.py`. Rolling six-month
  architecture: groups by (ABM, BM) pair, keeps the newest 6 distinct months by month *value* (not upload
  order/filename), evaluates the **average** joint days per BM against a configurable threshold
  (`app/manager_work_allocation_parameters_service.py`).
- **Manager Work Allocation — RBM** — `app/manager_work_allocation_rbm_service.py`. **Verified at parity
  with ABM as of today (2026-08-05)** — both call the exact same
  `app/manager_work_allocation_shared.py` helpers (`merge_same_month_duplicates`, `sync_rolling_window`,
  `parse_month`/`month_sort_key`) rather than duplicating that logic. Only the business rule differs by
  design: RBM sums joint days per BM ("worked with at least once" = covered) and flags via a
  BM-count-tiered missed-BM threshold, instead of ABM's rolling average vs. a flat threshold.
- **Employee Details** — `ui/work_distribution_employee_details_page.py` renders both engines through one
  shared dynamic-column monthly-trend table (`_build_monthly_trend_card`), columns built fresh from
  whatever months are currently retained in the rolling window — not a fixed tuple. ABM shows an "Average"
  summary column; RBM shows "Total". Confirmed structurally consistent between the two.
- **Smart rolling window**: entirely value-driven off the Month column
  (`manager_work_allocation_shared.compute_retained_month_keys`) — no manual month selection, no filename
  dependence, for both engines.

Full file list (all 17 confirmed referenced, all imports verified clean via `.venv` Python):

```
app/manager_work_allocation_parameters_service.py
app/manager_work_allocation_parser.py
app/manager_work_allocation_rbm_service.py
app/manager_work_allocation_service.py
app/manager_work_allocation_shared.py
app/work_distribution_email_settings_service.py
app/work_distribution_notification_service.py
app/work_distribution_parameters_service.py
app/work_distribution_parser.py
app/work_distribution_service.py
ui/work_distribution_dashboard_page.py
ui/work_distribution_email_center_page.py
ui/work_distribution_employee_details_page.py
ui/work_distribution_findings_page.py
ui/work_distribution_module.py
ui/work_distribution_settings_page.py
ui/work_distribution_upload_page.py
```

No duplicate/orphaned implementation was found anywhere else in `app/`/`ui/` (checked for a second
rolling-window or ABM/RBM engine hiding under a different filename — none exists).

## Version metadata (as found — needs attention, see TODOs)

- `APP_VERSION = "2.0.0"` (`app/version.py`)
- `CHANNEL = "production"` — **this looks wrong for the active dev tree**, see TODOs
- `BUILD_DATE = "2026-07-27"` — predates the 2026-08-05 Work Distribution rolling-window redesign
- `DESCRIPTION` still lists only "Path Validator, Inventory Monitoring, Payment Analytics" — omits Work
  Distribution and User Management

## Folder to use

**`C:\Users\Hp\OneDrive\Desktop\Saffron Automation v2.1 - Development`** — always. Do all development,
packaging, and publishing here.

### Known lookalike folders (do NOT use — reference only if explicitly asked)

- `Archive\Saffron Automation v2.0 - Development` — stalled ~2026-07-27, no Work Distribution module.
  Archived 2026-08-05.
- `Saffron Automation - Main` — left in place deliberately (not archived). This is what's currently
  packaged and installed at `C:\Users\Hp\AppData\Local\Programs\Saffron Automation` (built 2026-07-29).
  Also has no Work Distribution module. **The currently-installed/running exe predates Work Distribution
  entirely.**
- `Saffron Automation v1.0 - Stable`, both `PRE-2.1*-BACKUP-*` folders, `Saffron Employee Detector`
  (an older/smaller checkout), `Saffron Inventory`, `Downloads\Saffron Inventory Management`.

## Packaging workflow

- `Saffron Automation.spec` (PyInstaller `--onedir` spec) + `build_exe.ps1` at project root.
- Installer: `installer/saffron_validator.iss` (Inno Setup) — see `UPDATER_README.md`.
- **This project has never been packaged** — no `dist/`/`build/` output exists here yet. The
  exe currently installed on this machine was built from the older `Saffron Automation - Main` tree and
  does not contain Work Distribution.

## Outstanding TODOs (found during this audit)

1. **Work Distribution has no permission gate.** `app/permissions.py` has no `MODULE_WORK_DISTRIBUTION`
   constant or `can_access_work_distribution()` function, and `ui/main_window.py`'s
   `_MODULE_PERMISSION_CHECKS` doesn't list "Work Distribution" — so any signed-in user can open it
   regardless of role, and it's silently excluded from `permissions.log_accessible_modules()`'s reporting.
   Needs a decision: is this intentional for now (feature still being finished) or should it be gated
   before anyone else uses this build?
2. **Version metadata is stale relative to the code.** `CHANNEL = "production"` on what is actually the
   active dev tree means `ui/main_window.py`'s dev-mode title-bar treatment won't show; `BUILD_DATE` and
   `DESCRIPTION` don't reflect Work Distribution's existence.
3. **Never packaged.** No build has been produced from this tree yet — the currently-installed exe on this
   machine is from a different, older project and will not show Work Distribution at all if launched.
