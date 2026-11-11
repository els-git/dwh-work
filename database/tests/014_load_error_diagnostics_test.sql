-- I13-08 test of 014: run-finish variants with diagnostics (bad_value, diagnostic_context).
-- One transaction, ROLLED BACK; session advisory locks released explicitly at the end.
-- For E0 (sandbox with fixtures) and E1 (restored copy), never the working dwh.
-- app.integration_admin_session_user() is replaced inside the transaction (as in 008/010/011/012).
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 014_load_error_diagnostics_test.sql
-- Expected last line: ALL_TESTS_PASS (labels W01-W10: 10 checks).
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
  vals text := format('%s, %L, %L, %L', p_system, 'i1308_test', p_name, p_active);
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
      ELSE quote_literal('i1308t_' || p_system || '_' || p_name) END;
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

-- A source with a READY connection.
CREATE FUNCTION pg_temp.source(p_code text) RETURNS bigint
LANGUAGE plpgsql AS $fn$
DECLARE v bigint;
BEGIN
  INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
  VALUES (p_code, 'I13-08 test source', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO v;
  INSERT INTO meta.source_connection(source_system_id, source_kind, connection_config, status, created_by, updated_by,
      last_test_at, last_test_ok, last_test_code, last_test_by)
  VALUES (v, 'DATABASE', '{"host": "db", "port": 5432, "database": "src", "username": "reader"}', 'READY', 1, 1,
      now(), true, 'OK', 1);
  RETURN v;
END
$fn$;

CREATE FUNCTION pg_temp.f(p_col text) RETURNS jsonb
LANGUAGE sql AS $fn$
  SELECT jsonb_build_object('target_column', p_col, 'source_column', p_col, 'source_type', 'TEXT',
    'target_type', 'TEXT', 'conversion', 'NONE', 'write_policy', 'AUTHORITATIVE', 'allow_explicit_null', false) $fn$;

-- ODS field: transformation, STG column (NULL for CONSTANT/LOAD_RUN_ID/LOADED_AT), target type, value, policy.
CREATE FUNCTION pg_temp.o(p_target text, p_transform text, p_stg text, p_type text, p_param text DEFAULT NULL,
  p_policy text DEFAULT 'AUTHORITATIVE') RETURNS jsonb
LANGUAGE sql AS $fn$
  SELECT jsonb_build_object('target_column', p_target, 'transform', p_transform, 'stg_column', p_stg,
    'stg_type', CASE WHEN p_stg IS NULL THEN NULL ELSE 'TEXT' END, 'target_type', p_type, 'conversion', 'NONE',
    'param', p_param, 'write_policy', p_policy, 'allow_explicit_null', false) $fn$;

CREATE FUNCTION pg_temp.odoc(p_table text, p_fields jsonb, p_key jsonb) RETURNS jsonb
LANGUAGE sql AS $fn$ SELECT jsonb_build_object('target_table', p_table, 'fields', p_fields, 'key', p_key) $fn$;

CREATE FUNCTION pg_temp.good() RETURNS jsonb
LANGUAGE sql AS $fn$
  SELECT jsonb_build_array(pg_temp.o('id', 'COPY', 'id', 'TEXT'), pg_temp.o('pin', 'TRIM', 'pin', 'TEXT'),
    pg_temp.o('name', 'EMPTY_TO_NULL', 'name', 'TEXT'),
    pg_temp.o('birth', 'DATE_FROM_TEXT', 'birth', 'DATE', 'YYYY-MM-DD'),
    pg_temp.o('channel', 'DEFAULT', 'channel', 'TEXT', 'WEB'), pg_temp.o('source_code', 'CONSTANT', NULL, 'TEXT', 'OC'),
    pg_temp.o('loaded_at', 'LOADED_AT', NULL, 'TIMESTAMPTZ', NULL, 'INSERT_ONLY'),
    pg_temp.o('load_run_id', 'LOAD_RUN_ID', NULL, 'BIGINT', NULL, 'INSERT_ONLY')) $fn$;

DO $t$
DECLARE
  tok text := repeat('a', 64);
  src bigint;
  o1 bigint;
  rev integer;
  orev integer;
  j jsonb;
  run bigint;
  orun bigint;
  diag jsonb := '{"kind": "TYPE_CONVERSION", "stage": "STG", "total_rows": 65, "fields": [{"column": "birth",
    "rule": "date", "invalid_rows": 47, "values": [{"value": "99.99.9999", "count": 43}, {"value": "birthD", "count": 4}],
    "keys": [{"id": "3"}, {"id": "5"}]}]}';
  e etl.error_log;
BEGIN
  src := pg_temp.source('I1308_TEST_PG');
  o1 := pg_temp.make_object(src, 'requests', true);
  CREATE TABLE stg.i1308_test_t (id text PRIMARY KEY, birth text);
  CREATE TABLE ods.i1308_test_t (id text PRIMARY KEY, birth date);
  rev := app.integration_object_config_save_draft(tok, o1, NULL, jsonb_build_object('target_schema', 'stg',
    'target_table', 'i1308_test_t', 'fields', jsonb_build_array(pg_temp.f('id'), pg_temp.f('birth')),
    'key', '["id"]'::jsonb));
  PERFORM app.integration_object_config_record_validation(tok, o1, rev, true, '[]');
  PERFORM app.integration_object_config_publish(tok, o1, rev);
  orev := app.integration_ods_config_save_draft(tok, o1, NULL, pg_temp.odoc('i1308_test_t', jsonb_build_array(
    pg_temp.o('id', 'COPY', 'id', 'TEXT'), pg_temp.o('birth', 'DATE_FROM_TEXT', 'birth', 'DATE', 'DD.MM.YYYY')), '["id"]'));
  PERFORM app.integration_ods_config_record_validation(tok, o1, orev, true, '[]');
  PERFORM app.integration_ods_config_publish(tok, o1, orev);

  -- STG step: failed run with diagnostics.
  j := etl.object_run_begin(o1);
  run := (j ->> 'run_id')::bigint;
  PERFORM pg_temp.expect_fail('W01 diagnostics on a successful run rejected',
    format('SELECT etl.object_run_finish(%s, true, 1, 1, 0, 0, NULL, NULL, NULL, NULL, %L, NULL)', run, 'x'));
  PERFORM pg_temp.expect_fail('W02 diagnostics that are not a JSON object rejected',
    format('SELECT etl.object_run_finish(%s, false, 0, 0, 0, 0, %L, %L, NULL, NULL, NULL, %L)',
           run, 'TYPE_CONVERSION', 'm', '[1]'));
  PERFORM pg_temp.expect_fail('W03 diagnostics larger than 64 KB rejected',
    format('SELECT etl.object_run_finish(%s, false, 0, 0, 0, 0, %L, %L, NULL, NULL, NULL, %L)',
           run, 'TYPE_CONVERSION', 'm', jsonb_build_object('x', repeat('y', 70000))));
  PERFORM pg_temp.ok('W04 rejected calls left the run RUNNING',
    (SELECT status = 'RUNNING' FROM etl.object_run WHERE run_id = run));
  PERFORM etl.object_run_finish(run, false, 65, 0, 0, 0, 'TYPE_CONVERSION',
    'birth (date): 47 of 65 rows invalid', '{"id": "3"}', 'birth', '99.99.9999', diag);
  SELECT * INTO e FROM etl.error_log x WHERE x.batch_run_id = (j ->> 'batch_run_id')::bigint;
  PERFORM pg_temp.ok('W05 STG: one error row with message, column, key, bad_value and diagnostic_context',
    (SELECT count(*) FROM etl.error_log x WHERE x.batch_run_id = (j ->> 'batch_run_id')::bigint) = 1
    AND e.error_stage = 'OBJECT_LOAD' AND e.error_code = 'TYPE_CONVERSION' AND e.column_name = 'birth'
    AND e.record_key = '{"id": "3"}' AND e.bad_value = '99.99.9999' AND e.diagnostic_context = diag
    AND e.error_message LIKE 'birth (date): 47 of 65%'
    AND (SELECT status = 'FAILED' FROM etl.object_run WHERE run_id = run)
    AND EXISTS (SELECT 1 FROM etl.v_integration_error v WHERE v.error_id = e.error_id AND v.bad_value = '99.99.9999'
                  AND v.diagnostic_context -> 'fields' -> 0 ->> 'column' = 'birth'));
  PERFORM pg_temp.expect_fail('W06 finished run cannot be finished again through the new variant',
    format('SELECT etl.object_run_finish(%s, false, 0, 0, 0, 0, %L, %L, NULL, NULL, %L, NULL)',
           run, 'TYPE_CONVERSION', 'm', 'x'));

  -- The 10-parameter variant keeps working (engine before step 5).
  j := etl.object_run_begin(o1);
  PERFORM etl.object_run_finish((j ->> 'run_id')::bigint, false, 0, 0, 0, 0, 'SOURCE_ERROR', 'old call', NULL, NULL);
  PERFORM pg_temp.ok('W07 10-parameter variant unchanged: error row without bad_value and diagnostics',
    EXISTS (SELECT 1 FROM etl.error_log x WHERE x.batch_run_id = (j ->> 'batch_run_id')::bigint
              AND x.error_code = 'SOURCE_ERROR' AND x.bad_value IS NULL AND x.diagnostic_context IS NULL));

  -- Successful STG run, then a failed ODS step with diagnostics; long value cut to 500 characters.
  j := etl.object_run_begin(o1);
  run := (j ->> 'run_id')::bigint;
  PERFORM etl.object_run_finish(run, true, 1, 1, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL);
  PERFORM pg_temp.ok('W08 successful run through the new variant: SUCCESS, no error row',
    (SELECT status = 'SUCCESS' FROM etl.object_run WHERE run_id = run)
    AND NOT EXISTS (SELECT 1 FROM etl.error_log x WHERE x.batch_run_id = (j ->> 'batch_run_id')::bigint));
  j := etl.object_ods_run_begin(o1, run);
  orun := (j ->> 'ods_run_id')::bigint;
  PERFORM etl.object_ods_run_finish(orun, false, 65, 0, 0, 0, 'TYPE_CONVERSION', 'birth: 47 of 65 rows invalid',
    '{"id": "5"}', 'birth', repeat('z', 600), jsonb_set(diag, '{stage}', '"ODS"'));
  SELECT * INTO e FROM etl.error_log x WHERE x.batch_run_id = (j ->> 'batch_run_id')::bigint;
  PERFORM pg_temp.ok('W09 ODS: error row ODS_LOAD with bad_value (cut to 500) and diagnostic_context; run FAILED',
    e.error_stage = 'ODS_LOAD' AND e.column_name = 'birth' AND e.bad_value = repeat('z', 500)
    AND e.diagnostic_context ->> 'stage' = 'ODS'
    AND (SELECT status = 'FAILED' FROM etl.object_ods_run WHERE ods_run_id = orun));
  j := etl.object_ods_run_begin(o1, NULL);
  PERFORM etl.object_ods_run_finish((j ->> 'ods_run_id')::bigint, false, 0, 0, 0, 0, 'TYPE_CONVERSION', 'no details',
    NULL, NULL, NULL, NULL);
  PERFORM pg_temp.ok('W10 ODS failure without details through the new variant: error row, bad_value empty',
    EXISTS (SELECT 1 FROM etl.error_log x WHERE x.batch_run_id = (j ->> 'batch_run_id')::bigint
              AND x.error_stage = 'ODS_LOAD' AND x.bad_value IS NULL AND x.diagnostic_context IS NULL));
  PERFORM pg_advisory_unlock_all();
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
