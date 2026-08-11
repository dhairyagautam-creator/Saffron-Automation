-- Version 2.0 -- Milestone 8: write access for User Management.
--
-- Milestones 4/5 only ever gave `profiles` a SELECT-your-own-row policy.
-- This adds exactly the two write policies Add/Edit/Enable-Disable need:
-- an admin (user_management = true) may INSERT any profile row and UPDATE
-- any profile row -- but the UPDATE policy's WITH CHECK explicitly blocks
-- an admin from setting their OWN row's `active` to false, so "can't
-- disable your own account" is enforced by Postgres itself, not just the
-- UI (the UI also blocks it, for a faster/clearer error message, but this
-- is the real, unbypassable backstop).
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

drop policy if exists "Admins can insert profiles" on public.profiles;
create policy "Admins can insert profiles"
on public.profiles for insert
to authenticated
with check (
  exists (
    select 1
    from public.profiles p
    join public.roles r on r.id = p.role_id
    where p.id = auth.uid() and r.user_management = true
  )
);

drop policy if exists "Admins can update profiles" on public.profiles;
create policy "Admins can update profiles"
on public.profiles for update
to authenticated
using (
  exists (
    select 1
    from public.profiles p
    join public.roles r on r.id = p.role_id
    where p.id = auth.uid() and r.user_management = true
  )
)
with check (
  exists (
    select 1
    from public.profiles p
    join public.roles r on r.id = p.role_id
    where p.id = auth.uid() and r.user_management = true
  )
  and not (id = auth.uid() and active = false)
);

grant insert, update on public.profiles to authenticated;

commit;
