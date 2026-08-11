-- Version 2.0 -- fixes "infinite recursion detected in policy for
-- relation profiles" (Postgres error 42P17), hit as soon as migration
-- 0006 added a second self-referencing SELECT-shaped policy alongside the
-- original "read your own row" one. Each admin policy's inline correlated
-- subquery (`select ... from profiles p ... where p.id = auth.uid()`)
-- itself triggers profiles' own SELECT policies to resolve -- with two
-- such policies now in play, resolving one can require resolving the
-- other, which Postgres conservatively refuses as unsafe, even though it
-- would actually terminate here.
--
-- Fix: move the "is the caller an admin" check into a SECURITY DEFINER
-- function. Its internal query bypasses RLS (runs as the function owner,
-- not the caller), so it never re-triggers profiles' own policies --
-- exactly the same pattern already used for get_all_users() in migration
-- 0004. All three admin policies (from 0005 and 0006) are redefined to
-- call this function instead of repeating the subquery inline.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

create or replace function public.is_user_management_admin()
returns boolean
language sql
security definer
stable
set search_path = public, pg_catalog
as $$
  select exists (
    select 1
    from public.profiles p
    join public.roles r on r.id = p.role_id
    where p.id = auth.uid() and r.user_management = true
  );
$$;

revoke all on function public.is_user_management_admin() from public;
grant execute on function public.is_user_management_admin() to authenticated;

drop policy if exists "Admins can read all profiles" on public.profiles;
create policy "Admins can read all profiles"
on public.profiles for select
to authenticated
using (public.is_user_management_admin());

drop policy if exists "Admins can insert profiles" on public.profiles;
create policy "Admins can insert profiles"
on public.profiles for insert
to authenticated
with check (public.is_user_management_admin());

drop policy if exists "Admins can update profiles" on public.profiles;
create policy "Admins can update profiles"
on public.profiles for update
to authenticated
using (public.is_user_management_admin())
with check (public.is_user_management_admin() and not (id = auth.uid() and active = false));

commit;
