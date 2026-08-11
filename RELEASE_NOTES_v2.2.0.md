# Saffron Automation v2.2.0 — Production Release

**Release date:** 2026-08-07
**Channel:** production

This release adds the complete **Work Distribution** module and brings a substantial round of
Inventory Monitoring and User Management improvements, along with the supporting fixes made since
v2.0.0. Existing installations upgrade in place — all local settings, uploaded data, and
configuration are preserved automatically.

---

## ✨ New Features

### Work Distribution module (new)
A complete new automation module for field-force coverage and manager work allocation, built over the
same upload/parser/hierarchy foundation as the existing modules.

- **RGD Coverage** — doctor-level coverage findings, calculated separately for the BM side
  ("B-RGD" category doctors) and the ABM side ("A-RGD" ABM RGD doctors). Each upload is a complete
  monthly snapshot (full replacement, not incremental).
- **Manager Work Allocation — ABM engine** — evaluates the **average** joint field days per BM across
  the retained window against a configurable threshold.
- **Manager Work Allocation — RBM engine** — evaluates the **sum** of joint field days per BM
  ("worked with at least once" = covered) against a BM-count-tiered missed-BM threshold. Only this
  business rule differs from the ABM engine; the underlying mechanics are shared code.
- **Rolling six-month window** — each uploaded report already contains several months of history, so a
  manager/BM pair legitimately recurs once per month it has a record for. The module now retains the
  newest six *distinct* months by month value (never by upload order or filename), merges only true
  within-month duplicates, and preserves genuine month-by-month history instead of collapsing it into a
  single total.
- **Employee Details trend view** — a dynamic month-by-month trend table, built fresh from whichever
  months are currently retained, shared by both engines.
- **New persistent store** — `manager_work_allocation_records`, the accumulating rolling-window table
  both engines write through.

---

## ⚡ Improvements

### Inventory Monitoring
- **Automated replenishment emails** — a configurable Automated Email system that sends the
  Replenishment report to configured recipients (optionally triggered automatically after a successful
  Inventory Report upload).
- **New Inventory Report format support** — added parsing for the current ERP one-row-per-product
  export, with automatic fallback to the older pivoted-by-CFA and flat-row layouts. Downstream
  Thresholds / Replenishment / CWH / Export / Email logic is unchanged.
- **Central Warehouse (CWH) view** — a dedicated page for Ahmedabad CWH physical stock, with a
  configurable CWH threshold multiplier. CWH is correctly treated as the parent warehouse and excluded
  from CFA replenishment evaluation.
- **Reusable table export** — a shared, styled Excel export framework rolled out across the
  application's data tables.

### User Management
- Refinements to the User Management dashboard and role-based access control (role-gated module access,
  user add/edit/deactivate operations).

---

## 🐛 Bug Fixes

- **Inventory data silently reverting after upload** — fixed the underlying cloud-sync behavior where a
  local inventory update could be overwritten by a stale cloud copy on refresh (the true root cause
  behind several earlier replenishment symptoms).
- **Replenishment deficit rounding** — stock deficit / replenishment quantity now always rounds **up**
  to the nearest pack size, never down.
- **CFA threshold multiplier** — corrected the root cause behind incorrect CFA-level thresholds.
- **Packing-size parsing** — corrected packing parsing, including a hardened pack-size rule and a
  one-time data repair for previously mis-parsed values.
- **CWH physical stock reading** — corrected reading of real Central Warehouse stock.
- **Path Validator email routing** — simplified and corrected the manager email-routing engine
  (BM/ABM → RBM).

---

## ⚠ Known Issues

- **Work Distribution email sending is not yet wired to a Send action.** Recipient resolution
  (hierarchy-based routing) is implemented, but there is no user-facing "Send" button in the module
  yet — notifications for Work Distribution cannot be dispatched from the UI in this release.
- **Deletions do not propagate through cloud sync.** The application-wide Last-Modified-Wins sync rule
  keeps the newer copy of a record but does not remove records deleted on one machine — a record
  deleted locally can reappear on the next Refresh if its cloud copy still exists. (Inventory Report
  uploads are exempt: they perform a full-replace push.) Carried forward from v2.0.0.

---

## Upgrade notes

- Existing installations upgrade in place — same install location and Add/Remove Programs entry.
- All local settings, uploaded data, and configuration are preserved automatically (stored separately
  from the application files, unaffected by this or any future update).
- No manual migration steps are required; database schema changes (including the new
  `manager_work_allocation_records` table) are applied automatically on first launch.
