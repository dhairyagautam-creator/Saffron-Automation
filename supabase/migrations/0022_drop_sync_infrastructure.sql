-- Sync-removal teardown reference (Phase 6 of the sync-removal task).
--
-- *** DO NOT RUN THIS AGAINST PRODUCTION SUPABASE. ***
-- *** THIS FILE HAS NOT BEEN EXECUTED ANYWHERE.     ***
--
-- The application code that read/wrote every table and bucket below has
-- been removed (see app/sync_service.py and friends, deleted on branch
-- remove-sync-system). Nothing in the app calls these tables anymore.
-- This script is left here as a reviewed, ready-to-run reference for
-- whoever decides it's time to actually drop the cloud-side sync
-- infrastructure -- it is intentionally NOT wired into any migration
-- runner and NOT executed by this task.
--
-- Scope: only objects created for cross-machine data synchronization.
-- Auth/RBAC objects (0001-0007, 0020-0021: profiles, roles, permissions)
-- are untouched -- they back Supabase Auth/login, which is retained.

begin;

-- Milestone 9 generic cloud config store (app/rule_parameters.py,
-- app/inventory_parameters_service.py, app/payment_parameters_service.py
-- used to push/pull through this; see 0008_module_configurations.sql)
drop table if exists public.module_configurations;

-- Path Validator sync tables (see 0010-0013)
drop table if exists public.path_validator_imports;
drop table if exists public.path_validator_active_session;
drop table if exists public.path_validator_findings;
drop table if exists public.path_validator_email_notifications;
drop table if exists public.path_validator_organization_workbooks;

-- Inventory sync tables (see 0014-0015)
drop table if exists public.inventory_thresholds;
drop table if exists public.inventory_replenishment;

-- Payment Analytics sync tables (see 0016-0019)
drop table if exists public.payment_invoices;
drop table if exists public.payment_active_months;
drop table if exists public.payment_customer_profiles;
drop table if exists public.outstanding_invoices;

-- Storage buckets used only for cloud-synced workbook/import files
-- (see 0009_path_validator_storage_buckets.sql). Removing a bucket also
-- removes its objects -- double-check nothing else reads from these
-- before ever running this against a real project.
delete from storage.buckets where id = 'path-validator-operations-uploads';
delete from storage.buckets where id = 'path-validator-organization-data';

commit;
