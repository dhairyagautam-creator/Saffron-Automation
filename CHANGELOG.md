# Changelog

A condensed, human-readable history. **`V2_MIGRATION_LOG.md`** (in this same root) is the verbose,
authoritative record for everything through Milestone 57 (2026-07-29) — every entry below that point is a
compression of that file, not a replacement for it; go there for exact file diffs, verification steps, and
root-cause narratives. **This file is what a new session should skim first**; drop into the migration log
only when you need the full story behind one specific change.

> **Known gap:** `V2_MIGRATION_LOG.md` stops at Milestone 57 (2026-07-29). Work Distribution's entire
> build — RGD Coverage, then Manager Work Allocation's ABM engine, then its RBM engine, then the
> 2026-08-05 rolling six-month redesign of both — happened after that and was **never logged there**. The
> Work Distribution entries below are reconstructed from the modules' own docstrings (dated comments
> inside `app/work_distribution_service.py`, `app/manager_work_allocation_*.py`, etc.), not from a
> narrative log entry. If you keep maintaining a detailed migration log going forward, either resume
> writing into `V2_MIGRATION_LOG.md` or start a `V2_MIGRATION_LOG_2.md` — just note here which one is
> current so a future session doesn't read a stale file as the latest.

## 2026-08-05 — Folder cleanup + documentation

- Renamed the working project from `2.0 dev` to `Saffron Automation v2.1 - Development` — the old name
  sat next to `Saffron Automation v2.0 - Development` and caused a full session to search the wrong
  project and conclude a fully-built feature didn't exist. See `PROJECT_CONTEXT.md`.
- Archived `Saffron Automation v2.0 - Development` (stalled ~2026-07-27) to `Archive\`. Left
  `Saffron Automation - Main` untouched (still the current production reference / currently-installed
  build).
- Audited Work Distribution top to bottom: confirmed fully registered, no orphaned files, all imports
  resolve, no duplicate implementations, and RBM/ABM rolling six-month parity already complete (see next
  entry). Full detail in `PROJECT_STATUS.md`.
- Added `PROJECT_CONTEXT.md`, `ARCHITECTURE.md`, `DATABASE_SCHEMA.md`, `MODULE_OVERVIEW.md`, this
  `CHANGELOG.md`, and `PROJECT_STATUS.md`.

## 2026-08-05 — Manager Work Allocation: rolling six-month redesign (ABM + RBM)

A genuine business-process correction, not a bug fix: manager/subordinate rows that looked like
duplicates within one upload were never duplicates — each uploaded report already contains several
months of history, and a pair legitimately appears once per month it has a record for. The old
architecture summed all of it together, silently collapsing month-by-month history into one meaningless
total.

- Added `app/manager_work_allocation_shared.py` — the one shared implementation of month parsing
  (`parse_month`, tolerant of several real-world spellings), intra-month-duplicate merging
  (`merge_same_month_duplicates` — same pair + same month only; different months are never merged), and
  the rolling-window sync/trim (`sync_rolling_window` — upserts new monthly records, keeps the newest 6
  distinct months by month value, never by upload order or filename).
- ABM engine (`app/manager_work_allocation_service.py`) rebuilt onto this shared architecture: evaluates
  the **average** joint days per BM across the retained window against a configurable threshold.
- RBM engine (`app/manager_work_allocation_rbm_service.py`) rebuilt onto the identical shared
  architecture: evaluates the **sum** of joint days per BM ("worked with at least once" = covered) against
  a BM-count-tiered missed-BM threshold. Only this business rule differs from ABM by design — the
  rolling-window mechanics are shared code, not a parallel reimplementation.
- Employee Details (`ui/work_distribution_employee_details_page.py`) rebuilt to render a dynamic-column
  month-by-month trend table (built fresh from whichever months are currently retained) for both engines
  through one shared renderer.
- New table: `manager_work_allocation_records` — the persistent, accumulating rolling-window store (see
  `DATABASE_SCHEMA.md`). Both engines write through it via `sync_rolling_window` only.

## Reconstructed: Work Distribution's earlier phases (dates approximate, pre-dating the entry above)

- **Phase 1 — RGD Coverage** (`app/work_distribution_service.py`, `app/work_distribution_parser.py`):
  doctor-level coverage findings, split into a BM-side calculation ("B-RGD" Category doctors) and an
  ABM-side calculation ("A-RGD" ABM RGD doctors) — full-replacement per upload (a complete monthly
  snapshot, not incremental).
- **Phase 2/3 — Manager Work Allocation, ABM then RBM engines**: added as a second, independent engine
  over the same uploaded data/parser/hierarchy — originally on a simpler (non-rolling) architecture, later
  superseded by the 2026-08-05 redesign above.
- Hierarchy-based email routing (`app/work_distribution_notification_service.py`) was designed
  (recipient-resolution rules defined) but **sending was never wired to a real Send button** — still true
  as of the 2026-08-05 audit, see `PROJECT_STATUS.md`.

## Through 2026-07-29 — see `V2_MIGRATION_LOG.md` for full detail

Condensed timeline (milestone numbers refer to that file):

- **M1–M11b** (2026-07-25 – 26): Supabase connectivity, login screen, real authentication, RBAC
  foundation (roles/profiles tables, role loading, module-level access gating), User Management
  dashboard (list, then write operations), first cloud-sync architecture (generic per-module JSON config
  sync), Development release channel, and a packaging investigation that turned out to be a missing
  `.env` in the frozen build.
- **M12–M21** (2026-07-26): Full Path Validator cloud sync (imports, findings, email notifications,
  organization workbooks, storage buckets, a cross-machine sync poller), confirmed the sync framework is
  genuinely generic (zero Path-Validator-specific knowledge leaked into the shared service), version bump
  + exe build + installer, a module-wide manual Refresh button.
- **M22–M33** (2026-07-26): The same cloud-sync + Refresh pattern rolled out to Inventory Monitoring, then
  Payment Analytics — each a thin per-module wrapper over the same generic primitives.
- **M34–M38** (2026-07-27): A single app-wide Last-Modified-Wins conflict-resolution rule, retrofitted
  across every module's sync (deletion is explicitly out of scope for this rule — a known, flagged
  trade-off). Two-laptop verification performed; a pre-existing Payments data-loss bug found and fixed in
  the process.
- **M39** (v1.3.1-dev.0.7): Centralized hierarchy/fallback service.
- **Version 2.0.0 production release**: a real `datetime`-not-JSON-serializable bug found during final
  validation against live data (578 employees, 142 findings) in the findings/email sync services — fixed
  in both `Saffron Automation - Main` and (then) `Saffron Automation v2.0 - Development`.
- **M40** (2026-07-27): **`2.0 dev` created** as a fresh full copy of `Saffron Automation - Main`
  (excluding build artifacts) — the old `Saffron Automation v2.0 - Development` folder explicitly marked
  superseded/deprecated at this point. (This is the folder later renamed to
  `Saffron Automation v2.1 - Development` on 2026-08-05, see the top of this file.) Also: Inventory Phase
  1 — exclude Ahmedabad CWH from CFA calculations.
- **M41–M42** (2026-07-27): Inventory CWH page; fixed real CWH stock reading + configurable CWH
  multiplier.
- **M43–M44** (2026-07-28): Critical redesign of Path Validator's email routing engine — simplified to
  BM/ABM → RBM only, no fallback chain.
- **M45–M53** (2026-07-28): A string of Inventory Replenishment fixes (pack-rounded deficit, CWH shortage
  highlighting, CFA threshold multiplier root-causes, packing-parser correctness, a hard 10-or-72 packing
  size rule with one-time data repair) culminating in **M53: the true root cause** — the cloud sync layer
  was silently reverting local data.
- **M54–M55** (2026-07-28): A reusable Table Export framework, then rolled out application-wide.
- **M56** (2026-07-29): Inventory Automated Email system foundation.
- **M57** (2026-07-29): Inventory Excel format migration to a new one-row-per-product layout — parsing
  layer only, zero changes to downstream Thresholds/Replenishment/CWH/Export/Email logic (verified
  end-to-end against a scratch database).
