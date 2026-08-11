-- Version 2.0 -- Milestone 12: cloud pointer for the three Organization
-- Data workbooks (Onyx/Guardians/Xandra). storage_path points at the
-- 'path-validator-organization-data' bucket, where each workbook is
-- overwritten in place (upsert) every time a new one is connected, since
-- only the latest version of each is ever meaningful (see
-- app/organization_data_sync_service.py).
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

create table if not exists public.path_validator_organization_workbooks (
    workbook_name text primary key check (workbook_name in ('Onyx', 'Guardians', 'Xandra')),
    storage_path text not null,
    original_file_name text not null,
    uploaded_at timestamptz not null default now(),
    uploaded_by uuid references auth.users(id),
    updated_at timestamptz not null default now()
);

create or replace function public.set_path_validator_org_workbooks_audit_fields()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  if tg_op = 'INSERT' then
    new.uploaded_by = auth.uid();
    new.uploaded_at = now();
  end if;
  return new;
end;
$$;

drop trigger if exists set_path_validator_org_workbooks_audit_fields on public.path_validator_organization_workbooks;
create trigger set_path_validator_org_workbooks_audit_fields
before insert or update on public.path_validator_organization_workbooks
for each row execute function public.set_path_validator_org_workbooks_audit_fields();

alter table public.path_validator_organization_workbooks enable row level security;

drop policy if exists "Authenticated users can read path validator org workbooks" on public.path_validator_organization_workbooks;
create policy "Authenticated users can read path validator org workbooks"
on public.path_validator_organization_workbooks for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert path validator org workbooks" on public.path_validator_organization_workbooks;
create policy "Authenticated users can insert path validator org workbooks"
on public.path_validator_organization_workbooks for insert
to authenticated
with check (true);

drop policy if exists "Authenticated users can update path validator org workbooks" on public.path_validator_organization_workbooks;
create policy "Authenticated users can update path validator org workbooks"
on public.path_validator_organization_workbooks for update
to authenticated
using (true)
with check (true);

grant select, insert, update on public.path_validator_organization_workbooks to authenticated;

commit;
