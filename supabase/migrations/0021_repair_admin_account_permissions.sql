-- Repair: dhairyagautam@andrewsosborne.com lost module access after
-- migration 0020's permission redesign.
--
-- Root cause: 0020 backfilled is_super_admin=true only for profiles whose
-- role was named exactly 'Admin' (migration 0001's seeded row). This
-- account's role at the time evidently was NOT that exact row -- so the
-- name match silently skipped it, and it received neither is_super_admin
-- nor any user_module_permissions rows for the four legacy-boolean
-- modules either (both backfills in 0020 depend on the SAME role join).
--
-- This is a targeted DATA repair, not a schema change and not an
-- authentication change -- it touches only public.profiles.is_super_admin
-- and public.user_module_permissions for this one account. It never
-- reads or writes auth.users, so the account's email/password are
-- completely untouched.
--
-- Run in the Supabase SQL Editor (Dashboard -> SQL Editor -> New query ->
-- paste -> Run). Safe to re-run.

begin;

-- --- 1/2: super admin flag -------------------------------------------------
-- Every module, including any added after today, per app/permissions.py's
-- can_access() -- `if profile.is_super_admin: return True` is checked
-- BEFORE the per-module grants table, so this alone already restores full
-- access. Everything below is belt-and-suspenders, matching exactly how
-- migration 0020 treated real admins (their explicit per-module rows are
-- redundant with is_super_admin too, kept anyway for the same reason).

update public.profiles p
set is_super_admin = true
from auth.users u
where p.id = u.id
  and u.email = 'dhairyagautam@andrewsosborne.com';

-- --- 2/2: explicit per-module grants (belt-and-suspenders) -----------------
-- Every module key currently in app/module_registry.py, so the User
-- Management table's own "Modules" column shows the full list explicitly
-- rather than relying on a reader mentally knowing "Super Admin implies
-- everything." A future module added to the registry does NOT need a row
-- here to be added -- is_super_admin already covers it automatically;
-- this list only needs to grow if you want the explicit-grants display to
-- stay exhaustive for its own sake, not for actual access.

insert into public.user_module_permissions (user_id, module_key)
select u.id, key
from auth.users u
cross join (values
    ('employee_module'),
    ('inventory_module'),
    ('payments_module'),
    ('work_distribution'),
    ('user_management')
) as modules(key)
where u.email = 'dhairyagautam@andrewsosborne.com'
on conflict (user_id, module_key) do nothing;

commit;

-- --- Verify -----------------------------------------------------------
-- Run this separately (after the block above commits) to confirm the
-- repair -- expect is_super_admin = true and modules to list all five
-- current keys.
select
    u.email,
    p.is_super_admin,
    p.active,
    array_agg(ump.module_key order by ump.module_key) as modules
from auth.users u
join public.profiles p on p.id = u.id
left join public.user_module_permissions ump on ump.user_id = p.id
where u.email = 'dhairyagautam@andrewsosborne.com'
group by u.email, p.is_super_admin, p.active;
