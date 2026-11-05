-- I06 test of 010: object run begin/finish, record history, locks, refusals, run history view.
-- Everything runs in one transaction and is ROLLED BACK; no row survives. Session advisory
-- locks are released explicitly at the end (ROLLBACK does not release them).
-- For E0 (sandbox with fixtures) and E1 (restored copy), never the working dwh.
-- As in the 005/008 tests, app.integration_admin_session_user() is replaced inside the
-- transaction (published configurations are created through the 008 functions).
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 010_object_run_test.sql
-- Expected last line: ALL_TESTS_PASS (labels V01-V17: 17 checks).
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

-- Test objects: meta.source_object has more columns in dwh than in the E0 fixture,
-- so the INSERT fills every NOT NULL column without a default by name or type.
CREATE FUNCTION pg_temp.make_object(p_system bigint, p_name text, p_active boolean) RETURNS bigint
LANGUAGE plpgsql AS $fn$
DECLARE
  cols text := 'source_system_id, source_schema, source_object, is_active';
  vals text := format('%s, %L, %L, %L', p_system, 'i06_test', p_name, p_active);
  a record;
  v bigint;
BEGIN
  FOR a IN SELECT att.attname, format_type(att.atttypid, att.atttypmod) AS typ
             FROM pg_attribute att
            WHERE att.attrelid = 'meta.source_object'::regclass AND att.attnum > 0 AND NOT att.attisdropped
              AND att.attnotnull AND att.attidentity = '' AND att.attgenerated = ''
              AND NOT EXISTS (SELECT 1 FROM pg_attrdef d WHERE d.adrelid = att.attrelid AND d.adnum = att.attnum)
              AND att.attname NOT IN ('source_object_id', 'source_system_id', 'source_schema', 'source_object', 'is_active')
  LOOP
    cols := cols || ', ' || quote_ident(a.attname);
    vals := vals || ', ' || CASE
      WHEN a.attname = 'object_type' THEN quote_literal('TABLE')
      WHEN a.attname = 'load_strategy' THEN quote_literal('FULL')
      WHEN a.typ IN ('integer', 'bigint', 'smallint', 'numeric') THEN '100'
      WHEN a.typ = 'boolean' THEN 'false'
      WHEN a.typ LIKE 'timestamp%' OR a.typ = 'date' THEN 'now()'
      WHEN a.typ = 'jsonb' OR a.typ = 'json' THEN quote_literal('{}')
      ELSE quote_literal('i06t_' || p_system || '_' || p_name) END;
  END LOOP;
  EXECUTE format('INSERT INTO meta.source_object (%s) VALUES (%s) RETURNING source_object_id', cols, vals) INTO v;
  RETURN v;
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

CREATE FUNCTION pg_temp.cfg(p_table text, p_fields jsonb, p_key jsonb) RETURNS jsonb
LANGUAGE sql AS $fn$ SELECT jsonb_build_object('target_schema', 'stg', 'target_table', p_table, 'fields', p_fields, 'key', p_key) $fn$;

CREATE FUNCTION pg_temp.fld(p_target text, p_source text, p_policy text DEFAULT 'AUTHORITATIVE', p_null boolean DEFAULT false)
RETURNS jsonb LANGUAGE sql AS $fn$
  SELECT jsonb_build_object('target_column', p_target, 'source_column', p_source, 'source_type', 'BIGINT',
    'target_type', 'BIGINT', 'conversion', 'NONE', 'write_policy', p_policy, 'allow_explicit_null', p_null) $fn$;

-- A source with a connection (status READY or DISABLED) and a published object over a stg table.
CREATE FUNCTION pg_temp.source(p_code text, p_status text) RETURNS bigint
LANGUAGE plpgsql AS $fn$
DECLARE v bigint;
BEGIN
  INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
  VALUES (p_code, 'I06 test source', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO v;
  INSERT INTO meta.source_connection(source_system_id, source_kind, connection_config, status, created_by, updated_by,
      last_test_at, last_test_ok, last_test_code, last_test_by)
  VALUES (v, 'DATABASE', '{"host": "db", "port": 5432, "database": "src", "username": "reader"}', p_status, 1, 1,
      now(), true, 'OK', 1);
  RETURN v;
END
$fn$;

CREATE FUNCTION pg_temp.publish(p_object bigint, p_table text) RETURNS bigint
LANGUAGE plpgsql AS $fn$
DECLARE
  tok text := repeat('a', 64);
  rev integer;
BEGIN
  EXECUTE format('CREATE TABLE stg.%I (id bigint PRIMARY KEY, amount bigint)', p_table);
  rev := app.integration_object_config_save_draft(tok, p_object, NULL,
    pg_temp.cfg(p_table, jsonb_build_array(pg_temp.fld('id', 'id'), pg_temp.fld('amount', 'amount')), '["id"]'));
  PERFORM app.integration_object_config_record_validation(tok, p_object, rev, true, '[]');
  PERFORM app.integration_object_config_publish(tok, p_object, rev);
  RETURN (SELECT version_id FROM meta.object_config_current WHERE source_object_id = p_object);
END
$fn$;

CREATE FUNCTION pg_temp.locked(p_object bigint) RETURNS boolean
LANGUAGE sql AS $fn$
  SELECT EXISTS (SELECT 1 FROM pg_locks l WHERE l.locktype = 'advisory' AND l.pid = pg_backend_pid()
                  AND l.classid::bigint = (hashtext('dwh.object_run')::bigint & 4294967295) AND l.objid::bigint = p_object
                  AND l.granted)
$fn$;

DO $t$
DECLARE
  tok text := repeat('a', 64);
  src bigint;
  src_off bigint;
  leg bigint;
  o1 bigint;
  o_off bigint;
  o_none bigint;
  oleg bigint;
  vid bigint;
  j jsonb;
  j2 jsonb;
  run bigint;
  st text;
BEGIN
  src := pg_temp.source('I06_TEST_PG', 'READY');
  src_off := pg_temp.source('I06_TEST_OFF', 'DISABLED');
  o1 := pg_temp.make_object(src, 'payments', true);
  o_none := pg_temp.make_object(src, 'unconfigured', true);
  o_off := pg_temp.make_object(src_off, 'payments_off', true);
  vid := pg_temp.publish(o1, 'i06_test_payments');
  PERFORM pg_temp.publish(o_off, 'i06_test_payments_off');
  SELECT s.source_system_id INTO leg FROM meta.source_system s WHERE meta.source_is_legacy(s.source_system_id)
   ORDER BY s.source_system_id LIMIT 1;
  IF leg IS NULL THEN
    INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
    VALUES ('I06_TEST_LEGACY', 'I06 legacy source', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO leg;
    EXECUTE 'INSERT INTO meta.column_contract(contract_id, source_system_code) VALUES (1, ''I06_TEST_LEGACY'')';
  END IF;
  SELECT source_object_id INTO oleg FROM meta.source_object WHERE source_system_id = leg AND is_active ORDER BY 1 LIMIT 1;
  IF oleg IS NULL THEN oleg := pg_temp.make_object(leg, 'legacy_table', true); END IF;

  -- Begin.
  j := etl.object_run_begin(o1);
  run := (j ->> 'run_id')::bigint;
  PERFORM pg_temp.ok('V01 begin: batch OBJECT_LOAD MANUAL FULL RUNNING, job OBJECT_LOAD_<id>, job_run RUNNING',
    (SELECT pipeline_code = 'OBJECT_LOAD' AND trigger_type = 'MANUAL' AND run_mode = 'FULL' AND status = 'RUNNING'
       FROM etl.batch_run WHERE batch_run_id = (j ->> 'batch_run_id')::bigint)
    AND (SELECT j2.code = 'OBJECT_LOAD_' || o1 AND j2.job_type = 'EXTRACT' AND j2.target_object = 'i06_test_payments'
           FROM etl.job j2 JOIN etl.job_run jr ON jr.job_id = j2.job_id
          WHERE jr.job_run_id = (j ->> 'job_run_id')::bigint AND jr.status = 'RUNNING'));
  PERFORM pg_temp.ok('V02 begin returns version in force, definition and connection settings without secrets',
    (j ->> 'version_id')::bigint = vid AND j #>> '{definition,target_table}' = 'i06_test_payments'
    AND jsonb_array_length(j #> '{definition,fields}') = 2 AND j #>> '{source,connection_config,host}' = 'db'
    AND (SELECT definition_hash FROM etl.object_run WHERE run_id = run)
        = (SELECT definition_hash FROM meta.object_config_version WHERE version_id = vid));
  PERFORM pg_temp.ok('V03 session advisory lock held for the object', pg_temp.locked(o1));
  INSERT INTO etl.object_record_change(run_id, change_kind, record_key, changes)
  VALUES (run, 'INSERT', '{"id": 1}', '{"amount": {"after": 10}}');
  PERFORM pg_temp.ok('V04 record history row for a running run',
    (SELECT count(*) FROM etl.object_record_change WHERE run_id = run) = 1);

  -- Finish (success).
  st := etl.object_run_finish(run, true, 2, 1, 0, 1, NULL, NULL, NULL, NULL);
  PERFORM pg_temp.ok('V05 finish SUCCESS: run, job_run and batch closed with counts; lock released',
    st = 'SUCCESS' AND NOT pg_temp.locked(o1)
    AND (SELECT status = 'SUCCESS' AND rows_read = 2 AND rows_inserted = 1 AND rows_unchanged = 1 AND finished_at IS NOT NULL
           FROM etl.object_run WHERE run_id = run)
    AND (SELECT status = 'SUCCESS' AND rows_read = 2 AND rows_inserted = 1 AND error_count = 0
           FROM etl.job_run WHERE job_run_id = (j ->> 'job_run_id')::bigint)
    AND (SELECT status = 'SUCCESS' AND finished_at IS NOT NULL FROM etl.batch_run WHERE batch_run_id = (j ->> 'batch_run_id')::bigint));
  PERFORM pg_temp.expect_fail('V06 finish twice',
    format('SELECT etl.object_run_finish(%s, true, 0, 0, 0, 0, NULL, NULL, NULL, NULL)', run));
  PERFORM pg_temp.expect_fail('V07 record history for a finished run',
    format('INSERT INTO etl.object_record_change(run_id, change_kind, record_key, changes) VALUES (%s, %L, %L, %L)',
           run, 'INSERT', '{"id": 2}', '{}'));
  PERFORM pg_temp.expect_fail('V08 record history cannot be updated',
    format('UPDATE etl.object_record_change SET changes = %L WHERE run_id = %s', '{}', run));
  PERFORM pg_temp.expect_fail('V09 record history cannot be deleted',
    format('DELETE FROM etl.object_record_change WHERE run_id = %s', run));

  -- Failure with an error code.
  j := etl.object_run_begin(o1);
  PERFORM pg_temp.expect_fail('V10 invalid error code',
    format('SELECT etl.object_run_finish(%s, false, 0, 0, 0, 0, %L, NULL, NULL, NULL)', j ->> 'run_id', 'bad code'));
  st := etl.object_run_finish((j ->> 'run_id')::bigint, false, 5, 0, 0, 0, 'TYPE_CONVERSION',
          'Source value cannot be converted to the target type', '{"id": 7}', 'amount');
  PERFORM pg_temp.ok('V11 finish FAILED: error_log row OBJECT_LOAD with code, key, column and object',
    st = 'FAILED' AND NOT pg_temp.locked(o1)
    AND (SELECT count(*) FROM etl.error_log e WHERE e.batch_run_id = (j ->> 'batch_run_id')::bigint
           AND e.error_stage = 'OBJECT_LOAD' AND e.error_code = 'TYPE_CONVERSION' AND e.column_name = 'amount'
           AND e.record_key = '{"id": 7}' AND e.source_object_id = o1) = 1
    AND (SELECT status FROM etl.batch_run WHERE batch_run_id = (j ->> 'batch_run_id')::bigint) = 'FAILED');

  -- A run left RUNNING by a dead session is closed as ABANDONED by the next begin.
  j := etl.object_run_begin(o1);
  j2 := etl.object_run_begin(o1);
  PERFORM pg_temp.ok('V12 previous RUNNING run closed as ABANDONED, new run RUNNING',
    (SELECT status = 'FAILED' AND error_code = 'ABANDONED' FROM etl.object_run WHERE run_id = (j ->> 'run_id')::bigint)
    AND (SELECT status FROM etl.object_run WHERE run_id = (j2 ->> 'run_id')::bigint) = 'RUNNING');
  PERFORM etl.object_run_finish((j2 ->> 'run_id')::bigint, true, 0, 0, 0, 0, NULL, NULL, NULL, NULL);
  PERFORM pg_advisory_unlock_all();
  PERFORM pg_temp.ok('V13 run history view lists OBJECT_LOAD batches of the object',
    (SELECT count(*) FROM etl.v_integration_run_history h JOIN etl.batch_run b USING (batch_run_id)
          WHERE h.pipeline_code = 'OBJECT_LOAD' AND b.batch_code = 'OBJECT_LOAD_' || o1) = 4);

  -- Refusals (no run row is created).
  PERFORM pg_temp.expect_fail('V14 object without a published configuration',
    format('SELECT etl.object_run_begin(%s)', o_none));
  PERFORM pg_temp.expect_fail('V15 source connection disabled', format('SELECT etl.object_run_begin(%s)', o_off));
  PERFORM pg_temp.expect_fail('V16 legacy CRM source', format('SELECT etl.object_run_begin(%s)', oleg));
  PERFORM app.integration_object_config_set_drift(tok, o1, vid, true, '{"changes": []}');
  PERFORM pg_temp.expect_fail('V17 configuration in DRIFT', format('SELECT etl.object_run_begin(%s)', o1));
  PERFORM pg_advisory_unlock_all();
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
