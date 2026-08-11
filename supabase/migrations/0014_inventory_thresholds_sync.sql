-- Version 2.0 -- Inventory Monitoring cloud migration, Milestone 23.
--
-- Cloud mirror of inventory_thresholds. Unlike Path Validator's
-- investigation_findings/path_validator_imports, this table has no
-- separate cloud_id -- (branch_key, item_key) is already the real,
-- natural identity locally (see database/models.py's InventoryThreshold
-- UniqueConstraint and app/threshold_service.py, which already
-- upserts on this exact pair), so it doubles as the Postgres primary key
-- and the push_rows()/pull_rows() on_conflict target directly.
--
-- Sync is a full-table push/pull, not a delta -- these tables are small,
-- fixed-schema "current computed state" (not a growing history like
-- Path Validator's imports), and already upsert-in-place locally without
-- ever deleting a row missing from a new upload. Cloud sync mirrors that
-- exactly: never deletes, only upserts (confirmed with the user before
-- implementing).
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

create table if not exists public.inventory_thresholds (
    branch_key text not null,
    item_key text not null,
    branch_location text not null,
    division text,
    item_name text not null,
    packing double precision not null,
    previous_month_sales double precision not null,
    raw_threshold double precision not null,
    packed_threshold double precision not null,
    last_updated timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    updated_by uuid references auth.users(id),
    primary key (branch_key, item_key)
);

create or replace function public.set_inventory_thresholds_audit_fields()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  new.updated_by = auth.uid();
  return new;
end;
$$;

drop trigger if exists set_inventory_thresholds_audit_fields on public.inventory_thresholds;
create trigger set_inventory_thresholds_audit_fields
before insert or update on public.inventory_thresholds
for each row execute function public.set_inventory_thresholds_audit_fields();

alter table public.inventory_thresholds enable row level security;

drop policy if exists "Authenticated users can read inventory thresholds" on public.inventory_thresholds;
create policy "Authenticated users can read inventory thresholds"
on public.inventory_thresholds for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert inventory thresholds" on public.inventory_thresholds;
create policy "Authenticated users can insert inventory thresholds"
on public.inventory_thresholds for insert
to authenticated
with check (true);

drop policy if exists "Authenticated users can update inventory thresholds" on public.inventory_thresholds;
create policy "Authenticated users can update inventory thresholds"
on public.inventory_thresholds for update
to authenticated
using (true)
with check (true);

grant select, insert, update on public.inventory_thresholds to authenticated;

commit;
