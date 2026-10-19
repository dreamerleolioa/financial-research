-- Run after restore as the owner/admin against financial_research only.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
  IF current_database() <> 'financial_research' THEN
    RAISE EXCEPTION 'Wrong database for production preparation privileges';
  END IF;
  IF EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname IN ('public','maintenance_backup')
      AND c.relkind IN ('r','S') AND r.rolname <> 'financial_prod_migrator') THEN
    RAISE EXCEPTION 'Restore objects as financial_prod_migrator before granting access';
  END IF;
END $$;
SET LOCAL ROLE financial_prod_migrator;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO financial_prod_migrator;
GRANT USAGE ON SCHEMA public TO financial_prod_api, financial_prod_debug_readonly;
GRANT USAGE ON SCHEMA maintenance_backup TO financial_prod_debug_readonly;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO financial_prod_api;
REVOKE INSERT, UPDATE, DELETE ON public.alembic_version FROM financial_prod_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO financial_prod_api;
GRANT SELECT ON ALL TABLES IN SCHEMA public, maintenance_backup TO financial_prod_debug_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public, maintenance_backup TO financial_prod_debug_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE financial_prod_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO financial_prod_api;
ALTER DEFAULT PRIVILEGES FOR ROLE financial_prod_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO financial_prod_api;
ALTER DEFAULT PRIVILEGES FOR ROLE financial_prod_migrator IN SCHEMA public, maintenance_backup
  GRANT SELECT ON TABLES TO financial_prod_debug_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE financial_prod_migrator IN SCHEMA public, maintenance_backup
  GRANT SELECT ON SEQUENCES TO financial_prod_debug_readonly;
COMMIT;
