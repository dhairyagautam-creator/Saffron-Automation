-- Version 2.1 -- Phase 2 of email authority: a shared, cross-machine
-- record of every actual email send, replacing each module's Phase 1
-- LOCAL per-machine timestamp comparison for "new data since last send".
-- See docs/EMAIL_AUTHORITY_PHASE2_CONTEXT.md for the full design and the
-- known limitation this does NOT solve (data_version values are not
-- comparable across machines when the underlying business data itself
-- isn't synced -- accepted, documented, not blocking this build).
--
-- One row per actual send (not per module) -- module, the data version it
-- was sent against (active_session.import_id for Path Validator, the new
-- module_data_version counter for Inventory/Work Distribution -- see
-- database/models.py's ModuleDataVersion), who sent it, when.
--
-- RLS posture matches the existing precedent exactly (see
-- docs/SYNC_DESIGN.md and supabase/migrations/0023_sync_manifest.sql):
-- any authenticated user can read and insert; no update, no delete --
-- immutability enforced by never granting those, not by a trigger.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

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

commit;
