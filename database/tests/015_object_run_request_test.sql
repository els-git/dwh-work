-- I08 test of 015: queue of object run requests (create, cancel, claim, start, finish, recovery of a lost
-- dispatcher) and the status view. One transaction, ROLLED BACK; session advisory locks released at the end.
-- For E0 (sandbox with fixtures) and E1 (restored copy), never the working dwh.
-- app.integration_admin_session_user() and app.integration_run_admin_session_user() are replaced inside the
-- transaction (as in 008/010-014). Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 015_object_run_request_test.sql
-- Expected last line: ALL_TESTS_PASS (labels X01-X14: 14 checks).
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
  vals text := format('%s, %L, %L, %L', p_system, 'i0815_test', p_name, p_active);
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
      ELSE quote_literal('i0815t_' || p_system || '_' || p_name) END;
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
  EXECUTE format($f$
    CREATE OR REPLACE FUNCTION app.integration_run_admin_session_user(p_token_hash text)
     RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'audit' AS $b$
    DECLARE v bigint;
    BEGIN
      v := CASE p_token_hash WHEN repeat('a', 64) THEN %s END;
      IF v IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
      PERFORM set_config('dwh.actor_user_id', v::text, true);
      PERFORM set_config('dwh.actor_username', 'i08-test', true);
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
  VALUES (p_code, 'I08 test source', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO v;
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
  bad text := repeat('c', 64);
  src bigint;
  o1 bigint;
  o2 bigint;
  rev integer;
  orev integer;
  rq bigint;
  j jsonb;
  run bigint;
  orun bigint;
  c record;
  v etl.v_object_load_status;
BEGIN
  src := pg_temp.source('I08_TEST_PG');
  o1 := pg_temp.make_object(src, 'requests', true);
  o2 := pg_temp.make_object(src, 'unpublished', true);
  CREATE TABLE stg.i0815_test_t (id text PRIMARY KEY, born text);
  CREATE TABLE ods.i0815_test_t (id text PRIMARY KEY, born text);
  rev := app.integration_object_config_save_draft(tok, o1, NULL, jsonb_build_object('target_schema', 'stg',
    'target_table', 'i0815_test_t', 'fields', jsonb_build_array(pg_temp.f('id'), pg_temp.f('born')), 'key', '["id"]'::jsonb));
  PERFORM app.integration_object_config_record_validation(tok, o1, rev, true, '[]');
  PERFORM app.integration_object_config_publish(tok, o1, rev);
  orev := app.integration_ods_config_save_draft(tok, o1, NULL, pg_temp.odoc('i0815_test_t', jsonb_build_array(
    pg_temp.o('id', 'COPY', 'id', 'TEXT'), pg_temp.o('born', 'COPY', 'born', 'TEXT')), '["id"]'));
  PERFORM app.integration_ods_config_record_validation(tok, o1, orev, true, '[]');
  PERFORM app.integration_ods_config_publish(tok, o1, orev);

  rq := app.integration_object_run_request_create(tok, o1, 'run');
  PERFORM pg_temp.ok('X01 RUN request created QUEUED by the session user',
    (SELECT status = 'QUEUED' AND run_kind = 'RUN' AND requested_by_username = 'i08-test'
       FROM etl.object_run_request WHERE request_id = rq));
  PERFORM pg_temp.expect_fail('X02 second active request of the same object rejected',
    format('SELECT app.integration_object_run_request_create(%L, %s, %L)', tok, o1, 'FULL_RERUN'));
  PERFORM pg_temp.expect_fail('X03 unknown run kind rejected',
    format('SELECT app.integration_object_run_request_create(%L, %s, %L)', tok, o2, 'DELETE_ALL'));
  PERFORM pg_temp.expect_fail('X04 unknown session rejected',
    format('SELECT app.integration_object_run_request_create(%L, %s, %L)', bad, o2, 'RUN'));
  PERFORM pg_temp.expect_fail('X05 object without a published configuration rejected',
    format('SELECT app.integration_object_run_request_create(%L, %s, %L)', tok, o2, 'RUN'));
  PERFORM app.integration_object_run_request_cancel(tok, rq);
  PERFORM pg_temp.ok('X06 QUEUED request cancelled',
    (SELECT status = 'CANCELLED' AND finished_at IS NOT NULL FROM etl.object_run_request WHERE request_id = rq));
  PERFORM pg_temp.expect_fail('X07 cancelled request cannot be cancelled again',
    format('SELECT app.integration_object_run_request_cancel(%L, %s)', tok, rq));
  PERFORM pg_temp.expect_fail('X08 CONTINUE_ODS rejected without a failed ODS step after a successful STG run',
    format('SELECT app.integration_object_run_request_create(%L, %s, %L)', tok, o1, 'CONTINUE_ODS'));

  -- Dispatcher: claim, start, finish with the runs of the request.
  rq := app.integration_object_run_request_create(tok, o1, 'FULL_RERUN');
  SELECT * INTO c FROM etl.object_run_request_claim('worker-1');
  PERFORM pg_temp.ok('X09 claim returns the oldest queued request and marks it CLAIMED',
    c.request_id = rq AND c.source_object_id = o1 AND c.run_kind = 'FULL_RERUN'
    AND (SELECT status = 'CLAIMED' AND claimed_by = 'worker-1' FROM etl.object_run_request WHERE request_id = rq)
    AND NOT EXISTS (SELECT 1 FROM etl.object_run_request_claim('worker-1')));
  PERFORM pg_temp.expect_fail('X10 start by another worker rejected',
    format('SELECT etl.object_run_request_start(%s, %L)', rq, 'worker-2'));
  PERFORM etl.object_run_request_start(rq, 'worker-1');
  j := etl.object_run_begin(o1);
  run := (j ->> 'run_id')::bigint;
  PERFORM etl.object_run_finish(run, true, 1, 1, 0, 0, NULL, NULL, NULL, NULL);
  j := etl.object_ods_run_begin(o1, run);
  orun := (j ->> 'ods_run_id')::bigint;
  PERFORM etl.object_ods_run_finish(orun, false, 1, 0, 0, 0, 'TYPE_CONVERSION', 'born: test', NULL, 'born', 'x', NULL);
  PERFORM pg_temp.expect_fail('X11 finish with a run of another object rejected',
    format('SELECT etl.object_run_request_finish(%s, true, %s, NULL, NULL, NULL)', rq,
           (SELECT max(run_id) + 1000 FROM etl.object_run)));
  PERFORM etl.object_run_request_finish(rq, false, run, orun, 'TYPE_CONVERSION', 'born: test');
  PERFORM pg_temp.ok('X12 finish: FAILED with the STG and ODS runs, lock released',
    (SELECT status = 'FAILED' AND stg_run_id = run AND ods_run_id = orun AND result_code = 'TYPE_CONVERSION'
       FROM etl.object_run_request WHERE request_id = rq)
    AND NOT EXISTS (SELECT 1 FROM pg_locks l WHERE l.locktype = 'advisory' AND l.pid = pg_backend_pid()
                      AND l.classid = hashtext('dwh.object_run_request')::oid AND l.objid = rq::oid));

  -- CONTINUE_ODS is now allowed; a claimed request of a lost dispatcher is recovered.
  rq := app.integration_object_run_request_create(tok, o1, 'CONTINUE_ODS');
  SELECT * INTO v FROM etl.v_object_load_status WHERE source_object_id = o1;
  PERFORM pg_temp.ok('X13 status view: versions, last STG SUCCESS, last ODS FAILED with message, continue allowed, active request',
    v.load_version_no = 1 AND v.ods_version_no = 1 AND NOT v.is_legacy AND v.stg_run_status = 'SUCCESS'
    AND v.ods_run_status = 'FAILED' AND v.ods_error_message LIKE 'born: test%' AND v.can_continue_ods
    AND v.has_active_request AND v.last_request_kind = 'CONTINUE_ODS');
  PERFORM etl.object_run_request_claim('worker-1');
  PERFORM pg_advisory_unlock(hashtext('dwh.object_run_request'), rq::integer);
  PERFORM etl.object_run_request_claim('worker-2');
  PERFORM pg_temp.ok('X14 request of a lost dispatcher (lock free) closed FAILED DISPATCHER_LOST',
    (SELECT status = 'FAILED' AND result_code = 'DISPATCHER_LOST' AND message LIKE '%previous worker=worker-1%'
       FROM etl.object_run_request WHERE request_id = rq));
  PERFORM pg_advisory_unlock_all();
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
