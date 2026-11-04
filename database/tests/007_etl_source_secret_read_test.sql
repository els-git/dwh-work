-- I04 test of 007: service READ of a source credential for the ETL role.
-- Everything runs in one transaction and is ROLLED BACK; no row survives.
-- For E0 (sandbox with fixtures) and E1 (restored copy), never the working dwh.
-- As in the 005 test, app.integration_admin_session_user() is replaced inside the
-- transaction by a version mapping two fake tokens to an active admin and to a
-- foreign user id; ROLLBACK restores the real function. Test sources and objects
-- are created inside the transaction.
-- The actor of an ETL READ is session_user (the login role running the test).
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 007_etl_source_secret_read_test.sql
-- Expected last line: ALL_TESTS_PASS (labels U01-U12: 12 checks).
BEGIN;

CREATE FUNCTION pg_temp.expect_fail(label text, stmt text) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  BEGIN
    EXECUTE stmt;
  EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'PASS %: rejected (%)', label, SQLERRM;
    RETURN;
  END;
  RAISE EXCEPTION 'FAIL %: statement was accepted', label;
END
$fn$;

CREATE FUNCTION pg_temp.ok(label text, cond boolean) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  IF cond IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', label; END IF;
  RAISE NOTICE 'PASS %', label;
END
$fn$;

DO $setup$
DECLARE
  v_admin bigint := (SELECT user_id FROM app.user_account
                      WHERE is_active AND account_level IN ('SUPERADMIN', 'ADMIN') ORDER BY user_id LIMIT 1);
BEGIN
  IF v_admin IS NULL THEN RAISE EXCEPTION 'FAIL setup: no active admin user'; END IF;
  EXECUTE format($f$
    CREATE OR REPLACE FUNCTION app.integration_admin_session_user(p_token_hash text)
     RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'audit' AS $b$
    DECLARE v bigint;
    BEGIN
      v := CASE p_token_hash WHEN repeat('a', 64) THEN %s WHEN repeat('b', 64) THEN 999999999 END;
      IF v IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
      PERFORM set_config('dwh.actor_user_id', v::text, true);
      PERFORM set_config('dwh.security_source', 'INTEGRATION_CONTROL', true);
      RETURN v;
    END; $b$ $f$, v_admin);
END
$setup$;

DO $t$
DECLARE
  tok text := repeat('a', 64);
  src bigint;
  bare bigint;
  rd record;
  r record;
  r2 record;
  v integer;
  op uuid;
  aop uuid;
BEGIN
  INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
  VALUES ('I04_ETL_TEST', 'I04 ETL test source', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO src;
  INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
  VALUES ('I04_ETL_BARE', 'I04 ETL source without connection', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO bare;

  PERFORM pg_temp.expect_fail('U01 unknown source code',
    'SELECT * FROM etl.source_secret_read_begin(''I04_NO_SUCH_SOURCE'')');
  PERFORM pg_temp.expect_fail('U02 source without connection settings',
    'SELECT * FROM etl.source_secret_read_begin(''I04_ETL_BARE'')');
  v := app.integration_source_connection_save(tok, src, 'DATABASE', '{"host": "db.local", "port": 5432}');
  PERFORM pg_temp.expect_fail('U03 source without an active credential',
    'SELECT * FROM etl.source_secret_read_begin(''I04_ETL_TEST'')');
  SELECT * INTO rd FROM app.integration_source_secret_begin(tok, src);
  PERFORM app.integration_source_secret_complete(tok, src, rd.operation_id, true, repeat('0', 64), 'K_ETL_01');

  SELECT * INTO r FROM etl.source_secret_read_begin('I04_ETL_TEST');
  PERFORM pg_temp.ok('U04 READ returns the ACTIVE secret_ref and key_id',
    r.secret_ref = rd.secret_ref AND r.key_id = 'K_ETL_01');
  op := r.operation_id;
  PERFORM pg_temp.ok('U05 ATTEMPT journaled as SERVICE with the login role',
    EXISTS (SELECT 1 FROM secret_store.operation_event e WHERE e.operation_id = op AND e.operation_type = 'READ'
             AND e.phase = 'ATTEMPT' AND e.actor_kind = 'SERVICE' AND e.actor_ref = session_user::text
             AND e.create_operation_id = rd.operation_id));
  PERFORM pg_temp.ok('U06 audit event with security source ETL',
    EXISTS (SELECT 1 FROM audit.security_event s WHERE s.event_type = 'ETL_SOURCE_SECRET_READ'
             AND s.entity_key = src::text AND s.source = 'ETL' AND s.details ->> 'operation_id' = op::text));
  PERFORM pg_temp.expect_fail('U07 finish of an unknown operation',
    format('SELECT etl.source_secret_read_finish(%L, true)', gen_random_uuid()));
  PERFORM etl.source_secret_read_finish(op, true);
  PERFORM etl.source_secret_read_finish(op, true);
  PERFORM pg_temp.ok('U08 OUTCOME SUCCEEDED once (repeat is a no-op)',
    (SELECT count(*) FROM secret_store.operation_event e WHERE e.operation_id = op AND e.phase = 'OUTCOME'
       AND e.code = 'SUCCEEDED') = 1);
  PERFORM pg_temp.expect_fail('U09 finished again with another outcome',
    format('SELECT etl.source_secret_read_finish(%L, false)', op));
  SELECT * INTO r2 FROM app.integration_source_secret_read_begin(tok, src);
  aop := r2.operation_id;
  PERFORM pg_temp.expect_fail('U10 an administrator READ cannot be finished by the ETL function',
    format('SELECT etl.source_secret_read_finish(%L, true)', aop));
  PERFORM app.integration_source_secret_read_finish(tok, src, aop, true);
  SELECT * INTO r FROM etl.source_secret_read_begin('I04_ETL_TEST');
  PERFORM etl.source_secret_read_finish(r.operation_id, false);
  PERFORM pg_temp.ok('U11 failed decrypt recorded as FAILED, credential stays ACTIVE',
    EXISTS (SELECT 1 FROM secret_store.operation_event e WHERE e.operation_id = r.operation_id AND e.phase = 'OUTCOME'
             AND e.code = 'FAILED')
    AND (SELECT state FROM secret_store.binding WHERE create_operation_id = rd.operation_id) = 'ACTIVE');
  SELECT row_version INTO v FROM meta.source_connection WHERE source_system_id = src;
  PERFORM app.integration_source_connection_set_status(tok, src, v, 'DISABLED');
  PERFORM pg_temp.expect_fail('U12 DISABLED connection blocks the ETL READ',
    'SELECT * FROM etl.source_secret_read_begin(''I04_ETL_TEST'')');
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
