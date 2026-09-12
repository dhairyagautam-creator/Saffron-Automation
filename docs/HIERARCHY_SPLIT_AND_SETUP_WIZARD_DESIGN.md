# Employee Hierarchy Split + Setup Wizard Removal — Design & Investigation

Status: Part A is a ready-to-execute build checklist. Part B is investigation
only — open design questions listed at the end, not yet decided.

---

## Part A — Remove the Setup Wizard entirely (ready to build)

**Decided, final.** Nothing but the Login screen appears on first run. This
is independent of Part B's hierarchy split — it does not depend on the table
strategy, `workbook_connections` schema, or any other open question below
being resolved first. It can be built on its own.

### Why this is safe — traced, not assumed

- `ui/main_window.py:79-92` already builds and selects the Login screen
  **before** checking `is_setup_completed()` — the wizard has never gated
  access to Login; it only ever sat in front of an already-hidden main
  window. `_on_setup_wizard_finished` (`ui/main_window.py:206-207`) is just
  `self.deiconify()`. No account-creation logic exists anywhere in
  `ui/setup_wizard.py` — confirmed by reading the full 246-line file.
- The wizard's own docstring (`ui/setup_wizard.py:8-10`) already states that
  skipping both content steps produces the exact same end state as a
  never-configured install — removing them is not new behavior, just
  removing a UI path to a state the app already fully supports.
- Exhaustive project-wide grep for `is_setup_completed`, `set_setup_completed`,
  `SetupWizard`, `app_state_service`, and `setup_wizard` confirms exactly six
  files touch any of this: `app/app_state_service.py` (definitions),
  `ui/main_window.py`, `ui/about_page.py`, `ui/setup_wizard.py` (all three
  removed/edited below), plus `database/models.py:471` and
  `database/migrations.py:198-218,1077` — schema/migration for the
  `setup_completed` **column**, untouched by this change (see "Explicitly
  out of scope" below).

### Build checklist

1. **Delete `ui/setup_wizard.py`** entirely (the `SetupWizard` class, all 246 lines).
2. **`ui/main_window.py`**:
   - Remove the gate at `:90-92`:
     ```python
     if not is_setup_completed():
         self.withdraw()
         SetupWizard(self, self._on_setup_wizard_finished)
     ```
   - Remove the now-unused imports: `from app.app_state_service import is_setup_completed` (`:22`), `from ui.setup_wizard import SetupWizard` (`:31`).
   - `_on_setup_wizard_finished` (`:206-207`) becomes unreachable dead code — remove it too.
3. **`ui/about_page.py`**: remove the "Reset Configuration" button and its handler, `:96-103`:
   ```python
   SecondaryButton(body, text="Reset Configuration", command=self._on_reset_clicked).pack(anchor="w")
   ...
   def _on_reset_clicked(self) -> None:
       set_setup_completed(False)
       messagebox.showinfo(...)
   ```
   Also remove the descriptive text above it (`:82-94`, the paragraph explaining what the button does) and the now-unused `from app.app_state_service import set_setup_completed` import (`:9`). Check whether removing this button leaves an empty container/section on the About page that needs its own cleanup (its immediate parent card/section) — read that page's layout before deleting to confirm nothing else depends on that widget's presence.
4. **Delete `app/app_state_service.py` entirely** — once steps 1–3 land, `is_setup_completed()`/`set_setup_completed()` (its only two functions) have zero remaining callers anywhere in the codebase.

### Explicitly out of scope — flagged, not touched

- `AppSettings.setup_completed` (`database/models.py:471`) and its migration
  `ensure_app_settings_setup_completed_column()` (`database/migrations.py:198-218`,
  invoked at `:1077`) are schema-level and were not part of what you asked
  about. They become unused once Part A lands (nothing reads or writes that
  column anymore), but removing a column is a migration-shaped decision on
  its own, separate from deleting the four Python-level call sites above. Left
  alone here; say if you want that pursued as its own follow-up.

---

## Part B — Employee Hierarchy 3-way split (investigation only, no decisions made)

### B1. Current architecture: one table, one connection set, four UI entry points

`employee_hierarchy` has no ORM model — it's rebuilt via
`pandas.to_sql(if_exists="replace")` in `app/hierarchy_parser.py:378`, behind
one constant, `HIERARCHY_TABLE = "employee_hierarchy"` (`app/hierarchy_parser.py:53`).
Every read/write function in that file goes through this one constant; no
other file hardcodes the literal table name.

**Four separate UI pages already call the identical `refresh_hierarchy()`
against the identical `workbook_connections` rows** — not two, as the
original framing assumed:

1. `ui/organization_data_page.py` — Path Validator's dedicated page.
2. `ui/work_distribution_email_center_page.py`'s "Hierarchy Workbooks" card — its own docstring: *"there is exactly one hierarchy dataset in the application; this page is a second, convenient place to manage it... not a separate parallel system"* (`:10-16`).
3. `ui/review_hierarchy_page.py` — Review System's own page, explicitly *"a THIRD place to view/refresh it"* (`:4-9`).
4. `ui/setup_wizard.py:167-169` — first-run onboarding (**removed by Part A** — this entry point disappears regardless of how the split is scoped).

All four write to the same `WorkbookConnection` rows (`app/workbook_connections.py`),
keyed only by `workbook_name` with a hard `unique=True` constraint
(`database/models.py:133`) — no module-scoping column exists. There is
currently nowhere to store "Path Validator's Onyx file" separately from
"Work Distribution's Onyx file"; they are the same row today.

### B2. Path Validator's own reads — computation-affecting vs. display-only

**Computation-affecting** (feeds what gets written to `InvestigationFinding`):
- `rules/same_location.py:110` (`get_all_designations()`), used at `:126` to filter `ANALYZABLE_DESIGNATIONS = {"BM", "ABM"}`. A missing/stale hierarchy row causes that employee's entire day-group to be **silently skipped from evaluation entirely** — not miscounted, never evaluated at all.
- `rules/hours_worked.py` — grepped for "hierarchy": zero matches. Does not read the table.

**Not computation-affecting:**
- `app/master_attention_service.py:186,188` — explicitly documented as a *"read-only analytics layer... WITHOUT importing or re-running any detector"* (`:4-8`); enriches already-written findings, never feeds back into a write.
- `app/notification_service.py:233,566` — recipient/manager-chain resolution and hospital-suppression for email only. Confirmed by `same_location.py`'s own docstring: suppression *"never removes a finding from this count or from the Findings page — it only withholds the notification"* (`:216-219`).

No SQLAlchemy model, no foreign key, no join anywhere references `employee_hierarchy` — confirmed via the two "not a foreign key" comments on the only two tables that snapshot a name/email from it (`database/models.py:314`, `:1354`), both plain string columns.

### B3. Work Distribution's own reads — confirmed exhaustive (from the prior trace)

Re-verified this session, no correction needed:
- `app/work_distribution_service.py:297-298,301` — DOJ lookup, computation-affecting (decides `NOT_YET_JOINED` vs. full KPI evaluation, written to `WorkDistributionFinding`).
- `app/manager_work_allocation_service.py:158-159,411-412` and `app/manager_work_allocation_rbm_service.py:330-331,540-541` — same DOJ mechanism via `app/doj_eligibility_service.py` (confirmed as an indirect-import chain, not a discrepancy with the prior report), computation-affecting for the `:158-159`/`:330-331` sites, display-only for the `:411-412`/`:540-541` `get_employee_bm_monthly_history()` sites.
- `app/work_distribution_notification_service.py` — confirmed downstream-email-only by its own docstring (*"no finding's status/reason is ever recomputed here"*, `:78-81`).

The shared mechanism underneath all of Work Distribution's and Path Validator's DOJ-eligibility logic — `app/doj_eligibility_service.py` — treats "no DOJ on file" and "employee not found in hierarchy at all" as the *same*, deliberate `ACTIVE` fallback (`app/doj_eligibility_service.py:18-23`): *"Never invented or inferred... Existing behavior is completely unchanged in every one of these cases."* Any split must preserve this fallback exactly — it is documented, intentional behavior, not a bug to fix along the way.

### B4. Parser parameterization — small in isolation, wider once callers are counted

`app/hierarchy_parser.py`'s own functions are trivially parameterizable — one
constant, `HIERARCHY_TABLE`, threaded through every read/write. `HIERARCHY_COLUMNS`
is independent of the table name, so the same schema applies to a second table
with zero column changes.

Two things make this bigger than a one-file change:
1. **~9 other files call these read functions** (all sites in B2/B3, plus
   `app/hierarchy_service.py`, `ui/hierarchy_table_section.py`,
   `app/review_coverage_notification_service.py`). Each needs to specify
   *which* table it means once there are two — mechanical per site, but real
   breadth.
2. **`workbook_connections` has the identical singularity problem one layer
   down** (see B1) — splitting `employee_hierarchy` without also addressing
   the `unique=True` constraint on `workbook_name` leaves both new tables
   fed from the same single file-per-division connection.

### B5. A third consumer the original two-way framing didn't account for

**Review System** (`ui/review_hierarchy_page.py`, `app/review_coverage_notification_service.py`)
reads the exact same shared table via the exact same `find_by_employee_code`/
`find_by_employee_name` functions, for the same kind of recipient resolution
Work Distribution's own notification code does. A Path-Validator/Work-Distribution
split as originally scoped did not say where Review System's dependency
goes — resolved in B6 below: Review System gets its own, third table.

`app/hierarchy_service.py` (fallback-chain / `is_valid_recipient` logic) is
itself a shared consumer across all three module families (Path Validator,
Work Distribution, Review System) — whichever table(s) each module ends up
pointed at, this module's own calls into `hierarchy_parser` need the same
table-selection treatment as everything else in B4.

### B6. Decided

**Table strategy — RESOLVED**: three new tables, one each for Path Validator,
Work Distribution, and Review System — not two. This directly follows from
B5's finding that Review System is a genuine third consumer, not something
the original two-way framing can leave unaddressed.

**Migration path for existing installs — RESOLVED**: no data migration. All
three new tables start genuinely empty at first launch post-split — today's
single populated `employee_hierarchy` table is neither copied nor read from
during the transition. Each module's hierarchy is re-uploaded separately,
by the user, after the split ships. This applies uniformly regardless of
whether an install currently has a populated table or not — there is no
"existing install gets migrated, fresh install starts empty" distinction;
every install starts every one of the three tables empty.

### B7. Open questions — decisions still needed before building Part B

1. **`workbook_connections` schema change**: how does a single `workbook_name` become distinguishable per module — a new scoping column, or distinct name strings (e.g. `"Onyx-PV"`/`"Onyx-WD"`/`"Onyx-Review"`)? This is a real schema decision, not just a parser change, and now needs to distinguish three modules, not two.
2. **UI consequence of B1's finding**: with four (now three, after Part A) existing entry points all pointed at one shared dataset today, does each surviving page (`organization_data_page.py`, `work_distribution_email_center_page.py`'s card, `review_hierarchy_page.py`) get repointed at its own module's table, or does the UI itself need to change shape?
3. **Naming/placement of the three new tables** — not yet specified (e.g. `path_validator_employee_hierarchy` / `work_distribution_employee_hierarchy` / `review_employee_hierarchy`, or some other convention), and whether `HIERARCHY_COLUMNS` stays identical across all three or diverges per module's actual needs.

These three remain open — they're the decisions still needed before Part B
moves from investigation to a build plan.
