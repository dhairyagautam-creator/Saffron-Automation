-- Version 2.0 -- Payment Analytics cloud migration, Milestone 28.
--
-- Cloud mirror of payment_active_months -- the rolling six-month window
-- membership (at most 6 rows). Unlike Inventory's upsert-only tables,
-- this one genuinely evicts rows locally (the oldest active month is
-- deleted when a 7th month is accepted -- see
-- app/payment_analytics_service.process_monthly_report()), so cloud sync
-- always does a full replace (delete_rows() then push_rows() of the
-- current set) rather than upsert-only -- otherwise an evicted month
-- would orphan forever in the cloud. (year, month_number) is the natural
-- key, same UniqueConstraint already enforced locally.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

create table if not exists public.payment_active_months (
    year integer not null,
    month_number integer not null,
    month_label text not null,
    added_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    updated_by uuid references auth.users(id),
    primary key (year, month_number)
);

create or replace function public.set_payment_active_months_audit_fields()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  new.updated_by = auth.uid();
  return new;
end;
$$;

drop trigger if exists set_payment_active_months_audit_fields on public.payment_active_months;
create trigger set_payment_active_months_audit_fields
before insert or update on public.payment_active_months
for each row execute function public.set_payment_active_months_audit_fields();

alter table public.payment_active_months enable row level security;

drop policy if exists "Authenticated users can read payment active months" on public.payment_active_months;
create policy "Authenticated users can read payment active months"
on public.payment_active_months for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert payment active months" on public.payment_active_months;
create policy "Authenticated users can insert payment active months"
on public.payment_active_months for insert
to authenticated
with check (true);

drop policy if exists "Authenticated users can update payment active months" on public.payment_active_months;
create policy "Authenticated users can update payment active months"
on public.payment_active_months for update
to authenticated
using (true)
with check (true);

drop policy if exists "Authenticated users can delete payment active months" on public.payment_active_months;
create policy "Authenticated users can delete payment active months"
on public.payment_active_months for delete
to authenticated
using (true);

grant select, insert, update, delete on public.payment_active_months to authenticated;

commit;
