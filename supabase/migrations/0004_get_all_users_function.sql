-- Version 2.0 -- Milestone 7: expose a user list for User Management.
--
-- `auth.users` (email, created_at) is never exposed directly through the
-- Data API -- this function is the one deliberate, narrow exception. It
-- runs SECURITY DEFINER (elevated privileges) specifically so it can join
-- auth.users, but it enforces its own permission check internally first
-- (the caller's role must have user_management = true), since SECURITY
-- DEFINER bypasses RLS entirely and nothing else would stop an
-- unauthorized caller otherwise.
--
-- Run in the Supabase SQL Editor. Safe to re-run (CREATE OR REPLACE).

begin;

create or replace function public.get_all_users()
returns table (
    id uuid,
    email text,
    full_name text,
    role_id uuid,
    role_name text,
    active boolean,
    created_at timestamptz
)
language plpgsql
security definer
set search_path = public, pg_catalog
as $$
begin
  if not exists (
    select 1
    from public.profiles p
    join public.roles r on r.id = p.role_id
    where p.id = auth.uid() and r.user_management = true
  ) then
    raise exception 'Access denied: user_management permission required';
  end if;

  return query
  select
    p.id,
    u.email::text,
    p.full_name,
    p.role_id,
    r.name as role_name,
    p.active,
    p.created_at
  from public.profiles p
  join auth.users u on u.id = p.id
  left join public.roles r on r.id = p.role_id
  order by p.created_at desc;
end;
$$;

revoke all on function public.get_all_users() from public;
grant execute on function public.get_all_users() to authenticated;

commit;
