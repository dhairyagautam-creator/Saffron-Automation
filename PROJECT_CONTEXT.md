# Saffron Automation

This is the ACTIVE development project.

**Root:** `C:\Users\Hp\OneDrive\Desktop\Saffron Automation v2.1 - Development`

Renamed from `2.0 dev` on 2026-08-05, specifically because a folder literally named "2.0 dev" sitting
next to "Saffron Automation v2.0 - Development" caused a prior session to search the wrong project and
spend a long time concluding a fully-built feature didn't exist. Don't repeat that: **ignore every other
Saffron folder on this Desktop** unless the user explicitly asks you to reference one.

For the full audit trail of how modules/files were verified, see `PROJECT_STATUS.md` in this same root.

## Other Saffron folders (reference only, never modify unless explicitly asked)

- `Saffron Automation - Main` — the OLD project currently packaged and installed at
  `C:\Users\Hp\AppData\Local\Programs\Saffron Automation`. No Work Distribution module. Left in place
  deliberately (not archived) since it's the current production reference point.
- `Archive\Saffron Automation v2.0 - Development` — old, stalled dev snapshot, archived 2026-08-05.
- `Saffron Automation v1.0 - Stable`, both `PRE-2.1*-BACKUP-*` folders, `Saffron Employee Detector`
  (an older/smaller checkout), `Saffron Inventory`, `Downloads\Saffron Inventory Management`.

## Modules

Registered in `ui/main_window.py`, gated per role by `app/permissions.py` **except Work Distribution,
which currently has no permission gate at all** (see Outstanding TODOs in `PROJECT_STATUS.md`):

- Path Validator
- Inventory Monitoring
- Payment Analytics
- Work Distribution
- User Management

### Work Distribution

Two independent engines, same uploaded report data, same parser, same hierarchy dataset:

**1. RGD Coverage** — `app/work_distribution_service.py` + `app/work_distribution_parser.py`. Doctor-level
coverage findings, itself split by which of two independent Category-style columns a doctor row matches:
- **BM analysis** — doctors whose "Category" column is "B-RGD" AND whose "BM" column names this BM.
  Flags on Total Calls / Missed Doctor % / Poor Coverage % vs. configurable thresholds.
- **ABM analysis** — doctors whose "ABM RGD" column is "A-RGD" AND whose "ABM" column names this ABM.
  Flags on raw Missed Doctors / Doctors-with-<2-Visits counts vs. configurable thresholds.
- A doctor can legitimately count toward BOTH a BM's book and an ABM's book (two independent columns on
  the same row) — this is correct, not a double-count bug.
- Full-replacement architecture: an upload is a complete monthly snapshot; every existing doctor/finding
  row is deleted and rebuilt from that upload. NOT on the rolling six-month window (never was
  row-per-month in the first place).

**2. Manager Work Allocation** — `app/manager_work_allocation_service.py` (ABM) +
`app/manager_work_allocation_rbm_service.py` (RBM) + `app/manager_work_allocation_shared.py` (shared
rolling-window logic). Two engines, same rolling six-month architecture (**verified at parity 2026-08-05**
— see `PROJECT_STATUS.md` for the full verification):
- Shared rolling window logic — `manager_work_allocation_shared.py`'s `merge_same_month_duplicates`
  (collapses a genuine same-pair-same-month duplicate by summing; different months are NEVER merged) and
  `sync_rolling_window` (the one place either engine writes to `ManagerWorkAllocationRecord`: upserts new
  monthly records, then trims to the newest 6 distinct months by month *value* — never by upload order or
  filename).
- ABM rolling six-month engine — averages joint days per BM across the retained window, flags a BM below
  a configurable threshold, flags the ABM if any BM fails.
- RBM rolling six-month engine — sums joint days per BM across the retained window ("worked with at least
  once" = covered), flags via a BM-count-tiered missed-BM threshold (more BMs = more tolerance).
- Employee Details page (`ui/work_distribution_employee_details_page.py`) renders both engines through one
  shared dynamic-column monthly-trend table — columns built fresh from whichever months are currently
  retained, never a fixed tuple.
- Findings page — `ui/work_distribution_findings_page.py`.
- Settings — `ui/work_distribution_settings_page.py` + `app/manager_work_allocation_parameters_service.py`
  (ABM threshold, RBM flag tiers) + `app/work_distribution_parameters_service.py` (RGD Coverage
  thresholds).
- Hierarchy-based email routing — **designed but NOT wired to a Send button yet**
  (`app/work_distribution_notification_service.py` is explicitly "ARCHITECTURE ONLY" per its own
  docstring). Rules are defined (flagged BM → notify ABM+RBM; flagged ABM → notify RBM+SM+AGM+GM) and
  reuse Path Validator's existing hierarchy dataset (`app.hierarchy_parser`/`app.hierarchy_service`), but
  actual sending isn't implemented. There's a known, documented integration gap around name-matching
  between a Work Distribution finding's `employee_name` and the hierarchy dataset's own — verify this
  before ever wiring a real Send button.
- Export — `app/table_export_service.py` (shared export helper used across modules, not Work-Distribution-
  specific) — confirm current export coverage for Work Distribution's own tables before assuming it's
  complete.

## Rules for working in this project

- Always modify **this** project (`Saffron Automation v2.1 - Development`) only.
- Never reference `Saffron Automation - Main` or any archived/backup folder unless the user specifically
  asks you to compare against it.
- Before concluding any feature "doesn't exist" or "was never wired in," check this project directly —
  don't trust an assumption carried over from a different folder or an earlier stale context.
- See `PROJECT_STATUS.md` for the current known-gaps list (permission gating, stale version metadata,
  never packaged) before packaging or publishing anything.
