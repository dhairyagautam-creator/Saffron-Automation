-- Version 2.0 -- Role-Based Access Control: database foundation only.
--
-- Creates `roles` (the permission sets a user can be assigned) and
-- `profiles` (one row per auth.users row, linking a person to a role).
-- Nothing in the application reads these tables yet -- no permission
-- checks, no UI hide/show -- this migration only lays down the schema and
-- seeds the four default roles.
--
-- Run this once in the Supabase SQL Editor (Dashboard -> SQL Editor ->
-- New query -> paste -> Run). Safe to re-run: every statement is
-- idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING).

begin;

-- gen_random_uuid() ships enabled on every Supabase project already (it's
-- what auth.users.id itself uses) -- this is just a defensive no-op if that
-- were ever not the case.
create extension if not exists pgcrypto;

-- --- roles ------------------------------------------------------------
-- One row per permission set. Booleans are per-module flags rather than a
-- single "permissions" blob so a future UI can toggle one module at a time
-- without parsing/serializing anything.

create table if not exists public.roles (
    id uuid primary key default gen_random_uuid(),
    name text not null unique,
    employee_module boolean not null default false,
    inventory_module boolean not null default false,
    payments_module boolean not null default false,
    user_management boolean not null default false,
    created_at timestamptz not null default now()
);

-- RLS on, no policies yet -- these tables are fully inaccessible via the
-- app's anon/authenticated API keys until a later milestone adds explicit
-- read policies (once actual permission checks are being built). This is
-- the safe default: closed until something is deliberately opened, rather
-- than open until something is deliberately closed.
alter table public.roles enable row level security;

insert into public.roles (name, employee_module, inventory_module, payments_module, user_management)
values
    ('Admin',     true,  true,  true,  true),
    ('Inventory', false, true,  false, false),
    ('Accounts',  false, false, true,  false),
    ('HR',        true,  false, false, false)
on conflict (name) do nothing;

-- --- profiles -----------------------------------------------------------
-- One row per auth.users row (id IS the auth user's id, not a separate
-- generated key -- the standard Supabase pattern for a 1:1 profile table).
-- role_id is nullable: a user can exist without an assigned role yet
-- (e.g. immediately after signup, before an admin assigns one).

create table if not exists public.profiles (
    id uuid primary key references auth.users(id) on delete cascade,
    full_name text,
    role_id uuid references public.roles(id),
    active boolean not null default true,
    created_at timestamptz not null default now()
);

create index if not exists profiles_role_id_idx on public.profiles(role_id);

alter table public.profiles enable row level security;

-- Backfill: the one existing test user predates this table, so give them
-- a profile now (Admin, per instruction) rather than leaving them roleless.
-- Looked up by email since that's the only handle available here; safe to
-- re-run (updates role_id instead of erroring if the profile already exists).
insert into public.profiles (id, role_id, active)
select u.id, r.id, true
from auth.users u
cross join public.roles r
where u.email = 'dhairyagautam@andrewsosborne.com'
  and r.name = 'Admin'
on conflict (id) do update set role_id = excluded.role_id;

commit;
