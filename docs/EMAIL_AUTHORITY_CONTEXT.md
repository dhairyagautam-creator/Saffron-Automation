# Email Authority — Architectural Context

Read-only fact-finding report. No code, no migrations were touched to produce
this. Every claim below is cited as `path:line`; anything not found or
ambiguous is marked NOT FOUND / UNCLEAR rather than inferred.

Scope note up front: an older, generic cross-machine sync system used to
exist for Path Validator, Inventory, and Payment Analytics data
(`supabase/migrations/0022_drop_sync_infrastructure.sql:1-40`), but the file
that would drop its Supabase-side tables is explicitly marked
"NOT been executed anywhere" (`0022_drop_sync_infrastructure.sql:3-4`), and
the application code that used it is already gone. It was never a
manifest/seq design — it was a generic mirrored-table push/pull store
(`public.module_configurations`, `0022_drop_sync_infrastructure.sql:20-23`).
Treat it as dead, not as prior art for section 4.

---

## 1. Every automated email trigger

### Path Validator

- **Trigger site (the only one):** `ui/operations_page.py:687-688`, inside
  `_on_run_analysis_clicked` (method starts `ui/operations_page.py:578`).
  Fires at the end of the upload+rule-run pipeline, gated by
  `is_automatic_sending_enabled()` (`app/email_settings_service.py`,
  imported at `ui/operations_page.py:31`). When the toggle is off, the same
  method calls `_start_preview_only` instead
  (`ui/operations_page.py:689-690`), which builds the same batch but never
  opens an SMTP connection (`ui/operations_page.py:717-724`).
- **What sends:** `send_all_emails(import_id, ...)`,
  `app/notification_service.py:1059`. (Preview-only path calls
  `preview_email_batch`, `app/notification_service.py:986`.)
- **Logs to:** `email_notifications` table
  (`database/models.py:321,329` — class `EmailNotification`). Insert sites
  inside `send_all_emails`: `app/notification_service.py:1115,1130,1159,1176`
  (per-finding-type sends — manager notifications and low-working-hours
  notifications are logged as separate rows).

### Inventory

- **Trigger site (the only one, confirmed by the module's own docstring —
  "the ONLY report actually wired up today,"
  `app/inventory_notification_service.py:46-49`):**
  `ui/inventory_upload_page.py:214-215`, inside the upload page's `on_done`
  callback for `_on_browse_clicked` (method starts
  `ui/inventory_upload_page.py:106`, `run_in_background` call at
  `ui/inventory_upload_page.py:217`). Fires after Inventory Report
  upload+validation succeeds, gated by `is_automatic_sending_enabled()`
  (`app/inventory_email_settings_service.py`, imported at
  `ui/inventory_upload_page.py:37`). Calls
  `_start_automatic_send` (`ui/inventory_upload_page.py:221`).
- **What sends:** `send_inventory_replenishment_emails()`,
  `app/inventory_notification_service.py:340`, called at
  `ui/inventory_upload_page.py:235`. This is a thin wrapper
  (`app/inventory_notification_service.py:46-49`) around the generic
  `send_report_batch()` (used for future report types too, per that file's
  own docstring, `app/inventory_notification_service.py:26-51`).
- **Logs to:** `inventory_email_notifications` table
  (`database/models.py:1234,1260` — class `InventoryEmailNotification`).
  Insert sites (inside the generic `send_report_batch`):
  `app/inventory_notification_service.py:238,270,287`.
- Confirmed exhaustively: `send_inventory_replenishment_emails` has exactly
  one caller anywhere in the repo (grep for the symbol name returns only its
  own definition and this one call site).

### Payment Analytics

- **NOT FOUND.** No automated email trigger exists for this module. Grepped
  every `*payment*.py` file in the repo for `smtp`, `send_email`,
  `EmailNotification`, `notification_service`, and `automatic_sending` —
  zero matches. There is no `payment_email_notifications`-style table in
  `database/models.py`, no `payment_notification_service.py`, and no
  `payment_email_settings_service.py`. This module has no send function, no
  settings toggle, and no logging table at all — not "currently disabled,"
  genuinely absent.

### Work Distribution

Two automated trigger sites, both routing through the same shared method:

- **Trigger site 1 (RGD Coverage):** `ui/work_distribution_upload_page.py:576`,
  inside `_on_run_analysis_clicked` (method starts
  `ui/work_distribution_upload_page.py:527`).
- **Trigger site 2 (Manager Work Allocation):**
  `ui/work_distribution_upload_page.py:416`, inside
  `_on_mwa_run_analysis_clicked` (method starts
  `ui/work_distribution_upload_page.py:360`).
- Both call the shared `_maybe_auto_send_notifications`
  (`ui/work_distribution_upload_page.py:102-116`), which checks
  `is_automatic_sending_enabled()`
  (`app/work_distribution_email_settings_service.py`, imported at
  `ui/work_distribution_upload_page.py:58`) at
  `ui/work_distribution_upload_page.py:106`, then calls
  `build_notification_batch()` (`ui/work_distribution_upload_page.py:113`)
  and `send_notification_batch(...)`
  (`ui/work_distribution_upload_page.py:116`). This dual-trigger, one-method
  shape is documented explicitly in the file's own module docstring
  (`ui/work_distribution_upload_page.py:33-43`): "BOTH Run Analysis buttons
  (RGD Coverage and Manager Work Allocation) automatically build and send
  every currently flagged employee's notification right after their own
  analysis completes."
- **What sends:** `build_notification_batch()` /
  `send_notification_batch(...)`,
  `app/work_distribution_notification_service.py:468,647`.
- **Logs to:** `work_distribution_email_notifications` table
  (`database/models.py:1276,1305` — class
  `WorkDistributionEmailNotification`). Insert sites:
  `app/work_distribution_notification_service.py:687,701`.
- **Pre-existing manual send (not automated, but directly relevant — see
  §5):** `ui/work_distribution_email_center_page.py:520-546`
  (`_on_send_clicked` → `send_notification_batch`, same underlying
  function). The module docstring is explicit that when automatic sending
  is off, "the Email Center's manual Preview/Send controls are the only way
  to send" (`ui/work_distribution_upload_page.py:41-43`). This page already
  has a Preview button too (`_on_preview_clicked`,
  `ui/work_distribution_email_center_page.py:473-481`).

### Review System

- **Trigger site (the only one — scoped to Coverage Summary only, see
  below):** `ui/review_file_preview_page.py:484-485`, inside
  `_start_generation`'s `on_done` callback (method starts
  `ui/review_file_preview_page.py:461`). Fires only when
  `report_type == "Coverage Summary"` **and**
  `coverage_automatic_sending_enabled()`
  (`app/review_coverage_email_settings_service.py`, imported at
  `ui/review_file_preview_page.py:32-35`). The trigger event here is
  **report generation** (clicking "Generate" for the Coverage Summary
  report for one division), not raw file upload — structurally different
  from the other four modules, which all trigger on upload/analysis
  completion.
- **Scope is explicitly Coverage-Summary-only.** Per
  `ui/review_file_preview_page.py:311-317` ("Only shown for the Coverage
  Summary report type; Opus Summary and RGD Visit and Support have no email
  workflow of their own"), confirmed independently by grep: no
  `review_opus*.py` or `review_rgd*.py` file references
  `notification_service`, `send_notification`, `build_notification`, or any
  `EmailNotification` class anywhere in the repo.
- **What sends:** `build_notification_batch(division)` /
  `send_notification_batch(drafts)`,
  `app/review_coverage_notification_service.py:113,189`, called from
  `_send_coverage_emails` (`ui/review_file_preview_page.py:343-353`).
- **Logs to:** `review_coverage_email_notifications` table
  (`database/models.py:247,265` — class `ReviewCoverageEmailNotification`).
  Insert sites: `app/review_coverage_notification_service.py:229,244`.
- **Pre-existing manual send (not automated, but directly relevant — see
  §5):** a "Send Emails Now" button
  (`ui/review_file_preview_page.py:324-330`) wired to the same
  `_send_coverage_emails` method the automatic path calls, plus a per-page
  "Send automatically after each generation" switch
  (`ui/review_file_preview_page.py:332-341`).

### Cross-module note: "is currently sending" state is not shared

Path Validator and Inventory each track in-flight-send state via their own
separate in-memory singleton (`app/send_state.py`,
`app/inventory_send_state.py` respectively) — confirmed these are the only
two files anywhere importing anything from a `*send_state` module. Work
Distribution and Review System have no equivalent module; their send
buttons disable themselves directly instead
(e.g. `ui/review_file_preview_page.py:344-345`). There is no single
app-wide "is any email send in progress" flag today.

---

## 2. Current RBAC mechanics

- **`user_module_permissions`** (`supabase/migrations/0020_module_based_permissions.sql:54-59`):
  one row per `(user_id, module_key)`. Presence of the row IS the grant —
  no boolean column, so revoking is a DELETE
  (`0020_module_based_permissions.sql:46-48`). `module_key` is a free-form
  `text` column, deliberately not a foreign key into any Postgres-side list
  (`0020_module_based_permissions.sql:48-52`) — it only has to match
  whatever key the Python-side registry currently defines.
- **`is_super_admin`** (`profiles.is_super_admin`, added at
  `0020_module_based_permissions.sql:31-32`): a single boolean flag, not
  modeled as "has every module_key row," specifically so a super admin
  needs zero new rows when a module is added later
  (`0020_module_based_permissions.sql:24-29`).
- **`app/rbac_service.py:load_profile_and_permissions`**
  (`app/rbac_service.py:45-128`) runs once after sign-in: fetches the
  `profiles` row (`app/rbac_service.py:67`), then every
  `user_module_permissions` row for that user
  (`app/rbac_service.py:98-100`), reduces it to a `frozenset` of keys
  (`app/rbac_service.py:111`), and stores both on an
  `app.rbac_state.Profile` (`app/rbac_service.py:113-121`,
  `app/rbac_state.py:28-35`) — an in-memory, process-wide, frozen dataclass
  (`app/rbac_state.py:28-46`), never re-queried mid-session.
- **`app/permissions.py:can_access(module_key)`**
  (`app/permissions.py:21-33`) is the single gate every screen calls
  (per its own docstring, `app/permissions.py:5-9`): returns `False` if
  nobody is signed in (`app/permissions.py:29-30`), `True` unconditionally
  if `profile.is_super_admin` (`app/permissions.py:31-32` — checked
  **before**, and independently of, `module_keys`), otherwise
  `module_key in profile.module_keys` (`app/permissions.py:33`). Fails
  closed throughout (`app/permissions.py:11-13`).
- **Modules added after 0020 ran:** because the super-admin check
  (`app/permissions.py:31-32`) is a flag check, not a per-module-row
  lookup, a super admin automatically gets access to a module registered
  later in `app/module_registry.py`'s `MODULES` tuple
  (`app/module_registry.py:37-92`) with **zero** database changes —
  confirmed by `app/module_registry.py:10-14`'s own docstring ("Adding a
  new module means adding one `ModuleDef` here... no other file needs to
  change") and by `ui/user_dialogs.py:16-18` ("A Super Admin checkbox
  grants every module, including ones added later, without checking each
  box"). `review_system` itself is a live example of this: it is registered
  in `module_registry.py:85-91` and nothing in `0020_module_based_permissions.sql`
  (which predates it) mentions it by name.
- **`is_user_management_admin()`** (redefined at
  `0020_module_based_permissions.sql:121-141`, `SECURITY DEFINER`, to avoid
  RLS self-recursion per migration 0007) is the Postgres-side gate for
  every `user_module_permissions` RLS policy
  (`0020_module_based_permissions.sql:149-180`) and for `get_all_users()`
  (`0020_module_based_permissions.sql:204-238`) — checks
  `is_super_admin` OR a `user_management` row
  (`0020_module_based_permissions.sql:132-139`), structurally identical to
  the Python-side `can_access()` check.

---

## 3. User Management UI

- Built entirely from `app.module_registry.all_modules()`
  (`ui/user_dialogs.py:13-14,129`) — no network round trip, no hardcoded
  module list; per `app/module_registry.py:10-14`, a new `ModuleDef` shows
  up here with no other file changes needed.
- **Rendering:** `UserFormDialog._build_ui`
  (`ui/user_dialogs.py:73-152`). A "Super Admin" checkbox
  (`ui/user_dialogs.py:113-117`) sits above a plain (non-scrolling)
  `CTkFrame` container, `modules_frame`
  (`ui/user_dialogs.py:126-127`, deliberately not its own
  `CTkScrollableFrame` — the whole dialog already scrolls,
  `ui/user_dialogs.py:91,122-125`). Inside it, one `CTkCheckBox` per module
  is created in a loop (`ui/user_dialogs.py:128-134`):
  `for module in module_registry.all_modules(): ... checkbox = ctk.CTkCheckBox(modules_frame, text=module.title, variable=var); checkbox.pack(anchor="w", pady=2, padx=Spacing.SM)`.
  Each checkbox's `BooleanVar` is keyed into `self._module_vars` by
  `module.key` (`ui/user_dialogs.py:130-131`).
- **Toggling / interaction with Super Admin:** when Super Admin is checked,
  every module checkbox is disabled (not hidden) via
  `_apply_super_admin_lock` (`ui/user_dialogs.py:157-167`) —
  `state = "disabled" if self._super_admin_var.get() else "normal"`
  applied to every entry in `self._module_checkboxes`
  (`ui/user_dialogs.py:165-167`). Values underneath are preserved, not
  cleared (`ui/user_dialogs.py:162-164`). This is the closest existing
  precedent in the codebase for a parent-controls-child checkbox
  relationship, though it disables rather than un-checks.
- **Save path:** `_handle_save` (`ui/user_dialogs.py:197-231`) collects
  `module_keys = {key for key, var in self._module_vars.items() if var.get()}`
  (`ui/user_dialogs.py:201`) and hands the whole dict to `on_submit`
  (`ui/user_dialogs.py:230-231`), which the caller
  (`ui/user_management_page.py` — not read for this report) wires to
  `app.user_management_service.create_user` /
  `update_user`.
- **Persistence:** `_replace_module_grants(client, user_id, module_keys)`
  (`app/user_management_service.py:130-144`) does a full delete-then-reinsert
  of that user's `user_module_permissions` rows
  (`app/user_management_service.py:139-144`) — "the whole grant set is"
  replaced atomically per its own docstring
  (`app/user_management_service.py:131-138`), called from both
  `create_user` (`app/user_management_service.py:255`) and `update_user`
  (`app/user_management_service.py:299`).
- **Structural fit for a nested sub-checkbox:** `ModuleDef`
  (`app/module_registry.py:28-34`) is a flat dataclass — `key`,
  `screen_name`, `title`, `description`, `icon_name` — with no field for
  children or sub-permissions. `user_module_permissions`
  (`0020_module_based_permissions.sql:54-59`) and
  `_replace_module_grants` (`app/user_management_service.py:130-144`) both
  treat `module_keys` as one flat, unordered set of strings with no
  relationship between entries — nothing today models "this key requires
  that key." The checklist loop itself
  (`ui/user_dialogs.py:128-134`) would accept an indented child widget
  packed right after a given module's checkbox without restructuring (it's
  a plain `.pack(anchor="w", ...)` sequence in a plain frame, not a grid or
  fixed-position layout), but the "cannot exist without the parent; revoked
  with it" cascade rule does not exist anywhere in the current code and
  would need to be added — see §5.

---

## 4. Manifest/seq tracking

- **Only Review System has it.** `sync_manifest`
  (`supabase/migrations/0023_sync_manifest.sql:29-40`) is the only table
  anywhere in `supabase/migrations/` with a `generated always as identity`
  sequence column — confirmed by grepping every migration file for
  `manifest`, `generated always as identity`, and `\bseq\b`: only
  `0023_sync_manifest.sql` and `0024_sync_uploads_storage_update_policy.sql`
  (which only adds a Storage RLS policy, no new table) match.
- Local-only mirrors of this (`sync_state`, `sync_module_check`,
  `profile_name_cache` — `database/models.py:187,207,223`) are likewise
  Review-System-specific; `app/review_sync_service.py`'s `MODULE` constant
  is hardcoded to `"review_system"` (not verified against current file
  content in this report, per the file's size — see Files note below, but
  confirmed present from this session's own prior work).
- **What the other four modules have instead (none of it cross-machine):**
  - Path Validator: `import_history`
    (`database/models.py:14-24`, class `ImportHistory`) — local SQLite
    only, one row per import, autoincrement `id`, no Supabase mirror.
  - Work Distribution: `work_distribution_upload_log`
    (`database/models.py:1343`) — not read in detail for this report, but
    confirmed to exist locally; not seq/manifest-shaped.
  - Inventory, Payment Analytics: **NOT FOUND** — no dedicated
    upload-log-style table at all in `database/models.py`.
- **Conclusion:** seq tracking is Review-System-only, confirmed by direct
  search, not absence-of-evidence. Whatever "has new data arrived since
  last send" mechanism the other four modules use will need something new
  — nothing existing (local upload logs included) is visible across
  machines the way `sync_manifest.seq` is.

---

## 5. Open questions for me

These are things this investigation surfaced that need a decision before
scoping — not guessed at:

1. **Work Distribution and Review System (Coverage Summary) already have
   manual send UI predating this redesign** — Work Distribution's Email
   Center Preview/Send buttons
   (`ui/work_distribution_email_center_page.py:473-546`) and Review
   System's per-division "Send Emails Now" button
   (`ui/review_file_preview_page.py:324-330,343-353`). Does the new
   module-top "Send Emails" button *replace* these, coexist alongside them,
   or become the same button relocated? This affects two modules
   differently than the other three, which have no existing manual send UI
   at all.

2. **Review System's email workflow is Coverage-Summary-only, not
   module-wide** (`ui/review_file_preview_page.py:311-317`; confirmed no
   Opus/RGD email code exists at all). If the new button sits at the top of
   the Review System *module* screen rather than on the File Preview page
   specifically, what should it do — act only on Coverage Summary
   regardless of which report/division the user is currently viewing? Per
   division, or across all three at once? This needs an explicit decision,
   not an assumption carried over from the other four modules where one
   button naturally means "the module's one report."

3. **Payment Analytics has no existing send function, table, or settings
   service to reuse** (confirmed absent, §1). The stated premise —
   "the underlying send functions stay and get called by a new manual
   button instead" — has nothing to attach to for this module specifically.
   Is a full email system meant to be built from scratch for Payment
   Analytics as part of this work, or does its button stay permanently
   disabled ("No data to send" or equivalent) until that's scoped
   separately?

4. **The "new data since last send" guard's mechanism for the four
   non-Review modules is undecided by definition** (§4 found nothing to
   key it off). Options this report is not choosing between: reusing each
   module's existing local upload-log table where one exists (Path
   Validator, Work Distribution) even though it's per-machine and not
   cross-machine-aware like `sync_manifest.seq`; extending the
   Review-System-style Supabase manifest pattern to the other modules;
   or something else. Needs an explicit decision.

5. **Nothing today models "sub-permission requires parent module grant, and
   is revoked with it"** (§3). Two shapes are both structurally possible
   with today's flat `user_module_permissions` table and
   `_replace_module_grants` delete-and-reinsert
   (`app/user_management_service.py:130-144`) — e.g. storing the
   sub-permission as its own free-form key (the table already accepts any
   string, `0020_module_based_permissions.sql:48-52`) and enforcing the
   cascade purely client-side in `ui/user_dialogs.py` (mirroring the
   existing super-admin-disables-children pattern at
   `ui/user_dialogs.py:157-167`), versus something enforced at the
   `_replace_module_grants` layer or in Postgres. Not choosing between
   these here.

6. **`app/permissions.py` has no concept of a permission finer than a whole
   module today** — `can_access(module_key)` (`app/permissions.py:21-33`)
   is the only gate function, and it only ever answers "can this user open
   this module screen." An "email authority" sub-permission gate (used to
   grey out vs. enable the button) doesn't have an existing counterpart
   function to extend or mirror precisely — it would be new, not a
   variant of something already there.

7. **`ui/user_management_page.py` (the caller that wires `UserFormDialog`'s
   `on_submit` to `create_user`/`update_user`) was not read for this
   report** — named only via `app/user_management_service.py`'s import,
   not opened. If the sub-checkbox's persisted value needs to travel
   through that page's own submit-handling logic (beyond the dialog itself),
   that file needs a separate pass before scoping.
