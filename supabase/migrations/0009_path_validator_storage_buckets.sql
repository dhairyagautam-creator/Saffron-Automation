-- Version 2.0 -- Milestone 12: Storage buckets for the Path Validator cloud
-- sync effort. Operations/Organization Data uploads are stored here as the
-- source of truth; each laptop reconstructs its own local SQLite tables by
-- re-running the existing, unchanged local parser against the downloaded
-- file (see app/import_sync_service.py, app/organization_data_sync_service.py).
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

insert into storage.buckets (id, name, public)
values ('path-validator-operations-uploads', 'path-validator-operations-uploads', false)
on conflict (id) do nothing;

insert into storage.buckets (id, name, public)
values ('path-validator-organization-data', 'path-validator-organization-data', false)
on conflict (id) do nothing;

-- Matches the wide-open-to-any-authenticated-user precedent set by
-- 0008_module_configurations.sql -- screen-level RBAC (Milestone 6) is the
-- real access gate in this app, not per-row/per-bucket ownership.
drop policy if exists "Authenticated users can read path validator storage" on storage.objects;
create policy "Authenticated users can read path validator storage"
on storage.objects for select
to authenticated
using (bucket_id in ('path-validator-operations-uploads', 'path-validator-organization-data'));

drop policy if exists "Authenticated users can write path validator storage" on storage.objects;
create policy "Authenticated users can write path validator storage"
on storage.objects for insert
to authenticated
with check (bucket_id in ('path-validator-operations-uploads', 'path-validator-organization-data'));

drop policy if exists "Authenticated users can update path validator storage" on storage.objects;
create policy "Authenticated users can update path validator storage"
on storage.objects for update
to authenticated
using (bucket_id in ('path-validator-operations-uploads', 'path-validator-organization-data'));

commit;
