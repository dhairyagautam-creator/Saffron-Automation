-- Version 2.0 -- Milestone 12: cloud mirror of email_notifications (see
-- database/models.py's EmailNotification) -- the Send Log data, mapped to
-- this migration's "generated reports" requirement (there is no separate
-- report-export/PDF pipeline anywhere in this codebase; this is the closest
-- real equivalent -- confirmed with the user before implementing).
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

create table if not exists public.path_validator_email_notifications (
    cloud_id uuid primary key default gen_random_uuid(),
    import_cloud_id uuid not null references public.path_validator_imports(cloud_id),
    manager_name text,
    manager_email text,
    subject text not null,
    body text not null,
    finding_ids text not null,
    status text not null default 'Draft',
    error_message text,
    created_at timestamptz not null default now(),
    sent_at timestamptz,
    updated_at timestamptz not null default now(),
    updated_by uuid references auth.users(id)
);

create index if not exists idx_path_validator_emails_import
    on public.path_validator_email_notifications(import_cloud_id);
create index if not exists idx_path_validator_emails_updated_at
    on public.path_validator_email_notifications(updated_at);

create or replace function public.set_path_validator_emails_audit_fields()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  new.updated_by = auth.uid();
  return new;
end;
$$;

drop trigger if exists set_path_validator_emails_audit_fields on public.path_validator_email_notifications;
create trigger set_path_validator_emails_audit_fields
before insert or update on public.path_validator_email_notifications
for each row execute function public.set_path_validator_emails_audit_fields();

alter table public.path_validator_email_notifications enable row level security;

drop policy if exists "Authenticated users can read path validator emails" on public.path_validator_email_notifications;
create policy "Authenticated users can read path validator emails"
on public.path_validator_email_notifications for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert path validator emails" on public.path_validator_email_notifications;
create policy "Authenticated users can insert path validator emails"
on public.path_validator_email_notifications for insert
to authenticated
with check (true);

drop policy if exists "Authenticated users can update path validator emails" on public.path_validator_email_notifications;
create policy "Authenticated users can update path validator emails"
on public.path_validator_email_notifications for update
to authenticated
using (true)
with check (true);

grant select, insert, update on public.path_validator_email_notifications to authenticated;

commit;
