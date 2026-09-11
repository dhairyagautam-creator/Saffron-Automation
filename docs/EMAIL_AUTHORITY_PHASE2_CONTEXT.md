# Email Authority Phase 2 — Shared Send-History: Architectural Context

Read-only report. No code, no migrations, nothing touched to produce this.
Every claim cited as `path:line`. NOT FOUND / UNCLEAR where genuinely absent
or ambiguous, not inferred.

**Read section 6 before assuming the rest of this is a straightforward build.**
It surfaces a problem in the DECIDED design (§ Context) that affects all
three modules, not just the two without sync yet.

---

## 1. Where "data changed" is actually detected today

### Path Validator — confirmed correct as decided

`ActiveSession.import_id` (`database/models.py:37`), set by
`set_active_import()` (`app/session_state.py:43-59`), called once per
import. No change needed here — already covered in the Phase 1 report and
unaffected by anything below.

### Inventory — Phase 1's hook point is the right one; confirmed, not assumed

`InventoryReplenishment.last_updated` (Phase 1's actual hook,
`app/inventory_notification_service.py:364-379`) is set inside
`evaluate_replenishment()` (`app/replenishment_service.py:132`), specifically
at the full delete-then-rebuild: `now = utcnow()` computed once
(`app/replenishment_service.py:177`), every row in the rebuild stamped with
that same `now` (`app/replenishment_service.py:256`), after the existing
table is cleared (`app/replenishment_service.py:185`). Since every row from
one upload shares one timestamp, `MAX(last_updated)` is exactly "when did
the last upload's rebuild happen" — there's no earlier point in the
pipeline that would be cleaner; this *is* the moment the data actually
changes, not just when a file was picked. Phase 1 got this right.

### Work Distribution — Phase 1's hook point is wrong; found a real gap

Phase 1 used `WorkDistributionUploadLog.uploaded_at`
(`app/work_distribution_notification_service.py:670-691`), populated by
`record_upload()` (`app/work_distribution_upload_log_service.py:14-28`).
That module's own docstring says outright: *"Pure activity logging: never
read by any parser, calculation, threshold, or finding — only by the
Dashboard's own display"* (`app/work_distribution_upload_log_service.py:1-4`),
and separately: logged *"at the point of a successful Browse/parse... NOT
at Run Analysis time"* (per the Phase 1 report's own citation of this
file). Concretely: browsing a single division file writes a log row
immediately, before Run Analysis is ever clicked and before any
finding/record is recomputed — so Phase 1's "changed since last send" can
go **true on a mere browse with zero actual data change**, and conversely
the real recompute event (Run Analysis) has no upload_log entry that
distinguishes it from the three browse-time entries that preceded it.

The correct hook exists and is already exactly analogous to Inventory's:
both finding tables already carry their own `last_updated`:
- `WorkDistributionFinding.last_updated` (`database/models.py:1000`),
  full-replaced by `process_work_distribution_report()`
  (`app/work_distribution_service.py:261-264`, "full-replace
  WorkDistributionDoctor + WorkDistributionFinding").
- `ManagerWorkAllocationFinding.last_updated` (`database/models.py:1175`),
  shared by both ABM and RBM rows (`designation` discriminates,
  `database/models.py:1121-1124`), full-replaced by
  `process_manager_work_allocation_report()` (ABM) and `process_rbm_report()`
  (RBM) — both referenced from `ui/work_distribution_upload_page.py`'s
  imports (not re-verified line-by-line here, but these are the two
  functions Phase 1's own report already named as the RGD/MWA Run Analysis
  handlers).

Phase 1's button is module-wide by design (`upload_log_data_state()`
already takes `MAX` across every upload type,
`app/work_distribution_notification_service.py:670-691` — see the Phase 1
report). The Phase 2 counter should preserve that: `MAX` across
`WorkDistributionFinding.last_updated` and
`ManagerWorkAllocationFinding.last_updated`, not upload_log at all. This is
a correction to Phase 1, not a Phase 2 addition — worth fixing regardless
of whether Phase 2 proceeds.

---

## 2. Proposed local schema for the data_version counter

Given finding #1, this only needs to exist for Inventory and Work
Distribution (Path Validator already has `import_id`). One shared table,
matching the existing per-module-singleton convention already used by
`SyncModuleCheck` (`database/models.py:207-222`, `module` as primary key,
one row per module) rather than a bespoke column bolted onto
`InventoryReplenishment`/`WorkDistributionFinding` — those tables are
full-replaced wholesale on every upload, so a counter column living on
them would need to be preserved-and-incremented across a delete, which is
exactly the kind of awkward fit a small separate table avoids.

Proposed SQLAlchemy model (local SQLite, `database/models.py`):

```python
class ModuleDataVersion(Base):
    """One row per module that has a Phase 2 send-history counter but no
    real sync manifest yet (Inventory, Work Distribution) -- Path
    Validator doesn't need a row here, it already has import_id.
    `version` increments by exactly 1 each time that module's underlying
    data actually changes (see notification_service Phase 2 report §1 for
    the exact hook per module) -- never reset, never recomputed from
    anything else, a plain counter."""

    __tablename__ = "module_data_version"

    module = Column(String, primary_key=True)
    version = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=utcnow)
```

Migration (idempotent, matching the existing hand-written style in
`database/migrations.py`):

```python
def ensure_module_data_version_table() -> None:
    engine = get_engine()
    inspector = inspect(engine)
    if "module_data_version" in inspector.get_table_names():
        return
    ModuleDataVersion.__table__.create(bind=engine)
    logger.info("Migration: created module_data_version table")
```

Increment call (a small helper, called from the two hook points found in
§1 — `evaluate_replenishment()` and the two/three Work Distribution
process functions):

```python
def bump_data_version(module: str) -> int:
    session = get_config_session()
    try:
        row = session.query(ModuleDataVersion).filter_by(module=module).first()
        if row is None:
            row = ModuleDataVersion(module=module, version=1, updated_at=utcnow())
            session.add(row)
        else:
            row.version += 1
            row.updated_at = utcnow()
        session.commit()
        return row.version
    finally:
        session.close()
```

Not shown as final code (this is a report), but this is the concrete shape
being proposed, not just a description, per your ask.

---

## 3. Proposed Supabase send-history table schema

RLS precedent check, not assumed: `docs/SYNC_DESIGN.md:48-51` states the
posture explicitly — *"any authenticated user can read... and insert...
no update, no delete — immutability is enforced by never granting
those"* — and `supabase/migrations/0023_sync_manifest.sql:53-63` implements
exactly that shape for `sync_manifest` (SELECT `using (true)`, INSERT
`with check (true)`, both `to authenticated`, no UPDATE/DELETE policy
anywhere in the file). **This precedent fits directly** — a send-history
row is the same shape as a manifest row: an immutable fact about something
that happened, never edited or retracted.

One more thing worth citing: `docs/SYNC_DESIGN.md:163-166` already named
this exact feature as an open item and explicitly deferred it — *"Email
notification tables... append-only audit logs of what was sent. Do
non-uploaders need to see them across machines, or do they stay local? Not
decided; do not build either direction until answered."* Phase 2 is
answering that question now (cross-machine, shared). Worth knowing this
was flagged once before, not newly discovered.

Proposed DDL:

```sql
create table if not exists public.email_send_history (
    id bigint generated always as identity primary key,
    module text not null,
    data_version text not null,
    sent_by uuid not null references public.profiles(id),
    sent_at timestamptz not null default now()
);

create index if not exists email_send_history_module_sent_at_idx
    on public.email_send_history (module, sent_at desc);

alter table public.email_send_history enable row level security;
grant select, insert on public.email_send_history to authenticated;

drop policy if exists "Authenticated users can read send history" on public.email_send_history;
create policy "Authenticated users can read send history"
on public.email_send_history for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert send history" on public.email_send_history;
create policy "Authenticated users can insert send history"
on public.email_send_history for insert
to authenticated
with check (true);
```

Note on `data_version`'s type: proposed as `text`, not `bigint`, even
though Work Distribution/Inventory's counter is an integer and Path
Validator's `import_id` is also an integer — because they're two
*different, incompatible* integer spaces (see §6). Storing both as text
avoids implying they're the same kind of number; the module column is what
tells you how to interpret it. This is a detail worth confirming, not a
strong opinion.

`sent_by` resolving to a display name ("by Priya") reuses the exact
mechanism already built for Review System's sync, not a new lookup:
`public.profile_display_names` view (`supabase/migrations/0023_sync_manifest.sql:109-115`)
plus the local read-through cache and `display_name_for()`
(`app/review_sync_service.py` — cited in this session's earlier work, not
re-read line-by-line for this report). No new name-resolution design
needed.

---

## 4. Exact per-module changes to `send_button_state()`'s caller

`send_button_state()` itself (the pure classifier) doesn't need to change
in any module — it already takes `(has_data, changed_since_last_send)` or
equivalent and returns a string; that signature is correct for a
Supabase-backed check too, it just needs different *arguments*. What
changes is the function that supplies those arguments:

- **Path Validator**: `has_sent_email_for_import()`
  (`app/notification_service.py:1011-1025`) currently queries local
  `email_notifications`. Phase 2 replaces its body with a Supabase query:
  `select ... from email_send_history where module = 'employee_module' and
  data_version = :import_id`. Caller: `_refresh_send_emails_button()`
  (`ui/findings_page.py:352`).
- **Inventory**: `replenishment_data_state()`
  (`app/inventory_notification_service.py:364-379`) currently compares two
  local timestamps. Phase 2 replaces the "last sent" half with a Supabase
  query for `module = 'inventory_module'`, most recent row, compared
  against the new local `ModuleDataVersion.version` (§2) instead of
  `InventoryReplenishment.last_updated` directly. Caller:
  `_refresh_send_emails_button()`
  (`ui/inventory_automated_emails_page.py:195`).
- **Work Distribution**: `upload_log_data_state()`
  (`app/work_distribution_notification_service.py:670-691`) — same shape
  of change, `module = 'work_distribution'`, compared against the
  corrected local counter from §1/§2. Caller: `_refresh_send_emails_button()`
  (`ui/work_distribution_findings_page.py:151`).

All three call sites already exist and already call a function with the
right shape — this is a body-swap in three existing functions, not new
call sites.

---

## 5. Status line placement and the one-query question

**Placement**: `SendEmailsButton` (`ui/components.py:261-282`) currently
has exactly one text element besides the button itself —
`self.subtext` (`ui/components.py:275-278`), driven by `set_state(enabled,
subtext)` (`ui/components.py:280-282`). That label is state-dependent: it
shows the disabled-reason text and goes *empty* whenever the button is
enabled (confirmed in all three pages' `_refresh_send_emails_button()`
bodies — every enabled branch calls `set_state(enabled=True, subtext="")`).
The "Last sent 2h ago by Priya" line needs to be visible **regardless of
enabled/disabled state** — it's a different concern from the state
subtext, not a replacement for it. Concretely, this needs a **second**
label on the shared widget (e.g. `self.status_label`, packed below
`subtext`), with its own setter, so a disabled button can still show "Last
sent 2h ago by Priya" as smaller ambient text at the same time as "No
reports generated." This is an extension to the existing shared
component, not a new one.

**One query, not two**: yes, directly achievable, and for a clean reason —
the SAME row answers both questions. `email_send_history`'s most recent
row for a given module (`order by sent_at desc limit 1`) gives you
`data_version` (compare against local current version → drives
no_data/new_data/resend_confirm) **and** `sent_by`/`sent_at` (drives "Last
sent Xh ago by Y") in one read. `_refresh_send_emails_button()` in all
three pages already does one local query today; Phase 2 replaces it with
one Supabase query that returns both.

---

## 6. Cross-machine walkthrough — and the problem this surfaces

Walking through Inventory exactly as asked, plainly:

1. Machine A uploads an Inventory Report. `evaluate_replenishment()` runs
   (`app/replenishment_service.py:132`), full-replacing
   `InventoryReplenishment` locally on A only — this table is never synced
   (confirmed: `docs/SYNC_DESIGN.md:58` lists `inventory_replenishment` under
   "Locally-rebuilt (never sync as rows)"). A's local `ModuleDataVersion`
   counter (§2) for `inventory_module` increments, say, 3 → 4.
2. A clicks Send Emails. A row lands in `email_send_history`: `module =
   'inventory_module', data_version = '4', sent_by = A, sent_at = now()`.
3. Machine B opens the Automated Emails page. Per §4, it queries
   `email_send_history` for `inventory_module` → gets back `data_version =
   '4', sent_by = A`. It compares that against **B's own local
   `ModuleDataVersion.version`** for `inventory_module`.

Here is the problem: **B's counter and A's counter are not the same
number space.** Inventory has no sync mechanism at all today (confirmed,
§1) — B's `inventory_replenishment` table was independently built from
whatever Inventory Report *B's own user* uploaded, whenever that was, on B
alone. B's local counter might be `1` (B has never uploaded), `7` (B
uploads constantly, entirely unrelated data to A's), or anything else —
it has no relationship to A's `4` beyond both being integers. Comparing
them can't produce a meaningful `new_data`/`resend_confirm` answer:

- If B's counter is lower than 4 (e.g., B never uploaded — counter is 0 or
  unset), the button reads `no_data` or treats it as "not sent since,"
  even though B may have completely different, never-emailed real data
  sitting locally that A's send said nothing about.
- If B's counter happens to be higher (e.g., B uploaded 7 times,
  unrelated to A), the button reads `new_data`/"changed since last send"
  purely because B's own unrelated counter is numerically bigger — not
  because B's *current* data actually differs from what A sent.

**This isn't unique to Inventory or Work Distribution — it applies to
Path Validator too, exactly as decided in the Context section.**
`ActiveSession.import_id` is `ImportHistory.id`, a plain local
autoincrement (`database/models.py:14-24`) — confirmed never synced today
(`docs/SYNC_DESIGN.md:57`: `raw_visits`/`investigation_findings` listed
under "Locally-rebuilt (never sync as rows)"), and — this is the part
worth being explicit about — **even the *planned future* sync design
doesn't fix this**: `docs/SYNC_DESIGN.md:63-66` describes Path Validator
findings as "recomputed on each machine from raw_visits + rules... now
also never leaving the machine at all." Two machines downloading and
reprocessing the *exact same* uploaded source file would each assign it
their *own* local `import_history.id` via their own independent
autoincrement sequence — nothing ties those two IDs together. `import_id`
is exactly as machine-local as the new counter, just with a more
established-sounding name.

So the actual finding isn't "Inventory and Work Distribution need a
counter, Path Validator's fine as-is" — it's: **none of the three
proposed `data_version` values (import_id included) are inherently
comparable across machines, because none of the three modules' underlying
business data is synced across machines today.** The shared
`email_send_history` table correctly answers "did *some* machine already
send for *a* version" — it cannot correctly answer "does *my* current
data match what was sent" unless the version identifier itself means the
same thing on every machine, which none of the three currently do.

What *would* fix this (not proposing to build any of it, flagging that it
exists): a content hash of the uploaded/rebuilt data instead of a local
autoincrement/counter — two machines with identical data would hash
identically regardless of local history — or waiting for real sync
manifests (whose `seq` is genuinely Supabase-assigned and shared) to reach
these modules, which is explicitly the separate, unstarted work Phase 2 is
meant to not be blocked on.

---

## 7. Open questions for you

1. **The core one, from §6**: is the operational reality that only one
   person/machine ever uploads for a given module (others only view/send
   from already-current data), making this cross-machine gap mostly
   theoretical in practice? Or do multiple machines genuinely upload
   independently for Inventory/Work Distribution/Path Validator? This
   determines whether §6's finding is a real correctness problem to solve
   before building, or a known, accepted limitation worth documenting and
   shipping anyway. Not guessing at this.
2. If it's accepted as a known limitation: should the status line say
   something that makes the ambiguity visible (e.g., "Last sent 2h ago by
   Priya" with no claim about whether that matches *your* current data),
   versus the no_data/new_data/resend_confirm classification which
   actively asserts a (possibly wrong) claim about data freshness? These
   might warrant different confidence levels in the UI.
3. Work Distribution's counter question from §1 (`upload_log` → the two
   finding tables' `last_updated`) is a **correction to Phase 1**, not a
   Phase 2 addition. Do you want that fixed as its own small piece before
   Phase 2, alongside Phase 2, or is the current (slightly wrong)
   behavior acceptable to leave as-is for now?
4. `data_version` as `text` in the Supabase table (§3) — confirm that's
   the right call given the two incompatible integer spaces, or if you'd
   rather it stay typed per-module some other way.
5. Named in §5 but worth deciding explicitly: does "Last ago" use a live
   relative-time string that needs re-rendering as time passes (e.g. "2h
   ago" becoming "3h ago" without a manual refresh), or is it computed
   once at refresh time and left static until the next click/on_show?
   Affects whether this needs a periodic UI tick, which the original
   Phase 1 spec explicitly avoided ("no timer") for the button state
   itself.
