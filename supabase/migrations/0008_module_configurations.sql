-- Version 2.0 -- Milestone 9: generic cloud config store, first used by
-- Path Validator Parameters, designed to be reused by Employee/Inventory/
-- Payments modules later without any schema change -- one row per module,
-- the module's entire configuration as a single JSON object, never
-- individual settings as separate rows/columns.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

create table if not exists public.module_configurations (
    id uuid primary key default gen_random_uuid(),
    module_key text not null unique,
    config jsonb not null,
    updated_at timestamptz not null default now(),
    updated_by uuid references auth.users(id)
);

-- updated_at/updated_by are database-controlled, not client-provided --
-- whatever the client sends for these two is overwritten here, so
-- "last synced" always reflects the real write time and who really wrote it.
create or replace function public.set_module_configuration_audit_fields()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  new.updated_by = auth.uid();
  return new;
end;
$$;

drop trigger if exists set_module_configurations_audit_fields on public.module_configurations;
create trigger set_module_configurations_audit_fields
before insert or update on public.module_configurations
for each row execute function public.set_module_configuration_audit_fields();

alter table public.module_configurations enable row level security;

-- Any signed-in user may read/write any module's config -- consistent
-- with the app's current model where screen-level access (Milestone 6) is
-- the real gate; there's no per-module data-ownership concept yet for
-- shared operational configuration like this.
drop policy if exists "Authenticated users can read module configurations" on public.module_configurations;
create policy "Authenticated users can read module configurations"
on public.module_configurations for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert module configurations" on public.module_configurations;
create policy "Authenticated users can insert module configurations"
on public.module_configurations for insert
to authenticated
with check (true);

drop policy if exists "Authenticated users can update module configurations" on public.module_configurations;
create policy "Authenticated users can update module configurations"
on public.module_configurations for update
to authenticated
using (true)
with check (true);

grant select, insert, update on public.module_configurations to authenticated;

commit;
