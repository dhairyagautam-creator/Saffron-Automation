-- Version 2.0 -- Payment Analytics (Collections Action Center) cloud
-- migration, Milestone 28.
--
-- Cloud mirror of outstanding_invoices. No natural key exists locally
-- (pure autoincrement `id`), and every Outstanding Report upload is a
-- full "Daily Refresh" -- delete-all + reinsert, resetting every
-- followed_up flag (see app/collections_service.process_outstanding_report()).
-- Cloud sync mirrors that: a new upload always does delete_rows() then
-- push_rows() of the fresh set (cloud_id assigned per row at push time).
-- The one exception is the followed_up checkbox toggle, which pushes a
-- single-row upsert on cloud_id between uploads -- the reason this table
-- needs a cloud_id at all, unlike payment_active_months/
-- payment_customer_profiles' full-replace-only design (neither of those
-- has any single-row update path to address).
--
-- Days Outstanding / Days Over Due / Status are deliberately NOT columns
-- here, matching the local table exactly -- they depend on today's date
-- and are always computed fresh on whichever laptop reads them (see
-- app/collections_service.compute_days_and_status()), never synced as
-- stored values.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

create table if not exists public.outstanding_invoices (
    cloud_id uuid primary key default gen_random_uuid(),
    party_name text not null,
    team text,
    hq text,
    invoice_no text,
    lr_date date not null,
    due_date date not null,
    bill_amount double precision,
    month text,
    followed_up boolean not null default false,
    uploaded_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    updated_by uuid references auth.users(id)
);

create or replace function public.set_outstanding_invoices_audit_fields()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  new.updated_by = auth.uid();
  return new;
end;
$$;

drop trigger if exists set_outstanding_invoices_audit_fields on public.outstanding_invoices;
create trigger set_outstanding_invoices_audit_fields
before insert or update on public.outstanding_invoices
for each row execute function public.set_outstanding_invoices_audit_fields();

alter table public.outstanding_invoices enable row level security;

drop policy if exists "Authenticated users can read outstanding invoices" on public.outstanding_invoices;
create policy "Authenticated users can read outstanding invoices"
on public.outstanding_invoices for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert outstanding invoices" on public.outstanding_invoices;
create policy "Authenticated users can insert outstanding invoices"
on public.outstanding_invoices for insert
to authenticated
with check (true);

drop policy if exists "Authenticated users can update outstanding invoices" on public.outstanding_invoices;
create policy "Authenticated users can update outstanding invoices"
on public.outstanding_invoices for update
to authenticated
using (true)
with check (true);

drop policy if exists "Authenticated users can delete outstanding invoices" on public.outstanding_invoices;
create policy "Authenticated users can delete outstanding invoices"
on public.outstanding_invoices for delete
to authenticated
using (true);

grant select, insert, update, delete on public.outstanding_invoices to authenticated;

commit;
