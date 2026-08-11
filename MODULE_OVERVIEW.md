# Module Overview

Generated 2026-08-05. See `ARCHITECTURE.md` for the shared shell pattern every module follows, and
`DATABASE_SCHEMA.md` for each module's own tables.

## Path Validator

**Purpose:** GPS field-force anomaly detection from daily call reports — flags an employee's day as
suspicious when a large share of their GPS-tagged visits cluster within a small radius of one another
(`rules/same_location.py`).

**Entry point:** `ui/path_validator_module.py` — also owns the app's User Mode / Developer Mode chrome
(amber banner, "Developer Mode" badge, conditional Developer page); this concept is specific to this
module, not app-wide. This is the original "Saffron Validator" application, now embedded as one module
inside the larger shell rather than being its own root window.

**Key pieces:**
- `rules/same_location.py` — the rule engine itself, thresholds from `app/rule_parameters.py`.
- `app/dashboard_service.py` — read-only analytics layer (Employees Analysed/Flagged, Division/State
  summaries, severity buckets) — independently re-derives "who was analyzed" using the same two filters
  the rule engine applies, rather than calling into the rule engine's own internals.
- `app/hospital_service.py` / `app/region_suppression.py` — post-processing suppression filters (a
  flagged cluster near a real hospital, or in a suppressed region, is suppressed from notification but
  stays flagged in the findings themselves).
- `app/notification_service.py` / `app/email_template.py` — per-manager + master rollup emails, hierarchy
  routing via `app/hierarchy_service.py`.
- Cloud sync — `app/import_sync_service.py`, `app/findings_sync_service.py`, `app/email_sync_service.py`,
  `app/organization_data_sync_service.py`.

## Inventory Monitoring

**Purpose:** Upload inventory/sales reports, track replenishment thresholds, monitor stock status across
branches (Thresholds, Replenishment, CWH).

**Entry point:** `ui/inventory_module.py` — same shell shape as Path Validator, minus the Developer Mode
concept (that's Path-Validator-specific chrome, not app-wide) — sidebar is always the plain style, built
once.

**Key pieces:**
- `app/threshold_service.py`, `app/replenishment_service.py`, `app/cwh_service.py` — full-replace per
  upload (an upload is a complete monthly snapshot, not an incremental delta — see
  [[inventory_full_replace_fix]] memory for the 2026-07-29 fix that made this consistent across all three).
- `app/inventory_notification_service.py` + `ui/inventory_automated_emails_page.py` — automated email
  sends with a "Skipped - No Data" status for empty division-filtered reports.
- Cloud sync — `app/inventory_sync_service.py`.

## Payment Analytics

**Purpose:** Upload customer payment reports, track payment behavior, monitor customer risk.

**Entry point:** `ui/payment_analytics_module.py` — its own docstring flags this as **"UI scaffold only —
see the individual page modules for what's still placeholder."** Don't assume every page here is fully
live without checking that page's own file first.

**Key pieces:**
- `app/payment_analytics_service.py`, `app/collections_service.py` — collections/customer-risk logic,
  including its own (older, unrelated) six-month historical rolling concept — **do not confuse this with
  Manager Work Allocation's rolling six-month window**; the two share no code.
- `app/payment_parameters_service.py` — thresholds.
- Cloud sync — `app/payment_sync_service.py`.

## Work Distribution

**Purpose:** Two independent engines over manager/BM working-relationship reports — RGD Coverage (doctor
coverage) and Manager Work Allocation (ABM/RBM joint-working coverage). See `PROJECT_CONTEXT.md` for the
full breakdown (business rules, file list, known gaps) — not duplicated here to avoid the two documents
drifting out of sync.

**Entry point:** `ui/work_distribution_module.py`.

**Notable architectural fact:** this is the only module with **no cloud-sync counterpart at all** (see
`DATABASE_SCHEMA.md`) and **no permission gate** (see `PROJECT_STATUS.md`) — both worth resolving before
this module reaches the same maturity level as the other three.

## User Management

**Purpose:** Manage user accounts and role assignments (who can access which module).

**Entry point:** `ui/user_management_page.py`.

**Key pieces:**
- `app/auth_service.py` — Supabase Auth (sign in/out), session persistence via the OS credential store.
- `app/rbac_service.py` — loads profile/role after sign-in into `app/rbac_state.py`.
- `app/permissions.py` — single source of truth for every `can_access_*` check used elsewhere in the app.
- `app/user_management_service.py`, `app/user_validation.py` — account CRUD + validation for this page.
