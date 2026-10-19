-- Operator runs against postgres after checking the two empty preparation databases.
-- Creates passwordless roles; users must set passwords themselves before use.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
  IF current_database() <> 'postgres' OR
     NOT EXISTS (SELECT FROM pg_database WHERE datname='financial_research') OR
     NOT EXISTS (SELECT FROM pg_database WHERE datname='health-tracking') THEN
    RAISE EXCEPTION 'Expected postgres and both test databases';
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname IN
      ('financial_prod_migrator','financial_prod_api','financial_prod_debug_readonly')) THEN
    RAISE EXCEPTION 'Role exists: inspect privileges rather than reusing it';
  END IF;
END $$;
CREATE ROLE financial_prod_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
CREATE ROLE financial_prod_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10;
CREATE ROLE financial_prod_debug_readonly LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
ALTER ROLE financial_prod_debug_readonly SET default_transaction_read_only = on;
ALTER ROLE financial_prod_debug_readonly SET statement_timeout = '60s';
ALTER ROLE financial_prod_debug_readonly SET idle_in_transaction_session_timeout = '60s';
REVOKE ALL ON DATABASE financial_research FROM PUBLIC;
REVOKE ALL ON DATABASE "health-tracking" FROM PUBLIC;
-- Cloud SQL's postgres is an administrator, not a true superuser. Ownership
-- transfer requires explicit SET ROLE membership in the new owner role.
GRANT financial_prod_migrator TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;
GRANT CONNECT ON DATABASE financial_research TO financial_prod_api, financial_prod_debug_readonly;
ALTER DATABASE financial_research OWNER TO financial_prod_migrator;
-- ALTER OWNER rewrites the old owner's ACL entries. Preserve CONNECT after
-- transfer, acting as the new owner with the operator identity captured first.
DO $$ DECLARE operator_name name := current_user; BEGIN
  SET LOCAL ROLE financial_prod_migrator;
  EXECUTE format('GRANT CONNECT ON DATABASE financial_research TO %I', operator_name);
END $$;
COMMIT;
