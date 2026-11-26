-- I14-07 test of 037 (E0 sandbox, E1 copy), run after the package as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 037_ai_hidden_query_functions_test.sql
-- One transaction, ROLLED BACK. Errors are compared by SQLSTATE (the dwh server writes Russian messages).
-- Expected last line: ALL_TESTS_PASS (H01-H07).
BEGIN;

CREATE FUNCTION pg_temp.ok(label text, cond boolean) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  IF cond IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', label; END IF;
  RAISE NOTICE 'PASS %', label;
END
$fn$;

-- runs one statement as the given role and returns the SQLSTATE or NO ERROR
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

DO $t$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['dwh_app', 'dwh_etl', 'dwh_ai_ro', 'dwh_dm_sql_ro'] LOOP
    CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r);
    PERFORM pg_temp.ok('H01 ' || r || ': schema_to_xml refused (42501)',
      pg_temp.try_as(r, $q$SELECT schema_to_xml('pg_catalog', true, false, '')$q$) = '42501');
    PERFORM pg_temp.ok('H02 ' || r || ': query_to_xml refused (42501)',
      pg_temp.try_as(r, $q$SELECT query_to_xml('select 1', true, false, '')$q$) = '42501');
    PERFORM pg_temp.ok('H03 ' || r || ': database_to_xml refused (42501)',
      pg_temp.try_as(r, $q$SELECT database_to_xml(true, false, '')$q$) = '42501');
    PERFORM pg_temp.ok('H04 ' || r || ': ts_stat refused (42501)',
      pg_temp.try_as(r, $q$SELECT * FROM ts_stat('select 1')$q$) = '42501');
  END LOOP;
END
$t$;

SELECT pg_temp.ok('H05 other XML functions still work (xmlelement)',
  pg_temp.try_as('dwh_app', $q$SELECT xmlelement(name a)$q$) = 'NO ERROR');
SELECT pg_temp.ok('H06 every revoked privilege is kept for the rollback',
  (SELECT count(*) FROM audit.revoked_grant_037 WHERE grantee = 'PUBLIC') >= 16);
SELECT pg_temp.ok('H07 nobody but the owner reads audit.revoked_grant_037',
  NOT has_table_privilege('dwh_app', 'audit.revoked_grant_037', 'SELECT')
  AND NOT has_table_privilege('dwh_etl', 'audit.revoked_grant_037', 'SELECT'));

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
