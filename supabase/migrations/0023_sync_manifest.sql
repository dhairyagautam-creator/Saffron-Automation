-- Version 2.1 -- First vertical slice of the sync design (docs/SYNC_DESIGN.md),
-- scoped to the Review System only. One append-only manifest table + one
-- private Storage bucket: an upload writes an object then a manifest row
-- pointing at it; every other client polls the manifest, downloads what it
-- doesn't have, verifies the hash, and applies it locally. No other module
-- is touched by this migration.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

-- --- Storage bucket ---------------------------------------------------
-- Private (not public) -- same posture as every existing bucket in this
-- project (0009). One bucket for now; `module`/`slot_key` in the manifest
-- row (and in the object's own storage_path) is what actually scopes an
-- object to Review System, so a second module can reuse this same bucket
-- later without a migration, if that turns out to be wanted.

insert into storage.buckets (id, name, public)
values ('sync-uploads', 'sync-uploads', false)
on conflict (id) do nothing;

-- --- sync_manifest ------------------------------------------------------
-- Append-only: no update policy, no delete policy, anywhere in this file.
-- This is structural, not a convention someone could accidentally violate --
-- nothing in Postgres can UPDATE or DELETE a row here through the API
-- (postgrest) without a policy granting it, and none is ever created.

create table if not exists public.sync_manifest (
    seq bigint generated always as identity primary key,
    module text not null,
    slot_key text not null,
    storage_path text not null,
    sha256 text not null,
    size_bytes bigint not null,
    filename text not null,
    app_version text not null,
    uploaded_by uuid not null references public.profiles(id),
    uploaded_at timestamptz not null default now()
);

create index if not exists sync_manifest_module_seq_idx
    on public.sync_manifest (module, seq desc);

alter table public.sync_manifest enable row level security;

grant select, insert on public.sync_manifest to authenticated;

-- Matches the existing wide-open-to-any-authenticated-user posture on
-- business data (see 0008's own precedent comment) -- screen-level RBAC is
-- the real access gate in this app, not row ownership. No update policy,
-- no delete policy: see the append-only note above.
drop policy if exists "Authenticated users can read the sync manifest" on public.sync_manifest;
create policy "Authenticated users can read the sync manifest"
on public.sync_manifest for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert into the sync manifest" on public.sync_manifest;
create policy "Authenticated users can insert into the sync manifest"
on public.sync_manifest for insert
to authenticated
with check (true);

-- --- Storage policies -----------------------------------------------------
-- Select + insert only, matching the manifest table's own append-only
-- posture -- deliberately NO update policy. Each upload's object path is
-- derived from its content hash (module/slot_key/sha256), so a genuinely
-- new upload is always a new path, never a mutation of an existing object;
-- there is nothing an update policy would ever legitimately be used for.

drop policy if exists "Authenticated users can read sync uploads" on storage.objects;
create policy "Authenticated users can read sync uploads"
on storage.objects for select
to authenticated
using (bucket_id = 'sync-uploads');

drop policy if exists "Authenticated users can write sync uploads" on storage.objects;
create policy "Authenticated users can write sync uploads"
on storage.objects for insert
to authenticated
with check (bucket_id = 'sync-uploads');

-- --- Profile name resolution ------------------------------------------
-- Prerequisite this slice actually needs and the existing schema doesn't
-- provide: the UI must show uploaded_by as a display name, never a uuid,
-- for ANY uploader, not just the signed-in user themselves. profiles' own
-- SELECT policies (0002/0006/0007) only let a user read their OWN row, or
-- let a user_management admin read every row -- a non-admin Review System
-- user cannot currently look up a colleague's name at all.
--
-- A plain RLS policy can't fix this: RLS is row-level only, and profiles
-- holds role_id/is_super_admin alongside full_name/active -- a policy
-- opening SELECT to every row would expose those too, not just a name.
-- So: a VIEW restricted to exactly the safe columns (id, full_name,
-- active -- no email column exists on profiles at all; it lives on
-- auth.users and is not exposed here), left WITHOUT security_invoker, so
-- it runs as its owner and is not subject to profiles' own restrictive
-- policies -- the same elevated-privilege-bypasses-RLS mechanism this
-- project already uses for get_all_users()/is_user_management_admin()
-- (0004/0007/0020), just via a view (for column restriction) instead of a
-- function.
--
-- This adds ZERO policies to profiles itself -- nothing here can
-- reintroduce 0007's recursion, because that recursion only happens when
-- a POLICY ON PROFILES queries profiles again inside its own USING
-- clause, and this migration never touches profiles' policies at all.

create or replace view public.profile_display_names
with (security_invoker = false)
as
  select id, full_name, active
  from public.profiles;

grant select on public.profile_display_names to authenticated;

commit;
