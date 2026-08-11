-- Version 2.0 -- fixes both User Management bugs found in real testing:
-- disabling a user silently affected 0 rows, and creating a user's
-- profile was rejected even though the admin genuinely has
-- user_management=true.
--
-- Root cause: Postgres RLS requires a row to be visible under a SELECT
-- policy before UPDATE/DELETE can even locate it to modify -- and
-- postgrest's INSERT reads the new row back afterward to return it, which
-- needs that same SELECT visibility for the row just created. The only
-- SELECT policy that existed let a user see their OWN row only, so an
-- admin could not see (and therefore could not update, or read back after
-- inserting) any OTHER user's row -- even though the INSERT/UPDATE
-- policies themselves were otherwise correct.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

drop policy if exists "Admins can read all profiles" on public.profiles;
create policy "Admins can read all profiles"
on public.profiles for select
to authenticated
using (
  exists (
    select 1
    from public.profiles p
    join public.roles r on r.id = p.role_id
    where p.id = auth.uid() and r.user_management = true
  )
);

commit;
