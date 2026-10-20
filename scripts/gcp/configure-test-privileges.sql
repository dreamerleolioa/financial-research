-- Run after restore as the owner/admin against financial_research_test only.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
  IF current_database() <> 'financial_research_test' THEN
    RAISE EXCEPTION 'Wrong database for test privileges';
  END IF;
  IF EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname IN ('public','maintenance_backup')
      AND c.relkind IN ('r','S') AND r.rolname <> 'financial_test_migrator') THEN
    RAISE EXCEPTION 'Restore objects as financial_test_migrator before granting access';
  END IF;
END $$;
SET LOCAL ROLE financial_test_migrator;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO financial_test_migrator;
GRANT USAGE ON SCHEMA public TO financial_test_api, financial_debug_readonly;
GRANT USAGE ON SCHEMA maintenance_backup TO financial_debug_readonly;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO financial_test_api;
REVOKE INSERT, UPDATE, DELETE ON public.alembic_version FROM financial_test_api;
DO $$ BEGIN
  IF to_regclass('public.auth_allowed_accounts') IS NOT NULL THEN
    REVOKE ALL ON public.auth_allowed_accounts FROM PUBLIC;
    REVOKE ALL ON public.auth_allowed_accounts FROM financial_test_api;
    GRANT SELECT ON public.auth_allowed_accounts TO financial_test_api;
  END IF;
END $$;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO financial_test_api;
GRANT SELECT ON ALL TABLES IN SCHEMA public, maintenance_backup TO financial_debug_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public, maintenance_backup TO financial_debug_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE financial_test_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO financial_test_api;
ALTER DEFAULT PRIVILEGES FOR ROLE financial_test_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO financial_test_api;
ALTER DEFAULT PRIVILEGES FOR ROLE financial_test_migrator IN SCHEMA public, maintenance_backup
  GRANT SELECT ON TABLES TO financial_debug_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE financial_test_migrator IN SCHEMA public, maintenance_backup
  GRANT SELECT ON SEQUENCES TO financial_debug_readonly;
COMMIT;
