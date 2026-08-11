-- Version 2.0 -- Payment Analytics cloud migration, Milestone 28.
--
-- Cloud mirror of payment_invoices -- the permanent invoice ledger.
-- Unlike Inventory's tables, PaymentInvoice has no natural key locally
-- (pure autoincrement `id`, no UniqueConstraint) -- structurally closest
-- to Path Validator's investigation_findings, so this uses the same
-- cloud_id/updated_at/synced_at design: cloud_id is the client-generated
-- identity, updated_at drives delta-pull (only fetch what changed since a
-- laptop's own high-water mark).
--
-- A Historical Payment Report re-upload wipes and rebuilds this table
-- entirely, locally -- app/payment_sync_service.py mirrors that by also
-- clearing this cloud table (via the generic delete_rows()) before
-- re-pushing, but only after an explicit in-app confirmation, since
-- wiping shared cloud data is a real, visible action for other laptops
-- (confirmed with the user before implementing). A routine Monthly
-- Payment Report upload never wipes anything -- it only ever pushes the
-- newly-added invoices.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

create table if not exists public.payment_invoices (
    cloud_id uuid primary key default gen_random_uuid(),
    month text not null,
    year integer not null,
    month_number integer not null,
    party_name text not null,
    customer_type text,
    invoice_no text,
    lr_date date not null,
    due_date date,
    clear_date date not null,
    payment_days integer not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    updated_by uuid references auth.users(id)
);

create index if not exists idx_payment_invoices_updated_at on public.payment_invoices(updated_at);
create index if not exists idx_payment_invoices_year_month on public.payment_invoices(year, month_number);

create or replace function public.set_payment_invoices_audit_fields()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  new.updated_by = auth.uid();
  return new;
end;
$$;

drop trigger if exists set_payment_invoices_audit_fields on public.payment_invoices;
create trigger set_payment_invoices_audit_fields
before insert or update on public.payment_invoices
for each row execute function public.set_payment_invoices_audit_fields();

alter table public.payment_invoices enable row level security;

drop policy if exists "Authenticated users can read payment invoices" on public.payment_invoices;
create policy "Authenticated users can read payment invoices"
on public.payment_invoices for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert payment invoices" on public.payment_invoices;
create policy "Authenticated users can insert payment invoices"
on public.payment_invoices for insert
to authenticated
with check (true);

drop policy if exists "Authenticated users can update payment invoices" on public.payment_invoices;
create policy "Authenticated users can update payment invoices"
on public.payment_invoices for update
to authenticated
using (true)
with check (true);

-- Delete permission is new relative to every prior sync migration --
-- needed for the Historical Report's confirmed, explicit cloud-wipe path
-- (see app/sync_service.delete_rows()).
drop policy if exists "Authenticated users can delete payment invoices" on public.payment_invoices;
create policy "Authenticated users can delete payment invoices"
on public.payment_invoices for delete
to authenticated
using (true);

grant select, insert, update, delete on public.payment_invoices to authenticated;

commit;
