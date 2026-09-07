-- Read-only audit: run this in the Supabase SQL Editor and paste the results
-- back before running DROP_SYNC_INFRASTRUCTURE.sql. Makes no changes.
--
-- Run each numbered block separately (or all at once -- the SQL Editor shows
-- one result grid per statement) and paste back all four/five grids.

-- ============================================================
-- 1. Every table in the public schema, with its EXACT row count.
--    (Built dynamically so it also catches anything not listed in this
--    review's migration history -- that's the point of an audit.)
-- ============================================================
do $$
declare
  r record;
begin
  create temporary table if not exists _audit_row_counts (table_name text, row_count bigint);
  truncate _audit_row_counts;
  for r in select tablename from pg_tables where schemaname = 'public' loop
    execute format('insert into _audit_row_counts select %L, count(*) from public.%I', r.tablename, r.tablename);
  end loop;
end $$;

select * from _audit_row_counts order by table_name;

-- ============================================================
-- 2. Every storage bucket, with its object count.
-- ============================================================
select
  b.id as bucket_id,
  b.name,
  b.public,
  count(o.id) as object_count
from storage.buckets b
left join storage.objects o on o.bucket_id = b.id
group by b.id, b.name, b.public
order by b.id;

-- ============================================================
-- 3. Every trigger in the public schema.
-- ============================================================
select
  event_object_table as table_name,
  trigger_name,
  action_timing,
  event_manipulation
from information_schema.triggers
where trigger_schema = 'public'
order by event_object_table, trigger_name;

-- ============================================================
-- 4. Every function in the public schema.
-- ============================================================
select
  p.proname as function_name,
  pg_get_function_identity_arguments(p.oid) as arguments,
  case p.prosecdef when true then 'SECURITY DEFINER' else 'SECURITY INVOKER' end as security,
  l.lanname as language
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
join pg_language l on l.oid = p.prolang
where n.nspname = 'public'
order by p.proname;

-- ============================================================
-- 5. Bonus -- every RLS policy on the three tables the drop script must
--    NOT touch (roles/profiles/user_module_permissions). Use this to
--    confirm the exclusion list before running the drop script.
-- ============================================================
select schemaname, tablename, policyname, cmd, roles
from pg_policies
where schemaname = 'public'
  and tablename in ('roles', 'profiles', 'user_module_permissions')
order by tablename, policyname;
