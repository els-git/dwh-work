-- Rollback of package 038: gives dwh_etl back exactly the privileges kept in audit.revoked_grant_038.
-- Run as dwh_admin:  psql -1 -v ON_ERROR_STOP=1 -f 038_audit_schema_etl_privileges.down.sql
DO $pre$
BEGIN
  IF to_regclass('audit.revoked_grant_038') IS NULL THEN RAISE EXCEPTION 'STOP: 038 is not applied'; END IF;
  IF EXISTS (SELECT 1 FROM audit.revoked_grant_038
              WHERE grantee <> 'dwh_etl'
                 OR (object_kind IN ('TABLE', 'SEQUENCE') AND (to_regclass(object_name) IS NULL
                     OR (SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                          WHERE c.oid = to_regclass(object_name)) <> 'audit'))
                 OR (object_kind = 'DEFAULT' AND split_part(object_name, ':', 2) NOT IN ('r', 'S'))
                 OR privilege_type NOT IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES',
                                           'TRIGGER', 'USAGE', 'MAINTAIN')) THEN
    RAISE EXCEPTION 'STOP: audit.revoked_grant_038 has a row package 038 could not have written';
  END IF;
END
$pre$;

DO $back$
DECLARE g record; n int := 0;
BEGIN
  FOR g IN SELECT * FROM audit.revoked_grant_038 ORDER BY 1, 2, 3, 4 LOOP
    IF g.object_kind = 'DEFAULT' THEN
      EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA audit GRANT %s ON %s TO dwh_etl',
                     split_part(g.object_name, ':', 1), g.privilege_type,
                     CASE WHEN split_part(g.object_name, ':', 2) = 'S' THEN 'SEQUENCES' ELSE 'TABLES' END);
    ELSE
      EXECUTE format('GRANT %s ON %s %s TO dwh_etl', g.privilege_type,
                     CASE WHEN g.object_kind = 'SEQUENCE' THEN 'SEQUENCE' ELSE 'TABLE' END, g.object_name);
    END IF;
    n := n + 1;
  END LOOP;
  RAISE NOTICE 'PRIVILEGES_RESTORED %', n;
END
$back$;

DROP TABLE audit.revoked_grant_038;

SELECT 'ROLLBACK_038_DONE' AS result;
