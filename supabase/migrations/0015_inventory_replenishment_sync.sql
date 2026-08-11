-- Version 2.0 -- Inventory Monitoring cloud migration, Milestone 23.
--
-- Cloud mirror of inventory_replenishment -- same design as
-- 0014_inventory_thresholds_sync.sql: (branch_key, item_key) is the real
-- natural identity (see app/replenishment_service.py, which already
-- upserts on this exact pair locally), used directly as the Postgres
-- primary key and on_conflict target. Full-table push/pull, upsert-only,
-- never deletes -- mirrors the existing local behavior exactly.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

create table if not exists public.inventory_replenishment (
    branch_key text not null,
    item_key text not null,
    branch_location text not null,
    division text,
    item_code text,
    item_name text not null,
    packing double precision,
    closing_stock double precision not null,
    transit_stock double precision not null,
    effective_available_stock double precision not null,
    raw_threshold double precision not null,
    packed_threshold double precision not null,
    stock_deficit double precision not null,
    status text not null,
    last_updated timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    updated_by uuid references auth.users(id),
    primary key (branch_key, item_key)
);

create or replace function public.set_inventory_replenishment_audit_fields()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  new.updated_by = auth.uid();
  return new;
end;
$$;

drop trigger if exists set_inventory_replenishment_audit_fields on public.inventory_replenishment;
create trigger set_inventory_replenishment_audit_fields
before insert or update on public.inventory_replenishment
for each row execute function public.set_inventory_replenishment_audit_fields();

alter table public.inventory_replenishment enable row level security;

drop policy if exists "Authenticated users can read inventory replenishment" on public.inventory_replenishment;
create policy "Authenticated users can read inventory replenishment"
on public.inventory_replenishment for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert inventory replenishment" on public.inventory_replenishment;
create policy "Authenticated users can insert inventory replenishment"
on public.inventory_replenishment for insert
to authenticated
with check (true);

drop policy if exists "Authenticated users can update inventory replenishment" on public.inventory_replenishment;
create policy "Authenticated users can update inventory replenishment"
on public.inventory_replenishment for update
to authenticated
using (true)
with check (true);

grant select, insert, update on public.inventory_replenishment to authenticated;

commit;
