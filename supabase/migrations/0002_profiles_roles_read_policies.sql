-- Version 2.0 -- RBAC Milestone 5: read access for profile/role loading.
--
-- Milestone 4 enabled RLS on `roles`/`profiles` with zero policies (fully
-- closed). This milestone's app code needs to read a signed-in user's own
-- profile and their role's permission flags, so this adds exactly the two
-- read policies that requires -- nothing else. No write access, no access
-- for anonymous (unauthenticated) requests.
--
-- Run in the Supabase SQL Editor (Dashboard -> SQL Editor -> New query ->
-- paste -> Run). Safe to re-run.

begin;

grant select on public.profiles to authenticated;
grant select on public.roles to authenticated;

drop policy if exists "Users can read their own profile" on public.profiles;
create policy "Users can read their own profile"
on public.profiles for select
to authenticated
using (auth.uid() = id);

-- roles is a small, non-sensitive reference table (just names + boolean
-- flags) -- any signed-in user can read the full list, not just their own.
drop policy if exists "Authenticated users can read roles" on public.roles;
create policy "Authenticated users can read roles"
on public.roles for select
to authenticated
using (true);

commit;
