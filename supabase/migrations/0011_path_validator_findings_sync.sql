-- Version 2.0 -- Milestone 12: cloud mirror of investigation_findings
-- (see database/models.py's InvestigationFinding), one-to-one column shape
-- plus cloud_id/import_cloud_id/audit columns. Findings and reviewer status
-- (Open/Reviewed/Ignored) are genuine data/decisions, not re-derivable by
-- re-running the rule engine, so -- unlike raw_visits/employee_hierarchy --
-- these rows are mirrored directly rather than reconstructed from a file.
--
-- updated_at is the field app/sync_poller.py's delta-pull relies on (only
-- fetch findings changed since a laptop's local high-water mark), so a
-- reviewer's status click on one laptop is visible on another without a
-- full re-pull of every finding every tick.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

create table if not exists public.path_validator_findings (
    cloud_id uuid primary key default gen_random_uuid(),
    import_cloud_id uuid not null references public.path_validator_imports(cloud_id),
    employee_name text not null,
    employee_code text not null,
    visit_date date not null,
    rule_name text not null,
    message text not null,
    division text,
    concentration_percent double precision,
    valid_visit_count integer,
    matched_visit_count integer,
    radius_meters integer,
    threshold_percent double precision,
    cluster_lat double precision,
    cluster_lon double precision,
    notification_status text,
    suppression_reason text,
    hospital_name text,
    hospital_lat double precision,
    hospital_lon double precision,
    hospital_distance_meters integer,
    status text not null default 'Open',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    updated_by uuid references auth.users(id)
);

create index if not exists idx_path_validator_findings_import
    on public.path_validator_findings(import_cloud_id);
create index if not exists idx_path_validator_findings_updated_at
    on public.path_validator_findings(updated_at);

create or replace function public.set_path_validator_findings_audit_fields()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  new.updated_by = auth.uid();
  return new;
end;
$$;

drop trigger if exists set_path_validator_findings_audit_fields on public.path_validator_findings;
create trigger set_path_validator_findings_audit_fields
before insert or update on public.path_validator_findings
for each row execute function public.set_path_validator_findings_audit_fields();

alter table public.path_validator_findings enable row level security;

drop policy if exists "Authenticated users can read path validator findings" on public.path_validator_findings;
create policy "Authenticated users can read path validator findings"
on public.path_validator_findings for select
to authenticated
using (true);

drop policy if exists "Authenticated users can insert path validator findings" on public.path_validator_findings;
create policy "Authenticated users can insert path validator findings"
on public.path_validator_findings for insert
to authenticated
with check (true);

drop policy if exists "Authenticated users can update path validator findings" on public.path_validator_findings;
create policy "Authenticated users can update path validator findings"
on public.path_validator_findings for update
to authenticated
using (true)
with check (true);

grant select, insert, update on public.path_validator_findings to authenticated;

commit;
