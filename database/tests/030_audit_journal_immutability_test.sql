-- I13-14 test of 030 (E0 sandbox, E1 copy), run after the package as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 030_audit_journal_immutability_test.sql
-- Everything happens in one transaction that is rolled back. Expected last line: ALL_TESTS_PASS (T01-T07).
BEGIN;

CREATE FUNCTION pg_temp.ok(label text, cond boolean) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  IF cond IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', label; END IF;
  RAISE NOTICE 'PASS %', label;
END
$fn$;

-- runs one statement as the given role (NULL = the owner) and returns 'SQLSTATE: message' or NO ERROR;
-- the checks use the SQLSTATE: the server of dwh writes its own messages in Russian
CREATE FUNCTION pg_temp.try_as(p_role text, p_sql text) RETURNS text
LANGUAGE plpgsql AS $fn$
BEGIN
  IF p_role IS NOT NULL THEN EXECUTE format('SET LOCAL ROLE %I', p_role); END IF;
  EXECUTE p_sql;
  IF p_role IS NOT NULL THEN RESET ROLE; END IF;
  RETURN 'NO ERROR';
EXCEPTION WHEN OTHERS THEN
  RETURN SQLSTATE || ': ' || SQLERRM;
END
$fn$;
GRANT EXECUTE ON FUNCTION pg_temp.try_as(text, text) TO dwh_etl;

DO $t$
DECLARE v_before bigint; v_id bigint; r text;
BEGIN
  SELECT count(*) INTO v_before FROM audit.security_event;
  SELECT max(security_event_id) INTO v_id FROM audit.security_event;

  r := pg_temp.try_as('dwh_etl', 'TRUNCATE audit.security_event');
  PERFORM pg_temp.ok('T01 dwh_etl cannot TRUNCATE the journal (' || r || ')', r LIKE '42501:%');
  r := pg_temp.try_as('dwh_etl', format('UPDATE audit.security_event SET outcome = outcome WHERE security_event_id = %s', coalesce(v_id, 0)));
  PERFORM pg_temp.ok('T02 dwh_etl cannot UPDATE (' || r || ')', r LIKE '42501:%');
  r := pg_temp.try_as('dwh_etl', format('DELETE FROM audit.security_event WHERE security_event_id = %s', coalesce(v_id, 0)));
  PERFORM pg_temp.ok('T03 dwh_etl cannot DELETE (' || r || ')', r LIKE '42501:%');
  r := pg_temp.try_as('dwh_etl', $$INSERT INTO audit.security_event (event_type, entity_type, action, outcome, source)
                                   VALUES ('I1314_TEST', 'TEST', 'TEST', 'SUCCESS', 'TEST')$$);
  PERFORM pg_temp.ok('T04 dwh_etl still INSERTs (' || r || ')', r = 'NO ERROR'
    AND (SELECT count(*) FROM audit.security_event) = v_before + 1);
  r := pg_temp.try_as(NULL, 'TRUNCATE audit.security_event');
  PERFORM pg_temp.ok('T05 the owner cannot TRUNCATE either (' || r || ')', r LIKE '%TRUNCATE of audit.security_event prohibited%');
  r := pg_temp.try_as(NULL, format('DELETE FROM audit.security_event WHERE security_event_id = %s', coalesce(v_id, 0)));
  PERFORM pg_temp.ok('T06 the owner cannot DELETE a row: append-only trigger (' || r || ')', r <> 'NO ERROR'
    OR v_id IS NULL);
  PERFORM audit.log_security_event('I1314_TEST', 'TEST', 'x', 'TEST', 'SUCCESS', '{}'::jsonb);
  PERFORM pg_temp.ok('T07 audit.log_security_event still writes; no row was lost',
    (SELECT count(*) FROM audit.security_event) = v_before + 2);
END
$t$;

ROLLBACK;
SELECT 'ALL_TESTS_PASS' AS result;
