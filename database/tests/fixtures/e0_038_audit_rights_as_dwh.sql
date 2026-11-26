-- E0 fixture of package 038 (sandbox only): schema audit gets the privileges seen on the dwh server
-- (security check and inventory of 26.11.2026): dwh_etl has all privileges on audit.revoked_grant_036,
-- INSERT and SELECT on audit.security_event, and default privileges of schema audit for dwh_admin.
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON audit.revoked_grant_036 TO dwh_etl;
GRANT INSERT, SELECT ON audit.security_event TO dwh_etl;
DO $f$
DECLARE s record;
BEGIN
  FOR s IN SELECT c.oid::regclass::text AS name FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'audit' AND c.relkind = 'S' LOOP
    EXECUTE format('GRANT SELECT, UPDATE, USAGE ON SEQUENCE %s TO dwh_etl', s.name);
  END LOOP;
END
$f$;
ALTER DEFAULT PRIVILEGES FOR ROLE dwh_admin IN SCHEMA audit GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO dwh_etl;
ALTER DEFAULT PRIVILEGES FOR ROLE dwh_admin IN SCHEMA audit GRANT SELECT, UPDATE, USAGE ON SEQUENCES TO dwh_etl;
