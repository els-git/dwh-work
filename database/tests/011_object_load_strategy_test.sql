-- I07 test of 011: load strategy in the version, publish checks, watermark of runs, DELETE history.
-- One transaction, ROLLED BACK; session advisory locks released explicitly at the end.
-- For E0 (sandbox with fixtures) and E1 (restored copy), never the working dwh.
-- app.integration_admin_session_user() is replaced inside the transaction (as in 008/010).
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 011_object_load_strategy_test.sql
-- Expected last line: ALL_TESTS_PASS (labels V01-V13: 13 checks).
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
  vals text := format('%s, %L, %L, %L', p_system, 'i07_test', p_name, p_active);
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
      ELSE quote_literal('i07t_' || p_system || '_' || p_name) END;
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
  VALUES (p_code, 'I07 test source', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO v;
  INSERT INTO meta.source_connection(source_system_id, source_kind, connection_config, status, created_by, updated_by,
      last_test_at, last_test_ok, last_test_code, last_test_by)
  VALUES (v, 'DATABASE', '{"host": "db", "port": 5432, "database": "src", "username": "reader"}', p_status, 1, 1,
      now(), true, 'OK', 1);
  RETURN v;
END
$fn$;

CREATE FUNCTION pg_temp.f(p_col text, p_type text) RETURNS jsonb
LANGUAGE sql AS $fn$
  SELECT jsonb_build_object('target_column', p_col, 'source_column', p_col, 'source_type', p_type,
    'target_type', p_type, 'conversion', 'NONE', 'write_policy', 'AUTHORITATIVE', 'allow_explicit_null', false) $fn$;

CREATE FUNCTION pg_temp.doc(p_strategy jsonb) RETURNS jsonb
LANGUAGE sql AS $fn$
  SELECT jsonb_build_object('target_schema', 'stg', 'target_table', 'i07_test_t',
    'fields', jsonb_build_array(pg_temp.f('id', 'BIGINT'), pg_temp.f('note', 'TEXT'),
                                pg_temp.f('changed_at', 'TIMESTAMP'), pg_temp.f('is_deleted', 'BOOLEAN')),
    'key', '["id"]'::jsonb) || p_strategy $fn$;

DO $t$
DECLARE
  tok text := repeat('a', 64);
  src bigint;
  o1 bigint;
  rev integer;
  vid bigint;
  j jsonb;
  run bigint;
BEGIN
  src := pg_temp.source('I07_TEST_PG', 'READY');
  o1 := pg_temp.make_object(src, 'events', true);
  CREATE TABLE stg.i07_test_t (id bigint PRIMARY KEY, note text, changed_at timestamp, is_deleted boolean);

  rev := app.integration_object_config_save_draft(tok, o1, NULL,
    pg_temp.doc('{"load_strategy": "INCREMENTAL", "overlap_minutes": 5, "delete_flag_column": "is_deleted"}'));
  PERFORM app.integration_object_config_record_validation(tok, o1, rev, true, '[]');
  PERFORM pg_temp.expect_fail('V01 publish INCREMENTAL without change column',
    format('SELECT app.integration_object_config_publish(%L, %s, %s)', tok, o1, rev));
  rev := app.integration_object_config_save_draft(tok, o1, rev,
    pg_temp.doc('{"load_strategy": "INCREMENTAL", "change_column": "note", "overlap_minutes": 5}'));
  PERFORM app.integration_object_config_record_validation(tok, o1, rev, true, '[]');
  PERFORM pg_temp.expect_fail('V02 publish with a text change column',
    format('SELECT app.integration_object_config_publish(%L, %s, %s)', tok, o1, rev));
  rev := app.integration_object_config_save_draft(tok, o1, rev,
    pg_temp.doc('{"load_strategy": "INCREMENTAL", "change_column": "changed_at", "delete_flag_column": "note"}'));
  PERFORM app.integration_object_config_record_validation(tok, o1, rev, true, '[]');
  PERFORM pg_temp.expect_fail('V03 publish with a non-boolean delete flag',
    format('SELECT app.integration_object_config_publish(%L, %s, %s)', tok, o1, rev));
  PERFORM pg_temp.expect_fail('V04 unknown strategy rejected',
    format('SELECT app.integration_object_config_save_draft(%L, %s, %s, %L)', tok, o1, rev,
           pg_temp.doc('{"load_strategy": "DELTA"}')));
  rev := app.integration_object_config_save_draft(tok, o1, rev,
    pg_temp.doc('{"load_strategy": "INCREMENTAL", "change_column": "changed_at", "overlap_minutes": 5, "delete_flag_column": "is_deleted"}'));
  SELECT version_id INTO vid FROM meta.object_config_version WHERE source_object_id = o1 AND status = 'DRAFT';
  PERFORM pg_temp.ok('V05 draft definition contains the strategy',
    meta.object_config_definition(vid) -> 'strategy' = '{"load_strategy": "INCREMENTAL", "change_column": "changed_at", "overlap_minutes": 5, "delete_flag_column": "is_deleted"}'::jsonb);
  PERFORM app.integration_object_config_record_validation(tok, o1, rev, true, '[]');
  PERFORM app.integration_object_config_publish(tok, o1, rev);
  PERFORM pg_temp.ok('V06 INCREMENTAL version published, READY',
    (SELECT status FROM meta.object_config_current WHERE source_object_id = o1) = 'READY'
    AND (SELECT load_strategy FROM meta.object_config_version WHERE version_id = vid) = 'INCREMENTAL');

  j := etl.object_run_begin(o1);
  run := (j ->> 'run_id')::bigint;
  PERFORM pg_temp.ok('V07 first run: no watermark yet, strategy in the definition',
    j -> 'watermark' = 'null'::jsonb AND j #>> '{definition,strategy,load_strategy}' = 'INCREMENTAL');
  PERFORM etl.object_run_record_increment(run, NULL, '2026-11-05 10:00:00', 1, 1);
  INSERT INTO etl.object_record_change(run_id, change_kind, record_key, changes)
  VALUES (run, 'DELETE', '{"id": 1}', '{"is_deleted": {"before": false, "after": true}}');
  PERFORM pg_temp.ok('V08 increment results and a DELETE history row stored for the running run',
    (SELECT watermark_to = '2026-11-05 10:00:00' AND rows_stale = 1 AND rows_deleted = 1 FROM etl.object_run WHERE run_id = run)
    AND (SELECT count(*) FROM etl.object_record_change WHERE run_id = run AND change_kind = 'DELETE') = 1);
  PERFORM etl.object_run_finish(run, true, 3, 1, 1, 1, NULL, NULL, NULL, NULL);
  PERFORM pg_temp.expect_fail('V09 increment results for a finished run',
    format('SELECT etl.object_run_record_increment(%s, NULL, %L, 0, 0)', run, 'x'));
  j := etl.object_run_begin(o1);
  PERFORM pg_temp.ok('V10 next run gets the watermark of the last successful run',
    j ->> 'watermark' = '2026-11-05 10:00:00');
  PERFORM pg_temp.expect_fail('V11 negative counters rejected',
    format('SELECT etl.object_run_record_increment(%s, NULL, NULL, -1, 0)', j ->> 'run_id'));
  PERFORM etl.object_run_finish((j ->> 'run_id')::bigint, false, 0, 0, 0, 0, 'SOURCE_ERROR', 'test', NULL, NULL);
  j := etl.object_run_begin(o1);
  PERFORM pg_temp.ok('V12 a failed run does not move the watermark',
    j ->> 'watermark' = '2026-11-05 10:00:00');
  PERFORM etl.object_run_finish((j ->> 'run_id')::bigint, true, 0, 0, 0, 0, NULL, NULL, NULL, NULL);
  PERFORM pg_temp.ok('V13 versions without a strategy read as FULL, overlap 10',
    NOT EXISTS (SELECT 1 FROM meta.object_config_version v WHERE v.version_id <> vid
                 AND (meta.object_config_definition(v.version_id) #>> '{strategy,load_strategy}' <> 'FULL'
                      OR (meta.object_config_definition(v.version_id) #>> '{strategy,overlap_minutes}')::int <> 10)));
  PERFORM pg_advisory_unlock_all();
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
