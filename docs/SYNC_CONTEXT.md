# Saffron Automation — Data Synchronization Context

**Purpose:** a reference for an engineer designing a data-synchronization architecture for this
app, who has never seen the codebase — including the cloud sync layer just removed — so design
starts from facts. **This document does not propose a sync design.**

**Repo:** `Saffron Automation v2.1 - Development`, branch `remove-sync-system`, HEAD `2401fab`
("Remove cloud synchronization layer app-wide"). Method: `graphify query`/`explain` first, then
direct file reads, `git show`/`git log -p` on the removal commit, and a read-only `sqlite3` query
of the live local database for row counts. Every claim is cited `path:line`; unverified items are
marked NOT FOUND/UNCLEAR.

---

## 1. Executive summary

Saffron Automation is a single-process Windows desktop app (Python 3.12, `customtkinter`/Tkinter —
not a web app) used internally by Saffron Formulations. Five modules: **Path Validator** (GPS
field-force anomaly detection), **Inventory Monitoring**, **Payment Analytics**, **Work
Distribution** (RGD Coverage + Manager Work Allocation), a **Review System** (12-slot workbook
upload/validation pipeline, `app/review_schemas.py:1-16`), plus **User Management**. Each uploads
Excel/CSV reports, computes findings against configurable thresholds, optionally emails results.
All business data lives in local SQLite; the only server component remaining is Supabase Auth
(login) plus a per-user module-permission table — not sync.

Three facts most consequential for a sync design:

1. **A cloud sync layer already existed and was entirely removed on this branch's HEAD commit**
   (`2401fab`): bidirectional push/pull, "Last-Modified-Wins" conflict resolution, a CAS/dirty-flag
   mechanism for findings, a 15-second poller. Milestone 53 (`V2_MIGRATION_LOG.md:1461-1526`)
   documents a production incident where it silently reverted correct local data for days: a
   timezone-mismatched timestamp comparison plus an unpaginated cloud read silently capped at 1000
   rows made the reconciler always think stale cloud data was newer. Any new design inherits this
   failure class — a comparison whose two sides aren't directly comparable — as the top thing to
   design around, not re-derive.
2. **Work Distribution / Manager Work Allocation — the largest module by row count (61,032 rows;
   Section 16) — never had any cloud counterpart, ever** (`DATABASE_SCHEMA.md:103-106`). A future
   design isn't retrofitting five modules uniformly; one has zero prior sync schema to build from.
3. **The old per-table sync strategy was never uniform** — delta-pull, full-replace-with-delete,
   natural-key upsert, and file-reconstruction all coexisted until Milestones 34-38
   (`V2_MIGRATION_LOG.md:670-717`) unified them under one rule specifically because that was
   unmanageable, and that unification explicitly gave up synchronizing deletions (`:703-708`) as an
   accepted trade-off. Both the fragmentation and the trade-off are precedent.

Confidence: high — corroborated by the removal diff, dated milestones, and greps confirming no
sync code paths remain.

---

## 2. Architecture overview

**Stack** (`ARCHITECTURE.md:6-15`): Python 3.12 + `customtkinter`; SQLite via SQLAlchemy
(`database/`); Supabase (Postgres) for auth only post-removal; `loguru` logging. Packaging is out
of scope (see Section 17, item 5 for a doc-staleness note re: packaging).

**Process model:** one process, one Tkinter main loop, no internal API/HTTP layer
(`ARCHITECTURE.md:47-49`). Only network calls: Supabase auth, third-party geocoding/hospital APIs.

**Entry point → first screen** (`main.py:60-119`): (1) `configure_logging()`, guarded; (2) resolve
writable data dir, fatal dialog if none found (`app/config.py:39-62`); (3) `log_config_status()` —
logs Supabase URL/masked key (`app/supabase_client.py:37-58`); (4) `init_db()` — creates tables on
both SQLite files (`database/connection.py:84-92`); (5) `run_startup_migrations()` — ~30 idempotent
schema-fix functions, fixed order (`database/migrations.py:781-815`); (6) one-time inventory
factory-reset gate; (7) five `ensure_*_defaults()` calls; (8) `MainWindow().mainloop()` — every
module screen built once into a registry, `tkraise()`s between them, no rebuild
(`ARCHITECTURE.md:30-32`); Login is a screen in the same registry.

**Layering** (`ARCHITECTURE.md:45-73`): `ui/*.py` = rendering/event wiring only; one
`app/*_service.py` per concern holds logic/validation/DB access; `database/` holds models, the
two-engine connection layer, and migrations.

Confidence: high.

---

## 3. Module inventory

| Module | Purpose | Tables owned | Cross-module reads | Writes cloud? | Writes local? | Offline? |
|---|---|---|---|---|---|---|
| Path Validator | GPS anomaly detection | `import_history`, `active_session`, `investigation_findings`, `workbook_connections`, `geocode_cache`, `hospital_lookup_cache`, `email_notifications`, `master_email_recipients`, dynamic `raw_visits`/`employee_hierarchy`/`employee_daily_metrics` | `rule_parameters`, `feature_flags`, `app_settings` | No — removed (`2401fab`) | Yes | Yes |
| Inventory Monitoring | Thresholds/CWH/branch status | `inventory_parameters`, `inventory_thresholds`, `inventory_replenishment`, `cwh_stock`, `inventory_email_recipients`, `inventory_email_notifications` | `app_settings` | No — removed | Yes | Yes |
| Payment Analytics | Payment behavior/collections | `payment_analytics_parameters`, `payment_invoices`, `payment_active_months`, `payment_customer_profiles`, `outstanding_invoices` | `app_settings` | No — removed | Yes | Yes |
| Work Distribution (RGD + Manager Work Allocation) | Coverage/joint-working KPIs | `work_distribution_*`, `manager_work_allocation_*` (7 tables) | `employee_hierarchy` (`PROJECT_CONTEXT.md:73`) | Never had one (`DATABASE_SCHEMA.md:103-106`) | Yes | Yes |
| Review System | 12-slot upload/validation + reporting | `review_file_slots`, `review_coverage_parameters`, `review_coverage_email_notifications` | `employee_hierarchy` | NOT FOUND — no migration or sync service exists; postdates `DATABASE_SCHEMA.md`'s audit | Yes | Yes |
| User Management | Account/role CRUD | none local — cloud `profiles`/`roles`/`user_module_permissions` | — | Yes — only genuinely cloud-authoritative module | No | **No** |

Confidence: medium — table ownership is high-confidence; Review System's cloud status is inferred
from absence, so NOT FOUND rather than a confirmed "no."

---

## 4. WRITE TOPOLOGY MAP

Destination is local SQLite unless noted. No server-side triggers/RPCs/scheduled jobs touch
business data today (Supabase's remaining active logic is auth/RBAC — Section 7).

| Entity | Who writes | Trigger | Row/bulk | Mutable/append | Delete semantics |
|---|---|---|---|---|---|
| `raw_visits` (dynamic) | `database/import_service.py:31-75` | Daily import | Bulk append (`:68`) | Append-only, tagged `import_id` | Never deleted; old imports just inactive |
| `import_history` | same | same | Row-level | Immutable | Never deleted |
| `active_session` | `app/session_state.py` (inferred, not opened) | New import active | Singleton, id=1 | Mutable | N/A |
| `investigation_findings` | `rules/same_location.py` (rule run), `app/findings_service.py` (status, `database/migrations.py:484-501`) | Rule run; reviewer click | Bulk insert; row-level update | `status`/`updated_at` mutable, rest append-once | Not deleted; non-active import hidden only (`models.py:93-95`) |
| `workbook_connections` | `app/workbook_connections.py:43-58` | Reconnect workbook | Row upsert by name | Mutable | Never deleted |
| `geocode_cache`/`hospital_lookup_cache` | hospital/geocode services | Email-gen time | Row insert | Append-only (unique lat/lon); failed lookups never cached (`models.py:266-269`) | Never deleted |
| `*_email_notifications` (4 tables) | each module's notification service | Send run | Row per recipient | status/sent_at mutate | Never deleted (audit log) |
| `inventory_thresholds`/`inventory_replenishment` | `app/threshold_service.py`/`replenishment_service.py` | Sales/Inventory Report upload | **Full delete-then-rebuild** (`models.py:442-448`,`682-689`) | Snapshot per upload | Absent combo = no row |
| `cwh_stock` | `app/cwh_service.py` | Same upload | Full delete-then-rebuild (`models.py:759-767`) | Snapshot | Item w/ CFA demand gets zero-stock row instead of vanishing |
| `payment_invoices` | `app/payment_analytics_service.py` | Historical (wipe+rebuild) / Monthly (append) | Bulk | **Immutable once inserted** (`models.py:499-502`,`533-536`) | Only Historical re-run wipes |
| `payment_active_months` | same | upload | Small bulk (≤6) | Membership = active signal (`models.py:565-571`) | Evicted rows deleted; invoices themselves never deleted |
| `payment_customer_profiles` | same | ledger change | **Full delete-then-rebuild** every time (`models.py:592-596`) | Recomputed | Full replace |
| `outstanding_invoices` | `app/collections_service.py` | "Daily Refresh" upload | Full delete+reinsert (`models.py:620-624`) | Only `followed_up` mutates between uploads | Full replace, resets `followed_up` |
| `work_distribution_doctors`/`_findings` | `app/work_distribution_service.py` | RGD upload | Full delete-then-rebuild (`models.py:872-876`,`923-925`) | Snapshot | Full replace |
| `manager_work_allocation_records` | `sync_rolling_window()` — sole write path for both engines | ABM/RBM upload | **Upsert per (source_engine, emp_code, team_emp_code, month)**, trim to newest 6 months (`models.py:988-1013`) | Genuinely accumulating — the one non-full-replace business table | Rows outside window deleted by trim |
| `manager_work_allocation_findings`/`_bm_details` | ABM/RBM services, scoped by designation | Same upload | Full delete-then-rebuild, scoped to acting engine | Snapshot | Full replace within engine scope |
| `review_file_slots` | `review_upload_service.py:40-77` | Upload/replace one of 12 slots | Row upsert by `slot_id` | Mutable — overwrites file too (`:80-90`) | No delete path found; slot overwritten, not removed |
| `user_module_permissions` (cloud) | Admin via User Management → Supabase | Grant/revoke | Row per (user, module) | Presence = grant; revoke = DELETE (`0020...sql:47-59`) | Real DELETE, RLS-gated (`:173-180`) |
| `profiles`/`is_super_admin` (cloud) | Admin; one-account repair script | Account create/edit | Row-level | Mutable | Soft (`active` bool), never real delete (`0001...sql:60`) |

**Server-side writers:** per-table trigger functions stamping `updated_at`/`updated_by` (e.g.
`0008_module_configurations.sql:22-36`) — all on tables the app no longer reads/writes. No RPC or
scheduled job found in `supabase/migrations/`.

Confidence: high for local tables; medium for `active_session`'s writer (inferred).

---

## 5. Data classification map

| Table | Category | Authoritative store | Shared scope | Derived from | Recomputable | Deterministic |
|---|---|---|---|---|---|---|
| `raw_visits` | Imported | Local | Shared-all | Uploaded Excel | Yes, re-import | Yes |
| `investigation_findings` | Derived + user-generated | Local | Shared-all | `raw_visits` via rule engine | Findings: yes. `status`: **no**, human decision | Rule eval yes; status N/A |
| `inventory_thresholds`/`_replenishment`/`cwh_stock` | Derived | Local | Shared-all | Sales+Inventory reports + params | Yes, re-upload | Yes, same multiplier |
| `payment_invoices` | Imported | Local | Shared-all | Uploaded report | Only by re-upload | Yes; `payment_days` computed |
| `payment_customer_profiles` | Derived | Local | Shared-all | `payment_invoices` | Yes, always rebuilt | Yes |
| `outstanding_invoices` | Imported + live-computed status | Local | Shared-all | Uploaded report; status computed from now() at read | Cols: yes. Status: **no**, time-dependent | Import yes; status not stable across days |
| `manager_work_allocation_records` | Accumulating | Local | Shared-all | Uploads, merged across time | **No** — history not reconstructable from one upload | Per-upload yes; overall no |
| Config tables (`app_settings`, `rule_parameters`, `feature_flags`, per-module params) | Config | Local | Shared-all (some split by `environment`) | Manual entry | No | N/A |
| `geocode_cache`/`hospital_lookup_cache` | Cache | Local | Shared-all | 3rd-party API | Yes, re-query | Yes, modulo API drift |
| `review_file_slots` | Config + file pointer | Local | Shared-all | Uploaded file (`config.py:81`) | Row: yes. File: **no**, sole copy | Yes |
| `roles`/`profiles`/`user_module_permissions` | Config/reference | **Cloud** | Per-user / shared-role read | Manual admin | No | N/A |
| `employee_hierarchy` (dynamic) | Imported | Local | Shared-all | Hierarchy workbook via `hierarchy_parser.py` | Yes, re-parse | Yes |
| `employee_daily_metrics` (dynamic) | Derived | Local | Shared-all | `raw_visits` via `app/metrics.py:80` | Yes, per import | Yes |

**Local vs. cloud ownership (moot for data now, but load-bearing precedent):** every synced
business table's cloud copy was a *mirror*, never authoritative — local SQLite always held the real
calculation. One exception: `raw_visits`/`employee_hierarchy` were never mirrored row-for-row; the
cloud side stored the *original file* in Storage and each machine **reconstructed** its table by
re-running the unchanged local parser (`0009...sql:1-5`, `0010...sql:10-12`) — a worth-preserving
reconstruct-vs-mirror precedent.

Confidence: high — sourced directly from model docstrings.

---

## 6. Local persistence

**Engine:** SQLite via SQLAlchemy, two engines/files (`database/connection.py:1-93`): config engine
always `saffron_validator.db` (`:39`); data engine is mode-dependent — User Mode shares the config
file, Developer Mode uses a separate `saffron_validator_dev.db` (`:41`,`:49`). Only Publish copies
*configuration* across modes, never data (`ARCHITECTURE.md:60-61`).

**Path resolution** (`app/config.py:39-115`): frozen exe → `%LOCALAPPDATA%\Saffron Validator`,
probed with a write-test file, falling back to `%TEMP%` then a fatal dialog; from source → project
root. `DATABASE_PATH = DATA_DIR/database/saffron_validator.db` (`:75`). A one-time migration copies
any `.db` found next to an old-style installed exe (`:93-114`).

**Indexes/constraints:** `UniqueConstraint`s on natural keys (`rule_parameters`
`(environment, rule_name, parameter_name)`, `models.py:54-56`; `inventory_thresholds`/
`_replenishment` `(branch_key, item_key)`, `:479-481,713-715`; `cwh_stock` `item_key`, `:799-801`;
`geocode_cache`/`hospital_lookup_cache` `(lat, lon)`, `:251,272`). **No foreign keys found
anywhere** — every cross-table reference is a plain column, enforced only in app code. No secondary
indexes beyond PK/unique locally (contrast cloud, which adds `updated_at` indexes — Appendix B).

**Every non-database file written at runtime** (full list, Appendix C):

| Path | Format | Writer | Purpose |
|---|---|---|---|
| `<DATA_DIR>/database/saffron_validator.db` | SQLite | `connection.py:43` | Config + User-mode data |
| `<DATA_DIR>/database/saffron_validator_dev.db` | SQLite | `connection.py:44` | Developer-mode data |
| `<DATA_DIR>/logs/saffron_validator*.log` | Text (loguru) | `app/logging_config.py` (not opened) | App log + rotations |
| `<DATA_DIR>/reports/*.xlsx` | Excel | `table_export_service.py` (`V2_MIGRATION_LOG.md:1533-1536`) | User "Export to Excel" |
| `<DATA_DIR>/review_uploads/<slot_id>.<ext>` | Excel/CSV | `review_upload_service.py:80-90` | Review System's 12 slots, gitignored |
| `cloud_cache/organization_data/*.xlsx` | Excel | **Orphaned** — Section 15 | Old sync layer's local cache |
| `.env` | dotenv | Hand-maintained, gitignored | Supabase URL/anon key |

Confidence: high for path resolution/two-DB design; medium for log rotation specifics (not opened).

---

## 7. Cloud persistence

**Supabase features used, post-removal:** Auth (`app/auth_service.py:91-146`) and RBAC Postgres
tables (`profiles`, `roles`, `user_module_permissions`) — nothing else. Storage, the generic
`module_configurations` config-sync table, and every per-module sync table are still **defined** in
migrations but no code reads/writes them (zero grep matches for `cloud_id`/`synced_at`/
`sync_origin`/`dirty` across `app/`/`ui/`).

**Triggers:** every sync-era table had a `BEFORE INSERT OR UPDATE` audit trigger (e.g.
`0008_module_configurations.sql:22-36`, repeated per table 0010-0019) — still defined in migration
files; whether still live in Supabase is unknown from this repo alone.

**RPCs:** two `SECURITY DEFINER` functions remain active — `is_user_management_admin()`
(`0007...sql:22-35`, redefined `0020...sql:121-141`) and `get_all_users()` (`0004...sql`, redefined
`0020...sql:204-238`). No scheduled job found in any migration.

**Other backend/shared location:** none found — no S3/GDrive/network-share reference anywhere. The
two former Storage buckets (`0009...sql:11-17`) are the only shared-file mechanism that ever
existed, now unused.

**What remains, concretely:** 21 historical migrations (`0001`-`0021`, untouched per the removal
commit) plus the new, deliberately-unexecuted `0022_drop_sync_infrastructure.sql` listing every
sync table/bucket a future cleanup should drop (`:20-48`). Whether the live project still
physically has 0008-0019's objects is **not verifiable from this repo** — the single most
important open question for new sync work (Section 18, Q1).

Confidence: high for what migrations declare; low for live database state (unobservable, out of
scope here).

---

## 8. Local vs cloud divergence

| Difference | Local | Cloud | Citation |
|---|---|---|---|
| Primary key | Autoincrement `INTEGER` | `uuid` `cloud_id`, or natural key as PK (Inventory) | `models.py` vs `0010...sql:22-23`, `0014...sql:22-35` |
| Timestamp | Naive local `datetime.now()` | `timestamptz`, server trigger, effectively UTC | `models.py` vs every `set_*_audit_fields()` |
| Sync bookkeeping cols | **Removed** this branch (`cloud_id`, `cloud_version`, `dirty`, `synced_at`, `sync_origin`, `cloud_updated_at`, `import_cloud_id`) | Still defined in migrations | `git show 2401fab -- database/models.py` |
| Delete semantics | Full delete-then-rebuild common | Old design could push deletes only via explicit `delete_rows()`; the unified rule dropped even that | `V2_MIGRATION_LOG.md:703-708` |
| Work Distribution / Manager Work Allocation / Review System | Fully modeled | **No cloud schema, ever** | `DATABASE_SCHEMA.md:103-106` |
| `raw_visits`/`employee_hierarchy` | Full dynamic table | Never mirrored row-for-row — source file stored, re-parsed per machine | `0009...sql:1-5` |

**Migration mechanism:** Local — `run_startup_migrations()`, ~30 independently-idempotent functions
(check `PRAGMA table_info`/`inspect().has_table()` first), fixed order, run every launch
(`migrations.py:781-815`). **No migration-version table** — schema state is implicit in which
columns currently exist, checked live every run (`:36-38`). Cloud — plain numbered `.sql` files
meant to be pasted into the Supabase SQL Editor by hand (e.g. `0001...sql:9-11`); no runner, no
`schema_migrations` table, no CI — whether a migration actually ran against the live project is
tracked nowhere machine-readable.

Confidence: high — both schemas fully read; version-table absence confirmed in both.

---

## 9. Identity, timestamps, versioning

| Table | PK strategy | Client/server ID | created/updated_at | Set by | Delete | Version/hash | Audit log | Retry-unsafe uniqueness |
|---|---|---|---|---|---|---|---|---|
| `import_history` | Autoincrement int | Server | `imported_at` only | `import_service.py:53-60` | Hard, never deleted | None | None | **None** — a retried import duplicates the row (`:41` dedupes only within one file) |
| `investigation_findings` | Autoincrement int | Server | `created_at`, `updated_at` on reviewer action | Rule engine / `findings_service.py`, backfilled `migrations.py:484-501` | Hard but functionally soft (hidden, not deleted) | None | `status` is a lightweight state field, no log | None found |
| `rule_parameters`/`feature_flags` | Autoincrement int | Server | None | — | Hard | None | None | `UniqueConstraint(environment, name, parameter_name)` protects retries (`models.py:54-56,76-77`) |
| `inventory_thresholds`/`_replenishment`/`cwh_stock` | Autoincrement int, natural key `(branch_key,item_key)`/`item_key` | Server | `last_updated` | Upload upsert | Hard (full rebuild) | None | None | `UniqueConstraint` on natural key — retry-safe (`:479-481,713-715,799-801`) |
| `payment_invoices` | Autoincrement int, **no natural key** | Server | `created_at` fixed, `updated_at` set once (`:533-536`) | Insert time | Hard (Historical wipe) | None | None | **None** — a retried upload can double-insert |
| `manager_work_allocation_records` | Autoincrement int, natural key `(source_engine, emp_code, team_emp_code, month)` **app-enforced only** | Server | `last_updated` | `sync_rolling_window()` | Hard delete on window trim | `month_sort_key` sortable but not a concurrency token | None | **No DB constraint found** for the 4-tuple (`:1047-1073`) — only app logic prevents duplicates |
| `roles`/`profiles`/`user_module_permissions` (cloud) | `uuid` | **Server**, except `profiles.id` = `auth.users.id` | cloud-side only | DB default | `profiles.active` soft; `user_module_permissions` real DELETE | None | None | Composite PK `(user_id, module_key)` prevents dup grants (`0020...sql:54-59`) |

**App-wide pattern:** every local table uses SQLite `AUTOINCREMENT`, not cross-machine-safe. The
old design's workaround was a client-generated `cloud_id` UUID for this reason (`0010...sql`
comment) — gone with sync, but the underlying problem hasn't.

Confidence: high; missing-uniqueness findings worth re-verifying against the live `.db`.

---

## 10. Authentication and authorization

**Credentials reach the client via** `.env` (`SUPABASE_URL`/`SUPABASE_ANON_KEY`), loaded via
`python-dotenv` (`app/supabase_client.py:21-26`). **A `.env` and `.env.example` both exist** —
values were not read or reproduced (redacted per instruction); only presence is reported.
Gitignored (`.gitignore:29`). A build-time guard, `_assert_not_service_role()`
(`supabase_client.py:80-87`), decodes the JWT `role` claim and **refuses to start** if a
`service_role` key is ever configured — the client is designed to ship only the low-privilege anon
key. No privileged key was found anywhere.

**Stored:** session tokens persist to the **OS credential store** via `keyring` (Windows Credential
Manager), not a plaintext file (`auth_service.py:15-17,56-59`); restore refreshes an expired access
token and re-saves on rotation (`:149-197`).

**Privileged key in the shipped client:** No — enforced by the guard above; the anon key ships by
design (RLS is the real boundary).

**RLS still in force** (full text `supabase/migrations/0001-0007, 0020-0021`; Appendix B):
- `profiles`: own-row `SELECT` (`0002...sql:17-21`); admin `SELECT`/`INSERT`/`UPDATE` all, except
  cannot set their own `active=false` (`0005...sql:29-49`, recursion-fixed `0007...sql:52-57`).
- `roles`: any authenticated user reads all (`0002...sql:25-29`) — non-sensitive reference data.
- `user_module_permissions`: own-row read; admin read/insert all, delete any grant except their own
  `user_management` grant (`0020...sql:149-180`).
- **Every sync-era table had `using (true)` for any authenticated user — zero row-level ownership
  was ever enforced on business data**, acknowledged in the migrations themselves (e.g.
  `0008...sql:40-43`). The only real boundary is the Python-side RBAC screen-gate
  (`app/permissions.py:21-33`). **A new sync design cannot lean on existing cloud RLS for business
  data — none exists.**

**Client-side RBAC:** `app/rbac_state.py:29-54` holds an in-memory `Profile`, fails closed
(`permissions.py:11-14,28-33`). `is_super_admin` auto-grants every module, including ones added
later (`0020...sql:23-29`); everyone else needs an explicit per-module grant. Work Distribution was
excluded from migration 0020's backfill — non-super-admins lost access and must be re-granted
(`0020...sql:73-79`).

Confidence: high — mechanisms read directly in source and live migration files; whether live
policies match these files exactly is unverifiable from the repo (Section 17, Q2).

---

## 11. Import and validation pipeline

| Path | Entry point | Format | Required columns | Validation | Destination | Mode | Retains source? |
|---|---|---|---|---|---|---|---|
| Path Validator daily report | `database/import_service.py:31` | Excel (implied) | 21 cols incl. Division, Employee Code, Actual Lat-Long (`app/config.py:118-140`) | NOT FOUND (validator not opened); intra-file `drop_duplicates()` (`:41`) | `raw_visits`, `import_history` | Append | No |
| Inventory — Inventory Report | `app/excel_validation.py:19-20,68-75` | `.xlsx/.xls/.xlsm/.csv` | `BranchLocation`, `Item Group`, `Item Code`, `Item Name`, `TotalQty`, `Transit Stock` | Header scan ≤100 rows, case/whitespace/period-normalized, merge-tolerant (`:23-53`) | `inventory_replenishment`, `cwh_stock` | **Full replace** | No |
| Inventory — Sales Report | `:21,77-83` | same 4 | `Division`, `CFA`, `Item Name`, `Packing`, `Sales` | same engine | `inventory_thresholds` | **Full replace** | No |
| Payment — Historical | `:85-93` | same 4 | `Month`, `Party Name`, `Type`, `Inv. No.`, `LR Date`, `Due Date`, `Clear Date` | same engine | `payment_invoices` (wipe+rebuild), `payment_active_months`, `payment_customer_profiles` (rebuild) | **Full replace** | No |
| Payment — Monthly | same validator, other call site | same | same | same | `payment_invoices` | **Append only** | No |
| Payment — Outstanding | `:95-104` | same 4 | `Party Name`, `Division`, `H.Q.`, `Bill No.`, `L.R. Date`, `Due Date`, `Bill Amount`, `Month` | same engine | `outstanding_invoices` | **Full replace** | No |
| Work Distribution (RGD/ABM/RBM) | `work_distribution_parser.py`, `manager_work_allocation_parser.py` (not opened) | NOT FOUND | NOT FOUND | NOT FOUND | doctors/findings (full replace); records (**upsert + 6mo trim**) | Mixed | No |
| Review System (12 slots) | `review_upload_service.py`, validated by `review_validation.py`/`review_schemas.py` | Per-slot, tolerant of real-world header variants (`review_schemas.py:10-59`) | Per-slot fixed lists w/ documented exceptions (`optional_columns`/`month_columns`/period families) | Required-present only; **column order/extras never fail validity** (`:30-42`) — a deliberate relaxation for structurally-different-but-equivalent regional files | `review_file_slots` + physical file | **Replace-in-place per slot** (`:84-90`) | **Yes — only path that retains the original file** |

No explicit file-size/row cap found in any validator opened. Work Distribution's largest table
(`work_distribution_doctors`) holds 61,032 rows currently (Section 16).

Confidence: medium — the Excel validator engine and Review System semantics were read directly;
Path Validator/Work Distribution parsers' exact rules were not opened this pass (NOT FOUND, not
guessed).

---

## 12. Runtime lifecycle

**Startup:** see Section 2. No network call required; Supabase is contacted only on an actual
sign-in attempt or session restore (`auth_service.py:91-197`), never at import time.

**Shutdown:** `app.mainloop()` returning is the only path found; no `atexit`/signal handler
located. `sign_out()` clears the local keyring entry unconditionally even if server-side
invalidation fails (`auth_service.py:200-209`) — never traps the user offline.

**Background threads/timers/polling: none remain.** The old 15-second Path Validator poller
(`app/module_sync_poller.py`) was deleted in `2401fab`; no other timer/poll/background thread found
anywhere in `app/`/`ui/`. Exports/uploads still run on a background thread behind a
`LoadingOverlay` purely for UI responsiveness (`V2_MIGRATION_LOG.md:1536`) — not polling or sync.

**Refresh triggers:** every page loads once at `on_show()`/after upload; no "Refresh" button
remains in any module (all removed in `2401fab`).

**In-memory caches:** `app/mode_state.py:21` and `app/rbac_state.py:39` — simple module-level
dicts, set/cleared by explicit actions, neither surviving restart by design (`mode_state.py:4-7`).
No other app-level cache found; `geocode_cache`/`hospital_lookup_cache` are persistent DB tables,
never automatically expired (`models.py:246-248`).

Confidence: high — absence of threads/pollers corroborated by both the commit diff and a grep of
the current tree.

---

## 13. Concurrency, transactions, failure

**Transaction boundaries:** every write path found commits per logical step, not atomically —
e.g. `import_service.py` commits `ImportHistory` (`:59-60`) separately from the bulk `raw_visits`
insert (`:68`); a crash between them leaves an orphaned history row. Startup migrations use
`engine.begin()` per function (`migrations.py:47,70,...`), not one transaction for the whole run —
mitigated by each function's own idempotency.

**Locking:** no application-level lock found anywhere. No `PRAGMA journal_mode=WAL` call found in
`connection.py`, so SQLite's default rollback-journal mode applies — a writer blocks other writers.

**Two instances running:** not tested, but architecturally unsafe by implication — no app-level
single-instance lock was found in `main.py`, and SQLite's default mode risks `database is locked`
errors during multi-second full-table-replace operations (Section 4) — a concern for any sync
design assuming one writer per machine without also guarding two copies of the app itself.

**No network:** every business write is local-first (Section 1). Only sign-in and optional
geocoding/hospital calls are network-dependent, both with non-crashing error handling
(`auth_service.py:113-133`, `supabase_client.py:143-161`).

**Crash/partial write:** the full-delete-then-rebuild tables (Section 4) are highest-risk — a crash
between delete and rebuild leaves the table **empty**, not merely stale. No wrapping transaction
was found for that pair in the files opened; the actual service files were not opened directly —
flagged UNCLEAR in Section 17.

**Retry logic:** none found for local writes. Old cloud sync: Milestone 53 shows a failed push
**silently disappeared** rather than retrying (`V2_MIGRATION_LOG.md:726`), causing that incident.

Confidence: medium — based on files actually opened, not every service; "two instances unsafe" is
inference.

---

## 14. Cross-module dependencies and invariants

**Shared state:** `employee_hierarchy` (dynamic, from Path Validator's `hierarchy_parser.py`) is
read by Work Distribution's notification design (`PROJECT_CONTEXT.md:73`) and Review System's
hierarchy/coverage pages. `app_settings`/`rule_parameters`/`feature_flags` are read app-wide via
`get_config_session()` (`connection.py:66-69`). `mode_state.py`/`rbac_state.py` are dependency-free
leaf modules read from anywhere. Work Distribution's two engines share one parser family and
hierarchy dataset but keep **independent** parameter/finding tables (`PROJECT_CONTEXT.md:36`).

**Numbered invariants** (from model docstrings/migration log; not independently re-verified
against live data):

1. `active_session.import_id`, when set, must reference an existing `import_history.id`; only rows
   tagged with that `import_id` are "current" (`models.py:29-34,93-95`).
2. `inventory_replenishment`/`cwh_stock` thresholds are snapshots of `inventory_thresholds` at
   upload time, not live-recomputed (`models.py:463-476`).
3. `manager_work_allocation_records` holds at most 6 distinct `month_sort_key` values per
   `source_engine`, enforced only by `sync_rolling_window()`'s trim, not a DB constraint
   (`:988-1013`).
4. `source_engine` partitions that table into two disjoint halves (`ABM`/`RBM`) that must never
   read or trim each other's rows (`:1037-1042`).
5. `payment_active_months` membership — not any flag on `payment_invoices` — is the sole
   "is active" signal; an invoice outside the window still exists but must be treated as archived
   (`:565-571`).
6. `AppSettings.dev_password_hash`/`dev_password_salt`/`setup_completed` are read/written only on
   the `environment='user'` row, regardless of active mode (`:390-397`).
7. A `WorkDistributionDoctor` only counts toward ABM KPIs when `abm_rgd` normalizes to `"A-RGD"`; a
   doctor can validly appear in both a BM's and an ABM's book via two independent columns
   (`:876-889`, `PROJECT_CONTEXT.md:44-45`).
8. Config tables always live on the config engine regardless of mode; every other business table
   follows the mode-dependent data engine — conflating these leaks Developer data into User Mode's
   file (`connection.py:1-26`).

Confidence: medium — invariants 1-7 are explicit docstring statements; invariant 8 is
architectural, verified by reading `connection.py` only.

---

## 15. Existing synchronization assumptions

The section most directly relevant to new sync design.

**1. A full bidirectional sync layer existed and was deliberately removed on HEAD** (`2401fab`): a
generic per-module JSON config push/pull (`app/sync_service.py`, Milestone 9); six per-module
data-sync services (import/inventory/findings/organization-data/payment/email); a centralized
`reconcile_rows()` Last-Modified-Wins reconciler adopted app-wide (Milestones 34-38,
`V2_MIGRATION_LOG.md:670-672`) to replace five inconsistent per-table strategies; a CAS/dirty-flag
mechanism (`cloud_version`, `dirty`) so a pulled cloud snapshot could never overwrite an
unacknowledged local reviewer decision; a 15-second Path-Validator-only poller; a manual
per-module Refresh button (`ModuleRefreshControl`) — all deleted.

**2. The sync layer caused a multi-day production data-loss incident** (Milestone 53,
`V2_MIGRATION_LOG.md:1461-1526`, summarized in Section 1) — a naive-vs-UTC timestamp comparison
under mismatched column names plus a PostgREST 1000-row cap hit silently by an unpaginated read.
Exactly the failure class any new design must defend against structurally.

**3. The replacement rule explicitly gave up deletion propagation** (`V2_MIGRATION_LOG.md:703-708`):
"Last-Modified-Wins only ever pushes or pulls — it never deletes... it will be pulled back."
Not hypothetical: it caused a second incident in a two-laptop test, where a stale
replace-from-cloud pull wiped 199 locally-built customer profiles after a cloud push had failed
silently (`:719-732`).

**4. Two dead-code remnants remain**, both referencing a class (`ModuleRefreshControl`) that no
longer exists: `ui/work_distribution_module.py:3` and `ui/review_system_module.py:3`, both reading
"*no ModuleRefreshControl (no cloud sync exists yet for this module)*" — already true before
removal, stale even at the moment of the removal commit.

**5. An orphaned local cache directory remains on disk**: `cloud_cache/organization_data/` holds
three real workbook files (last modified 2026-09-02, five days before removal).
`ARCHITECTURE.md:141` documents its purpose — "*Local cache of pulled cloud data*" — written by the
deleted `organization_data_sync_service.py`. A grep for `cloud_cache` in `app/`/`ui/` returns
**zero matches**; nothing reads or writes it anymore.

**6. `0022_drop_sync_infrastructure.sql`** evidences a conscious decision to leave cloud-side
artifacts in place: a fully-written teardown script explicitly annotated "**DO NOT RUN THIS AGAINST
PRODUCTION SUPABASE... NOT EXECUTED ANYWHERE**" (`:1-13`). Cloud-side sync schema/data may still
exist, populated with whatever was last pushed — unverifiable from this repo.

**7. No manual "poor man's sync"** (shared-drive snapshot export/import) was found; every export
produces a human-facing `.xlsx` report, never a re-importable snapshot.

**8. No cloud-first read pattern remains.** Before removal, Inventory's Refresh *was* effectively
cloud-first (pulled and overwrote local every time) — exactly the behavior that caused the
Milestone 53 incident.

Confidence: high — every item is a direct grep/read or cited passage from the log/removal diff.

---

## 16. Volume and performance observables

Row counts below are a **direct read of the live local `database/saffron_validator.db`**, not an
estimate:

| Table | Rows | Table | Rows |
|---|---|---|---|
| `work_distribution_doctors` | 61,032 | `hospital_lookup_cache` | 125 |
| `raw_visits` | 62,628 | `manager_work_allocation_findings` | 160 |
| `investigation_findings` | 8,447 | `work_distribution_email_notifications` | 161 |
| `manager_work_allocation_records` | 4,482 | `cwh_stock` | 105 |
| `inventory_replenishment` | 2,402 | `work_distribution_upload_log` | 46 |
| `geocode_cache` | 2,533 | `import_history` | 41 |
| `inventory_thresholds` | 1,408 | `review_file_slots` | 12 |
| `email_notifications` | 1,037 | `inventory_email_notifications` | 13 |
| `payment_invoices` | 721 | `payment_active_months` | 6 |
| `manager_work_allocation_bm_details` | 747 | `outstanding_invoices` | 0 |
| `employee_hierarchy` | 585 | `work_distribution_parameters` | 9 |
| `employee_daily_metrics` | 540 | | |
| `work_distribution_findings` | 527 | | |
| `payment_customer_profiles` | 207 | | |

**Estimate:** `work_distribution_doctors`/`raw_visits` (~60K rows) matter most for pagination
design — the scale at which the old design's silent 1000-row cap went undetected for the much
smaller `inventory_thresholds` (1,152 rows at incident time, `V2_MIGRATION_LOG.md:1491`) and would
recur immediately here with any naive single-page read.

**Query patterns/hot paths:** NOT FOUND — no slow-query log located; `app/timing.py`'s
`.timed(...)` wrapper (`import_service.py:39`) suggests some instrumentation exists, output
destination not traced. **File sizes:** NOT FOUND directly; sample export/upload files run
roughly 15-40 KB (rough estimate, not a stated limit).

Confidence: high for row counts (direct measurement); low for query patterns/file-size norms.

---

## 17. Unknowns, ambiguities, risks

1. **Live Supabase project state is unknown** — whether 0008-0019's tables/buckets still exist,
   with what data. **The single biggest blocker** to new sync design (Section 18, Q1).
2. **Migration files may not match what's deployed** — every file says "run by hand," no
   CI/runner enforces order. Migration 0021 *repairs* a data-corruption side effect of 0020's own
   backfill — manual application already caused one real drift incident.
3. **`payment_invoices`/`manager_work_allocation_records` may lack DB-level duplicate protection**
   (Section 9) — no `UniqueConstraint` found for either; a naive retry could double-insert.
4. **Whether full-delete-then-rebuild writes are atomic is unconfirmed** — `threshold_service.py`,
   `replenishment_service.py`, `cwh_service.py`, `collections_service.py`,
   `work_distribution_service.py` weren't opened. Non-atomic changes the crash failure mode from
   "stale" to "empty" (Section 13).
5. **The repo's own docs are measurably stale relative to code**: `PROJECT_STATUS.md:102-104`
   ("never packaged") is contradicted by real installers; `app/version.py:22` omits three modules
   despite `APP_VERSION` at 2.4.0; `DATABASE_SCHEMA.md` (2026-08-05) predates the Review System's
   tables. **Decisions sourced from this repo's markdown rather than code should be provisional.**
6. **Whether the Review System ever had — or plans — cloud sync is genuinely unknown**, not just
   "no": it postdates the last full audit and wasn't mentioned in the removal commit's scope.
7. **Path Validator's daily-import and both Work Distribution parsers' exact validation rules
   weren't traced to column-level detail** — referenced via graphify/docstrings only, given this
   review's time budget.
8. **No file-size/row-count upload limit was found anywhere** — every validator loads the whole
   file with no cap beyond a 100-row header-scan window.

Confidence: high that these are genuine gaps; severity judgments are this reviewer's assessment.

---

## 18. Questions for the owner

1. **Does the live Supabase project still contain the sync-era tables/buckets, with what data?**
   Unblocks: reuse that schema/data vs. treat local SQLite as sole source of truth vs. drop first.
2. **Is this Supabase project intended for new sync, or is the backend open for
   reconsideration?** Unblocks: whether the design inherits existing Auth/RLS/Postgres investment.
3. **Was the old single-editor assumption ("only one person edits a module at a time,"
   `V2_MIGRATION_LOG.md:672`) actually true, or just never yet violated?** Unblocks: whether genuine
   multi-writer conflict resolution is needed, or timestamp-comparison sync still suffices.
4. **Is deletion propagation actually required** for Payment Analytics' rolling window and
   Collections' daily refresh, or was living without it acceptable? Unblocks: whether
   tombstone/delete-sync — materially harder than row push/pull — is in scope.
5. **Is Work Distribution/Manager Work Allocation (largest module by row count) actually in
   scope** given it never had a cloud counterpart? Unblocks: whether the design must handle a
   module with zero sync precedent.
6. **Should the Review System be in scope?** Newest module, no sync history, and the only path
   that already retains original uploaded files locally. Unblocks: whether file-level sync is
   required alongside row sync.
7. **Is running two instances of the app on one machine ever intentional** (beyond the
   already-separate User/Developer files)? Unblocks: whether same-machine multi-instance writes
   need defending against.
8. **Has the missing uniqueness constraint on `manager_work_allocation_records`/`payment_invoices`
   ever produced a duplicate row?** Unblocks: whether a data-cleanup pass is needed before sync
   begins.

Confidence: high that these are the right questions given the evidence gathered.

---

## Appendix A — Local schema DDL

Authoritative source: `database/models.py` (1,311 lines; full column/type/constraint definitions
per Sections 4/5/9's citations). The three dynamically-created tables (not in `models.py`, per its
own docstring at `:1-7`) — read verbatim from the live `database/saffron_validator.db` via
`sqlite_master`:

```sql
CREATE TABLE raw_visits (
    "Division" TEXT, "Zone" TEXT, "Region" TEXT, "Employee Name" TEXT,
    "Employee Code" TEXT, "Reporting HQ" TEXT, "Desig." TEXT,
    "Reporting Sr. Name" TEXT, "Reporting Sr. Emp Code" TEXT,
    "Reporting Sr. Desig." TEXT, "Date" TEXT, "Day" TEXT,
    "Listed / Non Listed" TEXT, "Customer Type" TEXT, "Code" TEXT, "Name" TEXT,
    "SMS Code" TEXT, "Category" TEXT, "Specialty" TEXT, "City/Place" TEXT,
    "POB Value" FLOAT, "Worked With" TEXT, "Products" TEXT, "Gifts" TEXT,
    "Samples" FLOAT, "Visit Remarks" TEXT, "Visit Registration Mode" TEXT,
    "Visit Registration Time" TEXT, "Visit Registration Date" TEXT,
    "Standardized Location" FLOAT, "Actual Location" FLOAT,
    "Actual Lat-Long" TEXT, latitude FLOAT, longitude FLOAT, import_id BIGINT
);

CREATE TABLE employee_hierarchy (
    employee_code TEXT, employee_name TEXT, designation TEXT, mobile TEXT,
    email TEXT, doj TEXT, abm_code TEXT, abm_name TEXT, rbm_code TEXT,
    rbm_name TEXT, senior_code TEXT, senior_name TEXT, senior_email TEXT,
    senior_designation TEXT, division TEXT, source_sheet TEXT
);

CREATE TABLE employee_daily_metrics (
    employee_name TEXT, employee_code TEXT, visit_date DATE,
    total_calls BIGINT, unique_doctors BIGINT, first_visit_time TEXT,
    last_visit_time TEXT, total_distance_km FLOAT,
    valid_coordinate_visits BIGINT, invalid_coordinate_visits BIGINT
);
```

Confidence: high — the three DDLs above are a verbatim `sqlite_master` read of the live database.

## Appendix B — Cloud schema DDL and RLS policies

**Auth/RBAC (live, never removed) — read in full:** `0001_roles_and_profiles.sql`,
`0002_profiles_roles_read_policies.sql`, `0003_assign_inventory_test_user.sql`,
`0004_get_all_users_function.sql`, `0005_user_management_write_policies.sql`,
`0006_admin_read_all_profiles.sql`, `0007_fix_profiles_policy_recursion.sql`,
`0020_module_based_permissions.sql`, `0021_repair_admin_account_permissions.sql`. Policies
summarized with line citations in Section 10; full text remains at these paths (not reproduced
verbatim here per the excerpt-length limit).

**Sync infrastructure (schema still defined; app code deleted; live-DB presence unconfirmed,
Section 17 Q1) — read in full except where noted:** `0008_module_configurations.sql`,
`0009_path_validator_storage_buckets.sql`, `0010_path_validator_imports_and_active_session.sql`,
`0011_path_validator_findings_sync.sql`, `0012_path_validator_email_notifications_sync.sql`,
`0013_path_validator_organization_workbooks.sql`, `0014_inventory_thresholds_sync.sql`,
`0016_payment_invoices_sync.sql`, `0019_outstanding_invoices_sync.sql` — all nine read verbatim.
`0015_inventory_replenishment_sync.sql`, `0017_payment_active_months_sync.sql`,
`0018_payment_customer_profiles_sync.sql` were **not** opened byte-for-byte this pass; they
structurally repeat the confirmed pattern of 0014/0016/0019 (natural-key or `cloud_id`-keyed table,
`updated_at` audit trigger, `using (true)` RLS — Section 10) per `DATABASE_SCHEMA.md:97-100`'s own
table-of-contents description.

**Teardown reference (unexecuted):** `0022_drop_sync_infrastructure.sql` — read in full (49
lines); quoted in Section 15, item 6.

Confidence: high for the twelve files read in full; medium for the three not opened byte-for-byte
(pattern-matched, not directly confirmed).

## Appendix C — Every path written at runtime

- `<DATA_DIR>/database/saffron_validator.db`
- `<DATA_DIR>/database/saffron_validator_dev.db`
- `<DATA_DIR>/logs/saffron_validator.log` (+ rotated timestamped copies), `saffron_validator_dev.log`
- `<DATA_DIR>/reports/*.xlsx` (user-triggered exports)
- `<DATA_DIR>/review_uploads/<slot_id>.<ext>` (12 fixed slots)
- `cloud_cache/organization_data/*.xlsx` — orphaned, no longer written (Section 15, item 5)

`<DATA_DIR>` = `%LOCALAPPDATA%\Saffron Validator` (installed/frozen) or the project root (source);
resolution logic at `app/config.py:39-72`. Full detail and formats in Section 6.

## Appendix D — Dependency manifest

Full `requirements.txt`:

```
pandas
openpyxl
xlrd
sqlalchemy
customtkinter
geopy
loguru
pillow
matplotlib
supabase
python-dotenv
keyring

# Build-time only (packaging into a Windows .exe — see build_exe.ps1), not
# needed to run the app from source.
pyinstaller
```

No message-queue, cache (Redis), or purpose-built sync/replication library is present — the old
sync layer was hand-built entirely on the `supabase` Python client's plain table CRUD (`httpx` is
pulled in transitively and used directly at `app/auth_service.py:24`,
`app/supabase_client.py:15`).

Confidence: high — direct file read.
