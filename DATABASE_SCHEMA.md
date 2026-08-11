# Database Schema

Generated 2026-08-05 from a direct read of `database/models.py` and `supabase/migrations/*.sql`. See
`ARCHITECTURE.md` for the two-engine (config vs. data) design this schema lives inside.

## Local SQLite (`database/models.py`)

All tables below live on the **config/main** database (`get_config_session()`) unless noted otherwise —
i.e. they're shared configuration/data visible regardless of User/Developer Mode. Where a table *is*
mode-scoped, its own row carries an `environment` column (`'user'` | `'developer'`) rather than living on
a separate table.

**Not modeled here:** `raw_visits` — Path Validator's own uploaded-report table. Its columns come straight
from whatever the uploaded Excel contains (plus parsed `latitude`/`longitude` and an `import_id` tag),
created dynamically by pandas at import time (`database/import_service.py`), not a fixed SQLAlchemy class.

**Not found as a dedicated table:** employee hierarchy data. `app/hierarchy_parser.py`/
`app/hierarchy_service.py` operate on in-memory row lists (`refresh_hierarchy()`, `build_lookup_maps()`,
etc.) rather than a SQLAlchemy model — worth confirming exactly where/how hierarchy state persists between
launches (workbook re-parse each time? a cache under `cloud_cache/`?) before relying on this doc for that
detail; not chased down further in this pass.

### Cross-cutting configuration

| Table | Purpose |
|---|---|
| `app_settings` | One row per environment — email/geocoding credentials, `setup_completed` flag, dev-mode password hash/salt. |
| `rule_parameters` | Named parameter per (environment, rule) — Path Validator's rule thresholds, editable from Parameters page. |
| `feature_flags` | Named on/off toggle per environment — the mechanism behind Developer Mode's "ship flagged off in production" pattern. |
| `import_history` | One row per Path Validator import; `id` is the `import_id` referenced by `raw_visits`/`investigation_findings`. Has cloud-sync bookkeeping (`cloud_id`, `synced_at`, `sync_origin`). |
| `active_session` | Singleton (id=1) pointer to which import is Path Validator's current active session. |
| `master_email_recipients` | Manually-configured recipients for Path Validator's Master Email rollup (separate from per-manager emails, which resolve dynamically via the hierarchy). |

### Path Validator

| Table | Purpose |
|---|---|
| `investigation_findings` | One row per rule violation (GPS same-location clustering) flagged for an employee/date. Carries the full stats behind its message (concentration %, cluster lat/lon) plus Hospital Suppression's decision fields. Reviewer sets `status` (Open/Reviewed/Ignored). Cloud-sync bookkeeping via `cloud_id`/`updated_at`/`synced_at` (delta-pull pattern). |
| `workbook_connections` | Selected file path for a named hierarchy workbook (Onyx/Guardians/Xandra) — independent of the daily call-data import. |
| `geocode_cache` | Resolved address per (lat, lon), populated only at email-generation time. |
| `hospital_lookup_cache` | Whether a hospital was found near a (rounded) coordinate — only genuine Overpass answers are cached, never a network failure. |
| `email_notification` (`email_notifications`) | One row per manager notification prepared for a set of findings; status Draft/Sent/Failed/Unresolved. |

### Inventory Monitoring

| Table | Purpose |
|---|---|
| `inventory_parameters` | Named thresholds for the Inventory rule engine. |
| `inventory_thresholds` | Per-branch/item threshold evaluation — full-replace per upload. |
| `inventory_replenishment` | Replenishment-required rows generated from an uploaded Previous-Sales-style report — full-replace per upload. |
| `cwh_stock` | CWH (central warehouse?) stock rows. |
| `inventory_email_recipients` | Configured recipients for Inventory's automated emails (comma-separated `divisions` filter). |
| `inventory_email_notifications` | Send-attempt log, mirrors `email_notifications`; adds a "Skipped - No Data" status and a `report_type` field (no `import_id` — Inventory has no session/workbook-import concept). |

### Payment Analytics

| Table | Purpose |
|---|---|
| `payment_analytics_parameters` | Named thresholds for payment risk/collections rules. |
| `payment_invoices` | Uploaded invoice rows. |
| `payment_active_months` | Which months' data are currently considered "active" for the six-month historical collections view (`app/collections_service.py`/`app/payment_analytics_service.py` — a **different, older** six-month rolling concept than Manager Work Allocation's; the two are not related code). |
| `payment_customer_profiles` | Per-customer risk/behavior profile. |
| `outstanding_invoices` | Currently-outstanding invoice rows. |

### Work Distribution — RGD Coverage

| Table | Purpose |
|---|---|
| `work_distribution_parameters` | Named KPI thresholds (BM/ABM coverage), separate table from every other module's parameters (Work Distribution has no Developer Mode concept of its own). |
| `work_distribution_doctors` | One row per doctor from the most recent upload — **full-replacement**, since a report is a complete monthly snapshot. A doctor always belongs to one BM; only counts toward ABM calculations if `abm_rgd == "A-RGD"`. |
| `work_distribution_findings` | One row per employee (BM or ABM), the computed KPI result — full-replacement, snapshot of thresholds in effect at upload time. |

### Work Distribution — Manager Work Allocation

| Table | Purpose |
|---|---|
| `manager_work_allocation_parameters` | Named thresholds (ABM's Minimum Joint Working Days, RBM's flag tiers) — a separate table from `work_distribution_parameters` since this is a fully independent engine (own upload, own calculation, own findings). |
| `manager_work_allocation_records` | **The rolling six-month history itself.** One row per `(source_engine, emp_code, team_emp_code, month)` — i.e. one row per manager/subordinate pair *per month*, not per uploaded report row. Redesigned 2026-08-05 from a full-replacement mirror into a genuinely persistent, accumulating store: each upload upserts its own monthly records (an already-known month gets corrected, never duplicated) via `app.manager_work_allocation_shared.sync_rolling_window` — the one function either engine ever writes through — then the table is trimmed to the newest 6 distinct months by month *value*. `source_engine` (`"ABM"`\|`"RBM"`) scopes every read/write so the two engines' uploads never see or trim each other's history. Key columns: `month`, `month_sort_key` (year×100+month, e.g. 202607 — avoids string-sort bugs), `joint_days` (this pair+month's own merged total), plus descriptive fields (`rep_hq`, `zone`, `region`, etc.) not used by calculation. |
| `manager_work_allocation_findings` | One row per manager (ABM or RBM) — full-replacement, scoped per engine via `designation`. `total_bms`/`passed_bms`/`failed_bms` are reused by both engines with a per-engine meaning (ABM: met/missed the day threshold; RBM: covered/not-covered). `coverage_percent`/`reason` are RBM-only (NULL for ABM rows). |
| `manager_work_allocation_bm_details` | One row per (manager, subordinate) logical relationship, evaluated across the current rolling window — the Employee Details page's aggregate source (per-month breakdown itself is read straight from `manager_work_allocation_records`, not duplicated here). `joint_days` is ABM's average or RBM's sum; `required_days` is ABM-only (always 0 for RBM); `status` vocabulary differs per engine ("Pass"/"Fail" vs. "Yes"/"No"). |

## Cloud (Supabase, Postgres) — `supabase/migrations/`

19 migrations exist (`0001`–`0019`) as of this audit. Tables created:

| Migration | Table(s) | Covers |
|---|---|---|
| 0001 | `roles`, `profiles` | RBAC — role definitions and user profile→role assignment. |
| 0002–0007 | (policies/functions on the above) | RLS read/write policies, `get_all_users` function, recursion fix, admin read-all. |
| 0008 | `module_configurations` | The generic per-module JSON config sync (`app/sync_service.py`) target. |
| 0009 | (storage buckets) | Path Validator storage buckets. |
| 0010 | `path_validator_imports`, `path_validator_active_session` | Cloud mirror of `import_history`/`active_session`. |
| 0011 | `path_validator_findings` | Cloud mirror of `investigation_findings`. |
| 0012 | `path_validator_email_notifications` | Cloud mirror of `email_notifications`. |
| 0013 | `path_validator_organization_workbooks` | Cloud mirror of `workbook_connections`. |
| 0014 | `inventory_thresholds` | Cloud mirror. |
| 0015 | `inventory_replenishment` | Cloud mirror. |
| 0016 | `payment_invoices` | Cloud mirror. |
| 0017 | `payment_active_months` | Cloud mirror. |
| 0018 | `payment_customer_profiles` | Cloud mirror. |
| 0019 | `outstanding_invoices` | Cloud mirror. |

**No migration exists for Work Distribution or Manager Work Allocation** — confirmed by scanning every
migration filename and `create table` statement. This module is fully local-only right now; a future cloud
migration would need its own `002x_work_distribution_*.sql` / `002x_manager_work_allocation_*.sql` files
plus a matching `*_sync_service.py`, following the exact pattern the other modules already establish.
