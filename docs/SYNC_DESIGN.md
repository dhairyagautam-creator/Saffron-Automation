# Saffron Automation — Data Synchronization Design

Status: **approved mechanism, not yet built**. This is the design that came out of
`docs/SYNC_CONTEXT.md`'s review plus the owner's own answers — captured here so it
survives as a spec, not scattered across chat. Two items remain genuinely open and
are called out at the end; everything else below is decided.

## 1. Core mechanism — one pattern, applied uniformly

This is **not** per-table row sync. There is exactly one mechanism, reused by every
module:

1. An upload goes to **Supabase Storage**, unchanged, exactly as the user provided it.
2. A single **manifest table** (new, e.g. `public.upload_manifest`) gets one new row:
   `module`, `slot` (identifier within the module — e.g. a Review System slot_id, or
   "daily_report" for Path Validator), `storage_path`, `content_hash`, `uploaded_by`,
   `uploaded_at`, and a monotonic `sequence` number. Manifest rows are **append-only**
   — a replacement upload is a new row, never an edit to an old one.
3. Every client **polls the manifest**. For any row whose `content_hash` it doesn't
   already have locally, it downloads the file from Storage and **re-runs the
   existing local parser** against it, exactly as if the user had uploaded it
   themselves. This rebuilds the client's own SQLite tables.
4. **No derived row ever crosses the network.** The only things Supabase ever holds
   are the original files (Storage) and the manifest pointing at them (Postgres).

This is a direct extension of the pattern migrations `0009`/`0010` already
established for Path Validator (Storage bucket + cloud pointer row, client
re-parses locally) — generalized to every module and simplified: one manifest
table instead of a bespoke cloud table per module.

## 2. Manifest schema (sketch)

```sql
create table public.upload_manifest (
    id uuid primary key default gen_random_uuid(),
    module text not null,
    slot text not null,
    storage_path text not null,
    content_hash text not null,
    sequence bigint generated always as identity,
    uploaded_by uuid references auth.users(id),
    uploaded_at timestamptz not null default now()
);

create index on public.upload_manifest (module, slot, sequence);
```

Row-level security: any authenticated user can read (matches every existing
sync-era table's RLS shape — screen-level RBAC is the real gate in this app, not
row ownership) and insert (upload); no update, no delete — immutability is
enforced by never granting those.

## 3. Per-module mapping

| Module | What's uploaded | Rebuild recipe | Locally-rebuilt (never sync as rows) |
|---|---|---|---|
| **Path Validator** | Daily report + org/hierarchy workbooks | Download → re-run existing parser | `raw_visits`, `employee_hierarchy`, `employee_daily_metrics`, `investigation_findings` |
| **Inventory** | Sales Report, Inventory Report | Download → re-run existing parser (full delete-then-rebuild, same as today) | `inventory_thresholds` (`database/models.py:430`), `inventory_replenishment` (`:658`), `CwhStock` (`:735`) |
| **Payments** | Historical/Outstanding (full-replace) + Monthly (append) | Historical/Outstanding: latest file only. Monthly: retain the **ordered set** of files, replay in `sequence` order — a single file can't rebuild `payment_invoices`, since monthly uploads append rather than replace | `payment_invoices`, `payment_active_months`, `payment_customer_profiles` (both rebuilt from the ledger) |
| **Work Distribution** | RGD uploads (full-replace) | Retain the last 6+ months of ABM/RBM source files, replay in `sequence` order for the rolling window | `manager_work_allocation_records` (`database/migrations.py:608`) — **the one table whose state is not trivially derivable from a single file; verify replay reproduces the current table exactly before relying on it** |
| **Review System** | 12 fixed upload slots (`app/review_schemas.py`) | One manifest row per slot upload. Every other client pulls the file into `review_uploads/` and updates `review_file_slots` (`database/models.py:170`) so the slot shows filled, with uploader + timestamp | — |

**Path Validator findings** are purely derived once the reviewer-marking workflow
is removed (per the owner) — never synced, recomputed on each machine from
`raw_visits` + rules, same as before but now also never leaving the machine at
all.

**Never sync, regardless of module:** `geocode_cache`, `hospital_lookup_cache`
(pure caches — re-querying is cheap), and every table listed as "locally-rebuilt"
above.

## 4. Cloud-authoritative parameters (the one shared-write surface)

Unlike files, parameter/threshold tables are genuinely edited by more than one
person and become **cloud-authoritative** — Postgres is the source of truth,
local SQLite becomes a read cache refreshed on pull.

Affected tables (all share the same plain name/value or settings-row shape per
their own docstrings in `database/models.py`):

- `RuleParameter` (`:43`) — Path Validator
- `ReviewCoverageParameter` (`:194`) — Review System
- `InventoryParameter` (`:410`) — Inventory
- `PaymentAnalyticsParameter` (`:539`) — Payments
- `WorkDistributionParameter` (`:853`) — Work Distribution
- `ManagerWorkAllocationParameter` (`:969`) — Manager Work Allocation

**Mechanism: optimistic concurrency, not last-write-wins.**

```sql
alter table public.<parameter_table> add column version integer not null default 1;
```

Client reads a row (gets its current `version`), edits locally, then writes:

```sql
update public.<parameter_table>
set value = $new_value, version = version + 1
where id = $id and version = $version_read;
```

Zero rows affected ⇒ someone else changed it since it was read. The client shows
who and when, and forces a reload — **never** auto-merges, **never** compares
timestamps across machines. This is a single Postgres statement resolving its own
comparison; there is no second clock involved, which is exactly what the
Milestone 53 incident (`V2_MIGRATION_LOG.md:1483-1496`) got wrong with
cross-machine timestamp comparison.

This is required regardless of whether concurrent edits have actually happened —
it costs one column and one `WHERE` clause, and it fails **visibly** instead of
silently, which is the whole point.

## 5. Conflicts — what does and doesn't need handling

- **Files: no conflict resolution, by construction.** One designated uploader per
  module/slot means overlapping file writes are structurally impossible, not
  merely unobserved. A re-upload of a slot is simply a new manifest row —
  **later upload wins, file replaces file** — but `uploaded_by`/`uploaded_at`
  must be visible on every slot in the UI so a replacement is seen, not silent.
- **Parameters: optimistic version check**, per Section 4. No last-write-wins, no
  timestamp comparison, no automatic merge, anywhere in the system.

## 6. Invariants (hold regardless of the open compliance question below)

1. Files in Storage are immutable and append-only; a replacement is a new
   manifest row, never an edit. Full upload history is retained.
2. Parameter writes use the optimistic version check; a concurrent edit fails
   and is shown to the user, never auto-resolved.
3. No last-write-wins anywhere in the system.
4. **No manifest retention/pruning policy** — deliberately deferred. Deleting old
   uploads is exactly what an audit requirement would forbid if one exists, and
   Storage is cheap. Do not add this without being asked.
5. Every upload path must start persisting the original file to Storage — today
   only the Review System retains its source file locally; every other module's
   upload path needs that added as a prerequisite for this design (Storage
   becomes the thing being synced, so it has to actually hold the file).

## 7. Open items — owner-decided, not assumed

1. **Email notification tables** (`ReviewCoverageEmailNotification` and its
   siblings across modules) — append-only audit logs of what was sent. Do
   non-uploaders need to see them across machines, or do they stay local? Not
   decided; do not build either direction until answered.
2. **Compliance/audit obligation** on this data — unresolved on the owner's side.
   Doesn't block the design (Section 6's invariants already satisfy
   no-silent-overwrite by construction); if it comes back "yes," the only likely
   addition is a parameter change log (old value, new value, who, when — a small
   append-only table, not a redesign).

## 8. Suggested build order

Not yet started. Owner flagged Review System as highest priority (12 slots,
currently coordinated by hand, most immediately painful). Suggested sequence:

1. `upload_manifest` table + RLS (new migration, `supabase/migrations/0023_...`)
2. Review System: wire slot uploads through the manifest, add
   `uploaded_by`/pull logic, update `review_file_slots` on pull
3. Parameter version columns + optimistic-write helper (one generic
   client-side function, reused by all six parameter tables)
4. Path Validator / Inventory / Payments / Work Distribution upload paths, in
   whatever order matches actual pain — each is the same recipe against a
   different parser
