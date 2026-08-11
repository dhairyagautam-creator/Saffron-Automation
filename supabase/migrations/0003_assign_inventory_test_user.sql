-- Version 2.0 -- assigns the Inventory role to a second test account, so
-- restricted-role access control (Milestone 6) can be verified through a
-- real login instead of only a simulated one. Same pattern as migration
-- 0001's Admin backfill for the first test user.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

insert into public.profiles (id, role_id, active)
select u.id, r.id, true
from auth.users u
cross join public.roles r
where u.email = 'dhairyagamer07@gmail.com'
  and r.name = 'Inventory'
on conflict (id) do update set role_id = excluded.role_id;

commit;
