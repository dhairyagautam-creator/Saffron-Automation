-- Parameter sync project, Phase 2: revives the generic cloud config store
-- dropped during the sync-removal task (see 0022_drop_sync_infrastructure.sql,
-- which listed public.module_configurations for deletion but was itself
-- never executed anywhere -- this migration does NOT assume that old table
-- still exists; `create table if not exists` makes it safe to run whether
-- it does or doesn't). Schema is unchanged from the original design
-- (0008_module_configurations.sql, Version 2.0 Milestone 9): one row per
-- module, the module's entire non-secret configuration as a single JSON
-- object -- never individual settings as separate rows/columns.
--
-- Scope for this revival: Path Validator (rule thresholds), Inventory
-- (multipliers/display mode), Work Distribution (KPI thresholds + Manager
-- Work Allocation thresholds, one shared blob), Payment Analytics
-- (risk-scoring/ageing thresholds). Review System has nothing left to sync
-- (its one parameter was dead code, removed in Phase 1). Credentials
-- (every sender email/app password pair, the Geoapify API key) and
-- per-machine one-time bookkeeping flags are never pushed here by any
-- application code -- enforced in app/parameter_sync_service.py and its
-- own tests, not by a database constraint (Postgres/jsonb has no way to
-- forbid specific keys inside a blob it doesn't parse).
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
-- with the app's current model where screen-level access (can_access) is
-- the real gate; there's no per-module data-ownership concept for shared
-- operational configuration like this (matches 0008's own precedent,
-- and app/parameter_sync_service.py's own module docstring).
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
