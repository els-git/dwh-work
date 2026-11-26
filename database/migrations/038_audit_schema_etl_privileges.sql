-- Package 038 (I14-07, finding N4 and N6): dwh_etl loses its direct privileges in schema audit.
--   P1 rows of audit.revoked_grant_036 are checked against the lists of package 036: an object outside them, another
--      grantee or another privilege -> STOP (the rollback of 036 GRANTs by these rows);
--   P2 every privilege of dwh_etl on tables, views and sequences of schema audit is taken
--      (audit.revoked_grant_036: all; audit.security_event: INSERT, SELECT); events of loads are written by
--      SECURITY DEFINER functions of the owner, which this package checks first;
--   P3 default privileges of schema audit for dwh_etl are taken, so new audit tables do not get them.
-- Every privilege taken is kept in audit.revoked_grant_038 (owner only); the rollback restores it exactly.
-- Decision of I13-14 A1 (keep INSERT, SELECT) is replaced by the user's decision of 26.11.2026 (I14-07, package 038);
-- check 030 gives 5/6 after this package (its check 4 expects INSERT and SELECT).
-- Report: docs/DWH_I14_07_SECURITY_AUDIT_REPORT_20261126.md.
-- Run as a superuser role (dwh_admin): psql -1 -v ON_ERROR_STOP=1 -f 038_audit_schema_etl_privileges.sql
DO $pre$
BEGIN
  IF to_regclass('audit.revoked_grant_037') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 037 is not applied';
  END IF;
  IF to_regclass('audit.revoked_grant_038') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 038 is already applied';
  END IF;
  IF to_regclass('audit.revoked_grant_036') IS NULL THEN
    RAISE EXCEPTION 'STOP: audit.revoked_grant_036 is missing (package 036)';
  END IF;
  IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
    RAISE EXCEPTION 'STOP: default privileges of another owner need a superuser (dwh_admin)';
  END IF;
  -- code that would write the journal with the caller's rights: such a function must not exist
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
              WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
                AND p.prosrc ~* 'insert[[:space:]]+into[[:space:]]+audit\.security_event'
                AND (NOT p.prosecdef OR pg_get_userbyid(p.proowner) IN ('dwh_etl', 'dwh_app'))) THEN
    RAISE EXCEPTION 'STOP: a function writes audit.security_event with the rights of a working role';
  END IF;
  IF (SELECT pg_get_userbyid(p.proowner) FROM pg_proc p
       WHERE p.oid = to_regprocedure('audit.log_security_event(text,text,text,text,text,jsonb)'))
     IN ('dwh_etl', 'dwh_app') THEN
    RAISE EXCEPTION 'STOP: audit.log_security_event is owned by a working role';
  END IF;
END
$pre$;

-- Rows of audit.revoked_grant_036 that package 036 could not have written (I14-07, N4). The rollback of 036 GRANTs
-- by these rows, so a row added by someone else would hand out a privilege. Same list as 036 P1 and P2.
CREATE FUNCTION pg_temp.revoked_036_invalid() RETURNS SETOF text
LANGUAGE sql STABLE SET search_path TO 'pg_catalog' AS $v$
  SELECT format('%s %s %s %s', g.object_kind, g.object_name, g.grantee, g.privilege_type)
    FROM audit.revoked_grant_036 g
   WHERE NOT (
     (g.object_kind = 'TABLE' AND g.grantee = 'dwh_etl'
      AND to_regclass(g.object_name) IS NOT NULL
      AND ((to_regclass(g.object_name) IN (SELECT to_regclass(t) FROM unnest(ARRAY['dq.issue', 'dq.result', 'dq.rule',
              'dq.run', 'dq.run_rule_snapshot', 'etl.batch_run', 'etl.error_log', 'etl.job', 'etl.job_run',
              'etl.run_request', 'etl.watermark', 'meta.batch_contract_snapshot', 'meta.batch_mapping_snapshot',
              'meta.column_contract', 'meta.mapping_rule', 'meta.mapping_source_ref', 'meta.metadata_capture',
              'meta.source_column_snapshot', 'meta.source_object', 'meta.source_object_snapshot',
              'meta.source_system']) t)
            AND g.privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'))
        OR (to_regclass(g.object_name) IN (SELECT to_regclass(t)
              FROM unnest(ARRAY['etl.object_record_change', 'etl.object_ods_record_change']) t)
            AND g.privilege_type IN ('UPDATE', 'DELETE', 'TRUNCATE'))))
     OR (g.object_kind = 'FUNCTION' AND g.privilege_type = 'EXECUTE' AND g.grantee IN ('PUBLIC', 'dwh_app', 'dwh_etl')
         AND to_regprocedure(g.object_name) IS NOT NULL
         AND (SELECT n.nspname || '.' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               WHERE p.oid = to_regprocedure(g.object_name)) IN (
           'app.integration_contract_create', 'app.integration_contract_set_active', 'app.integration_contract_version',
           'app.integration_dq_rule_create', 'app.integration_dq_rule_set_active', 'app.integration_dq_rule_version',
           'app.integration_mapping_create', 'app.integration_mapping_set_active', 'app.integration_mapping_version',
           'app.integration_run_request_create', 'app.integration_run_request_cancel',
           'etl.integration_run_request_claim', 'etl.integration_run_request_fail',
           'etl.integration_run_request_start', 'etl.integration_run_request_success')))
   ORDER BY 1
$v$;

DO $p1$
DECLARE v_bad text[] := ARRAY(SELECT pg_temp.revoked_036_invalid());
BEGIN
  IF cardinality(v_bad) > 0 THEN
    RAISE EXCEPTION 'STOP: audit.revoked_grant_036 has % row(s) package 036 did not write: %', cardinality(v_bad),
      array_to_string(v_bad[1:5], '; ');
  END IF;
  RAISE NOTICE 'REVOKED_GRANT_036_ROWS_VALID %', (SELECT count(*) FROM audit.revoked_grant_036);
END
$p1$;

CREATE TABLE audit.revoked_grant_038 (
  object_kind text NOT NULL CHECK (object_kind IN ('TABLE', 'SEQUENCE', 'DEFAULT')),
  object_name text NOT NULL,   -- regclass text, or <owner>:<r|S> for default privileges of schema audit
  grantee text NOT NULL CHECK (grantee = 'dwh_etl'),
  privilege_type text NOT NULL,
  PRIMARY KEY (object_kind, object_name, grantee, privilege_type)
);
REVOKE ALL ON audit.revoked_grant_038 FROM PUBLIC;
DO $acl$
DECLARE r record;
BEGIN
  FOR r IN SELECT DISTINCT a.grantee FROM pg_class c, aclexplode(c.relacl) a
            WHERE c.oid = 'audit.revoked_grant_038'::regclass AND a.grantee <> c.relowner
  LOOP
    EXECUTE format('REVOKE ALL ON audit.revoked_grant_038 FROM %s',
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED audit.revoked_grant_038 FROM %',
                 CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(r.grantee) END;
  END LOOP;
END
$acl$;

DO $p2$
DECLARE r record; n int := 0;
BEGIN
  FOR r IN SELECT c.oid, c.oid::regclass::text AS name, c.relkind, a.privilege_type
             FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace AND s.nspname = 'audit',
                  aclexplode(c.relacl) a
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S') AND a.grantee = 'dwh_etl'::regrole
              AND c.oid <> 'audit.revoked_grant_038'::regclass
            ORDER BY 2, 4 LOOP
    INSERT INTO audit.revoked_grant_038
      VALUES (CASE WHEN r.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, r.name, 'dwh_etl', r.privilege_type);
    n := n + 1;
  END LOOP;
  FOR r IN SELECT DISTINCT object_kind, object_name FROM audit.revoked_grant_038 LOOP
    EXECUTE format('REVOKE ALL ON %s %s FROM dwh_etl', CASE WHEN r.object_kind = 'SEQUENCE' THEN 'SEQUENCE'
                   ELSE 'TABLE' END, r.object_name);
  END LOOP;
  RAISE NOTICE 'OBJECT_PRIVILEGES_REVOKED %', n;
END
$p2$;

DO $p3$
DECLARE r record; n int := 0;
BEGIN
  FOR r IN SELECT pg_get_userbyid(d.defaclrole) AS owner, d.defaclobjtype AS objtype, a.privilege_type
             FROM pg_default_acl d JOIN pg_namespace s ON s.oid = d.defaclnamespace AND s.nspname = 'audit',
                  aclexplode(d.defaclacl) a
            WHERE a.grantee = 'dwh_etl'::regrole AND d.defaclobjtype IN ('r', 'S')
            ORDER BY 1, 2, 3 LOOP
    INSERT INTO audit.revoked_grant_038 VALUES ('DEFAULT', r.owner || ':' || r.objtype::text, 'dwh_etl', r.privilege_type);
    n := n + 1;
  END LOOP;
  FOR r IN SELECT DISTINCT split_part(object_name, ':', 1) AS owner, split_part(object_name, ':', 2) AS objtype
             FROM audit.revoked_grant_038 WHERE object_kind = 'DEFAULT' LOOP
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA audit REVOKE ALL ON %s FROM dwh_etl', r.owner,
                   CASE WHEN r.objtype = 'S' THEN 'SEQUENCES' ELSE 'TABLES' END);
  END LOOP;
  RAISE NOTICE 'DEFAULT_PRIVILEGES_REVOKED %', n;
END
$p3$;

SELECT 'MIGRATION_038_DONE' AS result;
