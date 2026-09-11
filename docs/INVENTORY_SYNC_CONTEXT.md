# Inventory Sync — Architectural Context

Read-only research for extending the sync mechanism (`docs/SYNC_DESIGN.md`) to
the Inventory module. No code was touched to produce this report.

## 1. Current upload UI/flow, exactly as it works today

There is **no combined upload action**. Sales Report and Inventory Report are
two structurally independent widgets, each a separate `ctk.CTkFrame` subclass
with its own browse button, background thread, validation call, and status
label:

- `InventoryUploadPage` (`ui/inventory_upload_page.py:36-203`) — its own
  `_on_browse_clicked` (`ui/inventory_upload_page.py:94`), calling
  `validate_inventory_report` then `evaluate_replenishment` +
  `evaluate_cwh_stock` (`ui/inventory_upload_page.py:132-149`).
- `SalesUploadPage` (`ui/sales_upload_page.py:28-184`) — its own
  `_on_browse_clicked` (`ui/sales_upload_page.py:87`), calling
  `load_sales_report` then `generate_thresholds_from_sales`
  (`ui/sales_upload_page.py:125-130`).

`InventoryUploadsPage` (`ui/inventory_uploads_page.py:25-43`) only stacks the
two on one screen. Its own module docstring is explicit that this is "**Pure
UI composition, nothing else**... Neither class's own upload/validation/
processing logic is touched in any way" (`ui/inventory_uploads_page.py:6-11`),
and the constructor literally just packs both (`ui/inventory_uploads_page.py:41-42`):

```python
InventoryUploadPage(outer).pack(fill="x")
SalesUploadPage(outer).pack(fill="x")
```

**Correction to the stated premise:** the brief's context says "Sales Report
and Inventory Report currently must BOTH be selected on every upload." That is
**not what the code does**. Nothing requires the other file to be present,
selected, or even to have ever been uploaded — each button independently
triggers its own file dialog, its own validation, and its own downstream
compute; see Q3 below for what happens when one is uploaded with the other
never having run. The only coupling that exists is **visual**: both widgets
happen to render on one nav page (`ui/inventory_module.py:128` puts
`InventoryUploadsPage` under the single "Uploads" nav item, `ui/inventory_module.py:26`).
If the two are already independent at the action level, the "decoupling" work
for sync purposes is not a UI/validation change — see Open Questions §7.4.

## 2. Does every Inventory Report upload recompute thresholds, or only
   rebuild replenishment/cwh_stock reading thresholds as-is?

**Confirmed: only the latter.** An Inventory Report upload never touches
`InventoryThreshold`. It only rebuilds `InventoryReplenishment` and `CwhStock`,
reading `InventoryThreshold` as read-only input:

- `evaluate_replenishment()` (`app/replenishment_service.py:133-284`) — its own
  module docstring states this directly: "this module ONLY compares inventory
  against stored thresholds and persists the evaluation. It does NOT
  recalculate thresholds (thresholds are read-only input here, via
  `app.threshold_service.get_thresholds_lookup`)" (`app/replenishment_service.py:5-9`).
  The function calls `get_thresholds_lookup()` once at `app/replenishment_service.py:173`
  and only deletes/rebuilds `InventoryReplenishment` (`app/replenishment_service.py:186, 260-261`).
- `evaluate_cwh_stock()` (`app/cwh_service.py:74-221`) — its module docstring:
  "Does NOT touch `InventoryThreshold` or `InventoryReplenishment`, and does
  NOT change `generate_thresholds_from_sales()` or `evaluate_replenishment()`"
  (`app/cwh_service.py:34-36`). It reads `InventoryThreshold` rows directly via
  `session.query(InventoryThreshold).all()` (`app/cwh_service.py:144`) purely to
  sum `previous_month_sales`, and only deletes/rebuilds `CwhStock`
  (`app/cwh_service.py:158, 187-201`).

Thresholds are written in exactly one place: `generate_thresholds_from_sales()`
(`app/threshold_service.py:330-472`), which is called only from the Sales
Report upload path (`ui/sales_upload_page.py:130`). This deletes and rebuilds
the entire `InventoryThreshold` table (`app/threshold_service.py:434-436`).

**The earlier review's claim is correct.** `InventoryReplenishment` and
`CwhStock` are snapshots computed against whatever `InventoryThreshold` rows
happen to be live at the moment an Inventory Report is processed — not
recomputed together with thresholds. This is also stated as a deliberate
design point in the `InventoryThreshold` model docstring: "Full-replacement
table: a Previous Month Sales Report upload is a complete monthly snapshot...
A CFA+item combination absent from the newest upload no longer has a row at
all" (`database/models.py:480-486`), independent of whatever `InventoryReplenishment`/
`CwhStock` currently contain.

## 3. Uploading only an Inventory Report when `inventory_thresholds` is empty

**No error, no crash — silently produces a zero-evaluated (not "wrong")
result**, by design of the existing skip logic:

- `get_thresholds_lookup()` on an empty table returns `{}` (`app/threshold_service.py:504-527`,
  the dict comprehension over `session.query(InventoryThreshold).all()` — zero
  rows, empty dict).
- In `evaluate_replenishment()`, every row's `thresholds.get((cfa_key, item_key))`
  (`app/replenishment_service.py:209`) returns `None`, so every row hits the
  `skipped_no_threshold` branch (`app/replenishment_service.py:210-216`) and no
  `InventoryReplenishment` row is created for it. The function still runs its
  full delete-then-rebuild (deletes whatever old rows existed,
  `app/replenishment_service.py:186`), commits an empty insert, and returns
  normally with `evaluated: 0, replenishment_required: 0, healthy: 0,
  skipped_no_threshold: <rows_processed>` (`app/replenishment_service.py:277-284`).
  The UI reports this as a *success* message: "N product(s) evaluated, 0
  requiring replenishment" where N is 0 (`ui/inventory_upload_page.py:191-197`).
- In `evaluate_cwh_stock()`, `sales_by_item` is built from the same empty
  `InventoryThreshold` query (`app/cwh_service.py:144-149`), so it's `{}`.
  `all_item_keys` becomes just whatever CWH rows this upload itself contains
  (`app/cwh_service.py:156`), each with `total_previous_month_sales = 0.0` and
  therefore `cwh_threshold = 0.0` (`app/cwh_service.py:182-183`), which
  `_cwh_status()` always reports as `Healthy` regardless of stock
  (`app/cwh_service.py:69-71`, explicit in its own docstring: "No demand at
  all... is always Healthy regardless of stock").
- `get_excess_inventory()` reads the now-empty `InventoryReplenishment` table
  (`app/excess_inventory_service.py:95-99`) and returns `[]` — the Excess
  Inventory page shows nothing, not an error.

Net effect on a fresh machine that uploads only the Inventory Report: every
page that depends on thresholds (Replenishment, Excess Inventory, CWH) shows
an empty/zero/all-healthy state, with a *successful*-looking upload
confirmation. There is no error message and no "thresholds missing" warning
anywhere in this path — this is the exact silent-wrong-numbers-shaped failure
mode the sync design needs to prevent (a fresh machine that pulls only the
Inventory Report manifest slot before ever pulling the Sales Report slot will
look successful while reporting nothing needs replenishment).

## 4. File retention

**Confirmed: no current Inventory path retains either uploaded file**, same as
every module besides Review System. `docs/SYNC_DESIGN.md` states this directly
as an existing invariant: "today only the Review System retains its source
file locally; every other module's upload path needs that added as a
prerequisite for this design" (`docs/SYNC_DESIGN.md:157-159`), and lists
Inventory's rebuild recipe as "Download → re-run existing parser (full
delete-then-rebuild, same as today)" (`docs/SYNC_DESIGN.md:58`).

Confirmed independently from the upload pages themselves: `InventoryUploadPage`
and `SalesUploadPage` each only hold the picked path in an in-memory instance
attribute (`self._inventory_report_path`, `ui/inventory_upload_page.py:41,187`;
`self._sales_report_path`, `ui/sales_upload_page.py:33,167`) — never written
anywhere else, never passed to any service function as a path (both
`evaluate_replenishment`/`evaluate_cwh_stock`/`generate_thresholds_from_sales`
take an in-memory `pd.DataFrame`, not a file path — see their signatures at
`app/replenishment_service.py:133`, `app/cwh_service.py:74`,
`app/threshold_service.py:330`). No `database/models.py` model in this module
(`InventoryThreshold`, `InventoryReplenishment`, `CwhStock`) has a file-path or
blob column.

## 5. Validation requirements

Both file types go through the shared engine `_validate_and_load()`
(`app/excel_validation.py:264-361`), with format tried newest-first for the
Inventory Report specifically (`validate_inventory_report()`,
`app/excel_validation.py:856-904`).

- **Supported extensions** (checked twice — once by the UI before dispatch,
  once defensively inside the engine): `.xlsx`, `.xls`, `.xlsm`, `.csv`
  (`app/excel_validation.py:66`, enforced at `ui/inventory_upload_page.py:113`,
  `ui/sales_upload_page.py:106`, and defensively at `app/excel_validation.py:286-294`).
- **Required columns**:
  - Inventory Report (flat/legacy format):
    `BranchLocation, Item Group, Item Code, Item Name, TotalQty, Transit Stock`
    (`app/excel_validation.py:68-75`). Two newer formats are tried first and
    are structurally detected rather than column-matched — the pivoted-by-CFA
    format (`validate_pivoted_inventory_report`, `app/excel_validation.py:449-601`)
    and the current one-row-per-product format
    (`validate_new_format_inventory_report`, `app/excel_validation.py:694-853`).
  - Sales Report: `Division, CFA, Item Name, Packing, Sales`
    (`app/excel_validation.py:77-83`).
  - Column matching is case/whitespace/trailing-period-insensitive
    (`_normalize_header`, `app/excel_validation.py:107-119`); header row is
    found by scanning up to `MAX_HEADER_SCAN_ROWS = 100` rows
    (`app/excel_validation.py:64`), not assumed to be row 0.
- **No file-size limit or row-count limit found anywhere in this path** — grep
  of `app/excel_validation.py`, `ui/inventory_upload_page.py`, and
  `ui/sales_upload_page.py` surfaces no size check; the only bound is the
  100-row header-scan cap (`app/excel_validation.py:64`), which does not limit
  actual data rows loaded (`app/excel_validation.py:47-53` explicitly notes
  the real data load via `pd.read_excel` is "unbounded and unchanged").
  NOT FOUND — no upstream (OS file dialog, upload widget) size cap either.
- **Outright rejection**: unsupported extension (`ui/inventory_upload_page.py:113-119`,
  `ui/sales_upload_page.py:106-112`) and missing required columns/undetected
  header (`app/excel_validation.py:306-318`, surfaced as a bulleted
  "Invalid Inventory/Sales Report" dialog — `ui/inventory_upload_page.py:171-185`,
  `ui/sales_upload_page.py:155-165`). Any exception while opening/parsing the
  file (corrupt file, decode failure, etc.) is also caught and surfaced as an
  error, never raised (`app/excel_validation.py:296-304, 340-345`).

## 6. Other readers of `inventory_thresholds`

Exhaustive grep of `get_thresholds_lookup`/`get_all_thresholds` call sites
(the only two accessors defined for this table,
`app/threshold_service.py:475, 504`), plus `cwh_service.py`'s direct query,
gives exactly these readers:

| Reader | Call site | Live or snapshot |
|---|---|---|
| Thresholds page | `ui/inventory_thresholds_page.py:60` → `get_all_thresholds()` | Live, on every page view (`on_show`) |
| Excess Inventory page | `app/excess_inventory_service.py:90` → `get_thresholds_lookup()` | Live, on every call to `get_excess_inventory()` |
| Replenishment page | `app/replenishment_service.py:347` → `get_thresholds_lookup()` in `get_replenishment_required()` | Live, on every page view |
| Replenishment upload-time evaluation | `app/replenishment_service.py:173` in `evaluate_replenishment()` | Live, read once at upload time, then snapshotted into `InventoryReplenishment` |
| CWH page | `app/cwh_service.py:236` (`session.query(InventoryThreshold)`) in `get_cwh_overview()` | Live, but only for `division` join (display only) — everything else is a pure read of already-stored `CwhStock` (`app/cwh_service.py:224-232`) |
| CWH upload-time evaluation | `app/cwh_service.py:144` in `evaluate_cwh_stock()` | Live, read once at upload time, then snapshotted into `CwhStock` |

Dashboard (`ui/inventory_dashboard_page.py:10,41`) reads only
`get_replenishment_summary()` (from `InventoryReplenishment`, not
`InventoryThreshold` directly), so it is affected transitively, one hop
removed.

**Practical implication for staleness:** the Thresholds page and the Excess
Inventory page read `InventoryThreshold` live on every view — if Sales stays
locked for a long time, both will keep showing the same (correctly
unchanging) numbers with no staleness indicator of any kind. Neither page
displays `InventoryThreshold.last_updated` prominently as a "data as of" banner
— the Thresholds page includes it as a per-row column
(`app/threshold_service.py:496`) but there's no page-level "Sales Report last
updated: <date>" indicator anywhere in the module. NOT FOUND — no staleness
warning UI exists today for any of these pages.

## 7. Open questions for the owner

1. **What does "decoupled" mean given §1's finding?** The upload *actions* are
   already independent — nothing in the code requires re-selecting Sales on
   an Inventory Report upload today. If the ask is really about the **sync
   design**, the likely gap is that `docs/SYNC_DESIGN.md`'s per-module table
   currently treats Inventory as one row with one combined "What's uploaded:
   Sales Report, Inventory Report" / one recipe (`docs/SYNC_DESIGN.md:58`) —
   implying one manifest `slot`. Does "decoupled" mean: two separate manifest
   slots (e.g. `sales_report`, `inventory_report`) with independent
   `sequence`/`uploaded_at`, so a machine can pull a new Inventory Report
   without re-pulling Sales, and vice versa? If so this is a sync-manifest
   granularity decision, not an app-code change to the upload UI.
2. **Should a client pull-and-rebuild ever run Inventory Report replay against
   the LOCAL machine's already-stale `InventoryThreshold`, or must it verify a
   Sales Report slot has been pulled at least once first?** §3 shows uploading
   Inventory Report against empty/stale thresholds is silent, not an error —
   sync will reproduce whatever staleness already exists locally today, plus
   potentially introduce staleness that didn't exist before (e.g. Machine A
   never uploaded Sales locally, pulls only Inventory Report from Machine B).
   Does the sync design need an explicit "no thresholds pulled yet" gate/banner
   that the current single-machine flow has never needed (because on one
   machine, Sales is uploaded at least once by a human who presumably knows to
   do so)?
3. **Should the Sales Report's one-time/rare-upload framing be visible to the
   user anywhere** (e.g. "Thresholds last generated: <date>" on the Uploads
   page or Dashboard), or is this purely a backend/sync distinction with no UI
   change? Not decided; don't build either direction until answered.
4. **Retention granularity for Sales Report replay.** Per
   `docs/SYNC_DESIGN.md:58`, Inventory's rebuild recipe is "full
   delete-then-rebuild, same as today" — for Sales specifically, does a new
   machine joining late need only the SINGLE latest Sales Report file (since
   `generate_thresholds_from_sales` is itself full-replacement,
   `app/threshold_service.py:428-436`), or does something else in this module
   depend on Sales Report history the way Payments' Monthly upload does
   (`docs/SYNC_DESIGN.md:59`)? From the code read for this report, nothing
   found suggests Inventory needs anything but the latest file of each type —
   confirm this assumption before scoping the manifest slot as "latest only."
