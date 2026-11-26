-- I14-07 test of 038 (E0 sandbox, E1 copy), run after the package as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 038_audit_schema_etl_privileges_test.sql
-- One transaction, ROLLED BACK. Errors are compared by SQLSTATE (the dwh server writes Russian messages).
-- Expected last line: ALL_TESTS_PASS (X01-X07).
BEGIN;

CREATE FUNCTION pg_temp.ok(label text, cond boolean) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  IF cond IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', label; END IF;
  RAISE NOTICE 'PASS %', label;
END
$fn$;

CREATE FUNCTION pg_temp.try_as(p_role text, p_sql text) RETURNS text
LANGUAGE plpgsql AS $fn$
DECLARE v_state text := 'NO ERROR';
BEGIN
  BEGIN
    EXECUTE format('SET LOCAL ROLE %I', p_role);
    EXECUTE p_sql;
  EXCEPTION WHEN OTHERS THEN
    v_state := SQLSTATE;
  END;
  RESET ROLE;
  RETURN v_state;
END
$fn$;

SELECT pg_temp.ok('X01 dwh_etl cannot read the audit journal (42501)',
  pg_temp.try_as('dwh_etl', $q$SELECT count(*) FROM audit.security_event$q$) = '42501');
SELECT pg_temp.ok('X02 dwh_etl cannot add a row to audit.revoked_grant_036 (42501)',
  pg_temp.try_as('dwh_etl', $q$INSERT INTO audit.revoked_grant_036 VALUES ('TABLE', 'audit.security_event', 'dwh_etl', 'SELECT')$q$) = '42501');
SELECT pg_temp.ok('X03 dwh_etl cannot delete rows of audit.revoked_grant_036 (42501)',
  pg_temp.try_as('dwh_etl', $q$DELETE FROM audit.revoked_grant_036$q$) = '42501');

-- a new table of schema audit made by the owner of the default privileges gets nothing for dwh_etl
DO $t$
DECLARE v_owner text;
BEGIN
  SELECT pg_get_userbyid(c.relowner) INTO v_owner FROM pg_class c WHERE c.oid = 'audit.security_event'::regclass;
  EXECUTE format('SET LOCAL ROLE %I', v_owner);
  CREATE TABLE audit.x038_probe (id bigserial PRIMARY KEY, v text);
  RESET ROLE;
  PERFORM pg_temp.ok('X04 a new audit table and its sequence get no privilege for dwh_etl',
    NOT has_table_privilege('dwh_etl', 'audit.x038_probe', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
    AND NOT has_sequence_privilege('dwh_etl', 'audit.x038_probe_id_seq', 'SELECT,UPDATE,USAGE'));
END
$t$;

SELECT pg_temp.ok('X05 the privileges taken are kept for the rollback (036 table and journal)',
  EXISTS (SELECT 1 FROM audit.revoked_grant_038 WHERE object_name = 'audit.revoked_grant_036')
  AND EXISTS (SELECT 1 FROM audit.revoked_grant_038 WHERE object_name = 'audit.security_event'
                 AND privilege_type IN ('INSERT', 'SELECT')));
SELECT pg_temp.ok('X06 nobody but the owner reads audit.revoked_grant_038',
  NOT has_table_privilege('dwh_app', 'audit.revoked_grant_038', 'SELECT')
  AND NOT has_table_privilege('dwh_etl', 'audit.revoked_grant_038', 'SELECT'));
SELECT pg_temp.ok('X07 no function writes the journal with the rights of a working role',
  NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
                 AND p.prosrc ~* 'insert[[:space:]]+into[[:space:]]+audit\.security_event'
                 AND (NOT p.prosecdef OR pg_get_userbyid(p.proowner) IN ('dwh_etl', 'dwh_app'))));

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
