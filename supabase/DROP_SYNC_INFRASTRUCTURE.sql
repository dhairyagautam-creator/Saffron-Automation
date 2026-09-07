-- Sync infrastructure teardown -- revised from
-- supabase/migrations/0022_drop_sync_infrastructure.sql.
--
-- *** DO NOT RUN AGAINST PRODUCTION UNTIL AUDIT.sql'S RESULTS HAVE BEEN ***
-- *** PASTED BACK AND CONFIRMED TO MATCH WHAT THIS SCRIPT TARGETS.      ***
--
-- Scope: only objects created by migrations 0008-0019 (cloud sync
-- infrastructure for module_configurations, Path Validator, Inventory,
-- Payments). The application code that read/wrote all of it was removed on
-- branch remove-sync-system (commit 2401fab) -- nothing in the app calls
-- these tables/buckets anymore.
--
-- EXPLICITLY EXCLUDED -- auth/RBAC (migrations 0001-0007, 0020-0021).
-- Dropping any of these locks every user out of the app; verified by
-- reading all five files, not assumed:
--   tables:     public.roles, public.profiles, public.user_module_permissions
--   functions:  public.is_user_management_admin(), public.get_all_users()
--   column:     public.profiles.is_super_admin  (added by 0020)
--   policies:   every policy on the three tables above (0001-0007, 0020)
--   extension:  pgcrypto (0001) -- shared; also backs the sync tables'
--               gen_random_uuid() defaults, but it's database-wide, not
--               sync-specific, so it is not touched here either way.
--
-- Changes from 0022:
--   - every DROP guarded with IF EXISTS (0022 already did this for tables;
--     extended here to triggers, functions and storage objects too)
--   - grouped and commented by originating migration, so a section can be
--     run on its own
--   - explicit drop order inside Path Validator: path_validator_findings /
--     path_validator_email_notifications / path_validator_active_session
--     before path_validator_imports, because all three hold a foreign key
--     into path_validator_imports(cloud_id) -- 0022's flat DROP TABLE list
--     would have failed on that constraint the first time it was actually run
--   - drops the 12 trigger functions from 0008/0010-0019. 0022 dropped the
--     tables (which drops their triggers via cascade) but never dropped the
--     functions themselves -- a function isn't owned by the table its
--     trigger fires on, so cascade never reaches it and it would have been
--     left behind as dead code in the database
--   - drops the 3 storage.objects RLS policies from 0009. They reference
--     the two path-validator buckets by name inside their USING/WITH CHECK
--     clause, but they live on the shared storage.objects table, not on the
--     bucket row -- 0022 never touched them, so they would have survived
--     the bucket deletion as dead policies referencing nonexistent buckets
--
-- STORAGE BUCKETS ARE NOT HANDLED BY THIS SCRIPT. Supabase runs a
-- storage.protect_delete() trigger that rejects any direct SQL DELETE on
-- storage.objects/storage.buckets ("Direct deletion from storage tables is
-- not allowed. Use the Storage API instead.") -- confirmed by actually
-- running 0022's approach against the live project and hitting that error.
-- Deleting only the DB row would also orphan the real file in the backing
-- store, which is exactly what that trigger exists to prevent. Empty and
-- delete the two buckets by hand instead, in the Dashboard:
--   Storage -> path-validator-operations-uploads -> select all -> Delete
--   Storage -> path-validator-organization-data -> select all -> Delete
--   then delete each bucket itself (the "..." menu on the bucket -> Delete
--   bucket, or the trash icon, depending on your Dashboard version).
-- Per AUDIT.sql's results: 134 objects in the first bucket, 3 in the second.

begin;

-- ============================================================
-- 0008_module_configurations.sql
-- ============================================================
drop trigger if exists set_module_configurations_audit_fields on public.module_configurations;
drop table if exists public.module_configurations;
drop function if exists public.set_module_configuration_audit_fields();

-- ============================================================
-- 0010-0013: Path Validator sync tables
-- Order matters -- findings/emails/active_session hold FKs into
-- path_validator_imports(cloud_id), so they must be dropped first.
-- ============================================================
drop trigger if exists set_path_validator_findings_audit_fields on public.path_validator_findings;
drop table if exists public.path_validator_findings;
drop function if exists public.set_path_validator_findings_audit_fields();

drop trigger if exists set_path_validator_emails_audit_fields on public.path_validator_email_notifications;
drop table if exists public.path_validator_email_notifications;
drop function if exists public.set_path_validator_emails_audit_fields();

drop trigger if exists set_path_validator_active_session_audit_fields on public.path_validator_active_session;
drop table if exists public.path_validator_active_session;
drop function if exists public.set_path_validator_active_session_audit_fields();

drop trigger if exists set_path_validator_imports_audit_fields on public.path_validator_imports;
drop table if exists public.path_validator_imports;
drop function if exists public.set_path_validator_imports_audit_fields();

drop trigger if exists set_path_validator_org_workbooks_audit_fields on public.path_validator_organization_workbooks;
drop table if exists public.path_validator_organization_workbooks;
drop function if exists public.set_path_validator_org_workbooks_audit_fields();

-- ============================================================
-- 0014-0015: Inventory sync tables
-- ============================================================
drop trigger if exists set_inventory_thresholds_audit_fields on public.inventory_thresholds;
drop table if exists public.inventory_thresholds;
drop function if exists public.set_inventory_thresholds_audit_fields();

drop trigger if exists set_inventory_replenishment_audit_fields on public.inventory_replenishment;
drop table if exists public.inventory_replenishment;
drop function if exists public.set_inventory_replenishment_audit_fields();

-- ============================================================
-- 0016-0019: Payment Analytics sync tables
-- ============================================================
drop trigger if exists set_payment_invoices_audit_fields on public.payment_invoices;
drop table if exists public.payment_invoices;
drop function if exists public.set_payment_invoices_audit_fields();

drop trigger if exists set_payment_active_months_audit_fields on public.payment_active_months;
drop table if exists public.payment_active_months;
drop function if exists public.set_payment_active_months_audit_fields();

drop trigger if exists set_payment_customer_profiles_audit_fields on public.payment_customer_profiles;
drop table if exists public.payment_customer_profiles;
drop function if exists public.set_payment_customer_profiles_audit_fields();

drop trigger if exists set_outstanding_invoices_audit_fields on public.outstanding_invoices;
drop table if exists public.outstanding_invoices;
drop function if exists public.set_outstanding_invoices_audit_fields();

-- ============================================================
-- 0009: Path Validator storage RLS policies.
-- The buckets themselves are NOT dropped here -- see the note above the
-- `begin;` block. Empty and delete them by hand in the Dashboard, either
-- before or after running this script; these policy drops don't depend on
-- the buckets still existing.
-- ============================================================
drop policy if exists "Authenticated users can read path validator storage" on storage.objects;
drop policy if exists "Authenticated users can write path validator storage" on storage.objects;
drop policy if exists "Authenticated users can update path validator storage" on storage.objects;

commit;
