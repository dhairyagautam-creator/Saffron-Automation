-- Version 2.1 -- Permission system redesign: module-based, per-user grants
-- instead of shared named roles.
--
-- Problem being fixed: `roles` (migration 0001) bundles module access into
-- a handful of named, SHARED rows (Admin/Inventory/Accounts/HR) -- editing
-- one role's flags changes every user on that role at once, and adding a
-- new module requires a schema migration (a new boolean column) plus a
-- code change in three separate places. This migration replaces the
-- ACCESS mechanism with a generic, per-user table keyed by a free-form
-- module key string, so a brand-new module never needs a schema change
-- again -- only a new row in app/module_registry.py (Python-side, no SQL).
--
-- `roles` and `profiles.role_id` are NOT dropped -- this migration is
-- purely additive, so it stays reversible and nothing else that might
-- still reference them breaks. The application stops READING them for
-- access decisions after this ships; see app/permissions.py.
--
-- Run in the Supabase SQL Editor (Dashboard -> SQL Editor -> New query ->
-- paste -> Run), same as every migration before this one. Safe to re-run.

begin;

-- --- profiles.is_super_admin ---------------------------------------------
-- A super admin can access every module automatically, including one
-- added after this flag was set -- this is what makes "administrators
-- automatically gain access to new modules with zero maintenance" true.
-- Deliberately NOT modeled as "has every module_key row": that would
-- require inserting a new row for every super admin every time a module
-- is added, which is exactly the maintenance burden this redesign removes.

alter table public.profiles
    add column if not exists is_super_admin boolean not null default false;

-- Backfill: whoever currently holds the 'Admin' role (migration 0001's
-- only seeded role with every module flag true) becomes a super admin.
-- Mechanical, not a judgment call -- 'Admin' is the sole role where this
-- is unambiguous; every other seeded role (Inventory/Accounts/HR) grants
-- only a partial module set and does not qualify.
update public.profiles p
set is_super_admin = true
from public.roles r
where p.role_id = r.id
  and r.name = 'Admin';

-- --- user_module_permissions ----------------------------------------------
-- One row per (user, module) grant. Presence of a row IS the grant --
-- there is no boolean column to flip, so removing access is a DELETE, not
-- an UPDATE. module_key is a free-form string matching
-- app.module_registry's own ModuleDef.key -- intentionally NOT a foreign
-- key into any Postgres-side module list, since the module registry lives
-- in application code (Python), not the database; this table only stores
-- grants against whatever key the app currently defines.

create table if not exists public.user_module_permissions (
    user_id uuid not null references public.profiles(id) on delete cascade,
    module_key text not null,
    granted_at timestamptz not null default now(),
    primary key (user_id, module_key)
);

create index if not exists user_module_permissions_user_id_idx
    on public.user_module_permissions(user_id);

alter table public.user_module_permissions enable row level security;

-- --- Migrate existing role-based grants into explicit rows ----------------
-- Mechanical translation, one insert per legacy boolean column, preserving
-- exactly what each user could already access today (super admins get
-- their per-module rows too, alongside is_super_admin=true above --
-- harmless belt-and-suspenders, since can_access() checks is_super_admin
-- first anyway; see app/permissions.py).
--
-- Work Distribution is deliberately EXCLUDED from this backfill, per
-- explicit instruction: it has never had a permission gate before this
-- migration (hardcoded visible to everyone), and the decision made when
-- this redesign shipped was to close that gap strictly -- only super
-- admins get it automatically (via is_super_admin, not a row here); every
-- other existing user starts WITHOUT Work Distribution and must be
-- re-granted it explicitly in User Management if they still need it.

insert into public.user_module_permissions (user_id, module_key)
select p.id, 'employee_module'
from public.profiles p
join public.roles r on r.id = p.role_id
where r.employee_module = true
on conflict (user_id, module_key) do nothing;

insert into public.user_module_permissions (user_id, module_key)
select p.id, 'inventory_module'
from public.profiles p
join public.roles r on r.id = p.role_id
where r.inventory_module = true
on conflict (user_id, module_key) do nothing;

insert into public.user_module_permissions (user_id, module_key)
select p.id, 'payments_module'
from public.profiles p
join public.roles r on r.id = p.role_id
where r.payments_module = true
on conflict (user_id, module_key) do nothing;

insert into public.user_module_permissions (user_id, module_key)
select p.id, 'user_management'
from public.profiles p
join public.roles r on r.id = p.role_id
where r.user_management = true
on conflict (user_id, module_key) do nothing;

-- --- is_user_management_admin(): redefined, not replaced ------------------
-- Migration 0007 introduced this SECURITY DEFINER function specifically to
-- avoid RLS self-recursion (see that file's own comment) -- every
-- admin-gated policy on `profiles` (0005/0006/0007) already calls this
-- function rather than repeating the check inline, so redefining its BODY
-- here (same signature, same name) automatically upgrades every one of
-- those existing policies to the new module-based check with zero edits
-- to the policies themselves. Still SECURITY DEFINER for the same reason
-- as before: its internal query must bypass RLS on both tables it reads,
-- or checking "is caller an admin" would recursively trigger the very
-- policies it's being used to evaluate.

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
    where p.id = auth.uid()
      and (
        p.is_super_admin = true
        or exists (
          select 1
          from public.user_module_permissions ump
          where ump.user_id = p.id and ump.module_key = 'user_management'
        )
      )
  );
$$;

-- --- RLS: user_module_permissions -----------------------------------------
-- Own-row read (every signed-in user needs to load their OWN grants right
-- after login -- see app/rbac_service.py) plus full admin read/write
-- (User Management's Add/Edit flows), same "own row, or is_user_management
-- _admin()" shape every profiles policy above already uses.

drop policy if exists "Users can read their own module permissions" on public.user_module_permissions;
create policy "Users can read their own module permissions"
on public.user_module_permissions for select
to authenticated
using (user_id = auth.uid());

drop policy if exists "Admins can read all module permissions" on public.user_module_permissions;
create policy "Admins can read all module permissions"
on public.user_module_permissions for select
to authenticated
using (public.is_user_management_admin());

drop policy if exists "Admins can grant module permissions" on public.user_module_permissions;
create policy "Admins can grant module permissions"
on public.user_module_permissions for insert
to authenticated
with check (public.is_user_management_admin());

-- An admin can revoke any grant EXCEPT their own user_management grant --
-- the DB-level backstop for "don't lock yourself out of User Management,"
-- mirroring the existing "can't disable your own account" pattern
-- (migration 0005/0007) as closely as RLS allows. app/user_management_
-- service.py also blocks this client-side first, for a faster/clearer
-- error message -- same two-layer shape as the self-disable guard.
drop policy if exists "Admins can revoke module permissions" on public.user_module_permissions;
create policy "Admins can revoke module permissions"
on public.user_module_permissions for delete
to authenticated
using (
  public.is_user_management_admin()
  and not (user_id = auth.uid() and module_key = 'user_management')
);

grant select, insert, delete on public.user_module_permissions to authenticated;

-- --- get_all_users(): redefined for the new module-based shape -----------
-- Same SECURITY DEFINER function migration 0004 introduced (the one
-- deliberate place auth.users data reaches the app) and the same internal
-- permission check (still via is_user_management_admin(), now upgraded
-- automatically by the redefinition above) -- only the SELECT list and
-- join change: role_id/role_name are dropped (the app no longer reads
-- roles for access decisions -- see app/permissions.py), replaced by
-- is_super_admin and a modules array aggregated from
-- user_module_permissions. array_agg over a left join returns NULL (not
-- an empty array) for a user with zero grants, so `coalesce(..., '{}')`
-- guards that -- app/user_management_service.py otherwise has to
-- special-case None vs. [] for no good reason.
--
-- Postgres refuses CREATE OR REPLACE when the OUT columns themselves
-- change (only the body/logic can change in place) -- migration 0004's
-- version returned role_id/role_name, this one returns is_super_admin/
-- modules instead, so the old signature must be dropped explicitly first.

drop function if exists public.get_all_users();

create or replace function public.get_all_users()
returns table (
    id uuid,
    email text,
    full_name text,
    is_super_admin boolean,
    modules text[],
    active boolean,
    created_at timestamptz
)
language plpgsql
security definer
set search_path = public, pg_catalog
as $$
begin
  if not public.is_user_management_admin() then
    raise exception 'Access denied: user_management permission required';
  end if;

  return query
  select
    p.id,
    u.email::text,
    p.full_name,
    p.is_super_admin,
    coalesce(array_agg(ump.module_key) filter (where ump.module_key is not null), '{}'),
    p.active,
    p.created_at
  from public.profiles p
  join auth.users u on u.id = p.id
  left join public.user_module_permissions ump on ump.user_id = p.id
  group by p.id, u.email, p.full_name, p.is_super_admin, p.active, p.created_at
  order by p.created_at desc;
end;
$$;

revoke all on function public.get_all_users() from public;
grant execute on function public.get_all_users() to authenticated;

commit;
