-- Version 2.0 -- Milestone 12: cloud metadata for Operations imports and the
-- shared "active session" pointer.
--
-- path_validator_imports.cloud_id is the client-generated UUID that ties an
-- import together across laptops (local SQLite autoincrement import_ids
-- collide across machines and can never be used as the cross-machine key --
-- see database/migrations.py's ensure_import_history_cloud_columns()).
-- division_files records where each division's original uploaded file
-- landed in Storage (bucket 'path-validator-operations-uploads'), so any
-- laptop can download and re-run the existing local import pipeline against
-- them -- this table intentionally never mirrors raw_visits' own dynamic,
-- per-upload columns (see the module's design decision).
--
-- path_validator_active_session is a fixed single row (id=1, same singleton
-- idiom as the local ActiveSession table) recording which import is
-- currently the shared working session across the team.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

create table if not exists public.path_validator_imports (
    cloud_id uuid primary key default gen_random_uuid(),
    file_name text not null,
    imported_at timestamptz not null,
    rows_imported integer not null,
    duplicates_removed integer not null,
    division_files jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    created_by uuid references auth.users(id)
);

create index if not exists idx_path_validator_imports_updated_at
    on public.path_validator_imports(updated_at);

create or replace function public.set_path_validator_imports_audit_fields()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  if tg_op = 'INSERT' then
    new.created_by = auth.uid();
  end if;
  return new;
end;
$$;

drop trigger if exists set_path_validator_imports_audit_fields on public.path_validator_imports;
create trigger set_path_validator_imports_audit_fields
before insert or update on public.path_validator_imports
for each row execute function public.set_path_validator_imports_audit_fields();

alter table public.path_validator_imports enable row level security;

drop policy if exists "Authenticated users can read path validator imports" on public.path_validator_imports;
create policy "Authenticated users can read path validator imports"
on public.path_validator_imports for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert path validator imports" on public.path_validator_imports;
create policy "Authenticated users can insert path validator imports"
on public.path_validator_imports for insert
to authenticated
with check (true);

drop policy if exists "Authenticated users can update path validator imports" on public.path_validator_imports;
create policy "Authenticated users can update path validator imports"
on public.path_validator_imports for update
to authenticated
using (true)
with check (true);

grant select, insert, update on public.path_validator_imports to authenticated;


create table if not exists public.path_validator_active_session (
    id integer primary key check (id = 1),
    import_cloud_id uuid references public.path_validator_imports(cloud_id),
    activated_at timestamptz,
    updated_at timestamptz not null default now(),
    updated_by uuid references auth.users(id)
);

create or replace function public.set_path_validator_active_session_audit_fields()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  new.updated_by = auth.uid();
  return new;
end;
$$;

drop trigger if exists set_path_validator_active_session_audit_fields on public.path_validator_active_session;
create trigger set_path_validator_active_session_audit_fields
before insert or update on public.path_validator_active_session
for each row execute function public.set_path_validator_active_session_audit_fields();

alter table public.path_validator_active_session enable row level security;

drop policy if exists "Authenticated users can read path validator active session" on public.path_validator_active_session;
create policy "Authenticated users can read path validator active session"
on public.path_validator_active_session for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert path validator active session" on public.path_validator_active_session;
create policy "Authenticated users can insert path validator active session"
on public.path_validator_active_session for insert
to authenticated
with check (true);

drop policy if exists "Authenticated users can update path validator active session" on public.path_validator_active_session;
create policy "Authenticated users can update path validator active session"
on public.path_validator_active_session for update
to authenticated
using (true)
with check (true);

grant select, insert, update on public.path_validator_active_session to authenticated;

commit;
