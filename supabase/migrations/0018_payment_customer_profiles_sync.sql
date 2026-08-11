-- Version 2.0 -- Payment Analytics cloud migration, Milestone 28.
--
-- Cloud mirror of payment_customer_profiles. party_name is already the
-- natural key locally (UniqueConstraint), so this could look like
-- Inventory's upsert-only precedent -- but unlike Inventory's tables,
-- this one is genuinely delete-all + rebuilt from scratch every time the
-- active window changes (see
-- app/payment_analytics_service._replace_customer_profiles()), and a
-- customer who drops out of the active window must disappear from the
-- cloud too. So sync here always does a full replace (delete_rows() then
-- push_rows()), the same reasoning as payment_active_months, not
-- Inventory's upsert-only pattern -- mirroring exact local behavior in
-- both cases, just different local behavior.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

create table if not exists public.payment_customer_profiles (
    party_name text primary key,
    customer_type text,
    total_invoices integer not null,
    average_payment_days double precision not null,
    earliest_invoice date,
    latest_invoice date,
    risk_category text not null,
    last_updated timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    updated_by uuid references auth.users(id)
);

create or replace function public.set_payment_customer_profiles_audit_fields()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  new.updated_by = auth.uid();
  return new;
end;
$$;

drop trigger if exists set_payment_customer_profiles_audit_fields on public.payment_customer_profiles;
create trigger set_payment_customer_profiles_audit_fields
before insert or update on public.payment_customer_profiles
for each row execute function public.set_payment_customer_profiles_audit_fields();

alter table public.payment_customer_profiles enable row level security;

drop policy if exists "Authenticated users can read payment customer profiles" on public.payment_customer_profiles;
create policy "Authenticated users can read payment customer profiles"
on public.payment_customer_profiles for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert payment customer profiles" on public.payment_customer_profiles;
create policy "Authenticated users can insert payment customer profiles"
on public.payment_customer_profiles for insert
to authenticated
with check (true);

drop policy if exists "Authenticated users can update payment customer profiles" on public.payment_customer_profiles;
create policy "Authenticated users can update payment customer profiles"
on public.payment_customer_profiles for update
to authenticated
using (true)
with check (true);

drop policy if exists "Authenticated users can delete payment customer profiles" on public.payment_customer_profiles;
create policy "Authenticated users can delete payment customer profiles"
on public.payment_customer_profiles for delete
to authenticated
using (true);

grant select, insert, update, delete on public.payment_customer_profiles to authenticated;

commit;
