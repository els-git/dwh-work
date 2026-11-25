-- I12 test of 035 (E0 sandbox, E1 copy), run after the package as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 035_file_warnings_test.sql
-- One transaction, ROLLED BACK. app.integration_admin_session_user() is replaced inside it (token 'a'x64 = an
-- active admin). Errors are compared by SQLSTATE. Expected last line: ALL_TESTS_PASS (W01-W06).
BEGIN;

CREATE FUNCTION pg_temp.ok(label text, cond boolean) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  IF cond IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', label; END IF;
  RAISE NOTICE 'PASS %', label;
END
$fn$;

CREATE FUNCTION pg_temp.err(p_sql text) RETURNS text
LANGUAGE plpgsql AS $fn$
BEGIN
  EXECUTE p_sql;
  RETURN 'NO ERROR';
EXCEPTION WHEN OTHERS THEN
  RETURN SQLSTATE;
END
$fn$;

CREATE FUNCTION pg_temp.make_row(p_table regclass, p_key text, p_values jsonb) RETURNS bigint
LANGUAGE plpgsql AS $fn$
DECLARE cols text := ''; vals text := ''; a record; k text; v bigint;
BEGIN
  FOR k IN SELECT jsonb_object_keys(p_values) LOOP
    cols := cols || CASE WHEN cols = '' THEN '' ELSE ', ' END || quote_ident(k);
    vals := vals || CASE WHEN vals = '' THEN '' ELSE ', ' END || coalesce(quote_literal(p_values ->> k), 'NULL');
  END LOOP;
  FOR a IN SELECT att.attname, format_type(att.atttypid, att.atttypmod) AS typ
             FROM pg_attribute att
            WHERE att.attrelid = p_table AND att.attnum > 0 AND NOT att.attisdropped
              AND att.attnotnull AND att.attidentity = '' AND att.attgenerated = ''
              AND NOT EXISTS (SELECT 1 FROM pg_attrdef d WHERE d.adrelid = att.attrelid AND d.adnum = att.attnum)
              AND NOT p_values ? att.attname::text
  LOOP
    cols := cols || ', ' || quote_ident(a.attname);
    vals := vals || ', ' || CASE
      WHEN a.attname = 'object_type' THEN quote_literal('TABLE')
      WHEN a.attname = 'load_strategy' THEN quote_literal('FULL')
      WHEN a.typ IN ('integer', 'bigint', 'smallint', 'numeric') THEN '1'
      WHEN a.typ = 'boolean' THEN 'false'
      WHEN a.typ LIKE 'timestamp%' OR a.typ = 'date' THEN 'now()'
      WHEN a.typ IN ('jsonb', 'json') THEN quote_literal('{}')
      ELSE quote_literal('i1234')
    END;
  END LOOP;
  EXECUTE format('INSERT INTO %s (%s) VALUES (%s) RETURNING %I', p_table, cols, vals, p_key) INTO v;
  RETURN v;
END
$fn$;

DO $setup$
DECLARE
  v_admin bigint := (SELECT user_id FROM app.user_account
                      WHERE is_active AND account_level IN ('SUPERADMIN', 'ADMIN') ORDER BY user_id LIMIT 1);
  v_file bigint;
  v_db bigint;
  v_tab bigint;
BEGIN
  IF v_admin IS NULL THEN RAISE EXCEPTION 'FAIL setup: no active admin user'; END IF;
  EXECUTE format($f$
    CREATE OR REPLACE FUNCTION app.integration_admin_session_user(p_token_hash text)
     RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'audit' AS $b$
    DECLARE v bigint;
    BEGIN
      v := CASE p_token_hash WHEN repeat('a', 64) THEN %s END;
      IF v IS NULL THEN RAISE EXCEPTION 'Integration administration access denied'; END IF;
      PERFORM set_config('dwh.actor_user_id', v::text, true);
      PERFORM set_config('dwh.actor_username', 'i12-035-test', true);
      RETURN v;
    END; $b$ $f$, v_admin);
  v_file := pg_temp.make_row('meta.source_system', 'source_system_id',
            jsonb_build_object('code', 'I1235_FILES', 'name', 'I12 test file source', 'is_active', true,
              'source_type', 'FILE'));
  v_db := pg_temp.make_row('meta.source_system', 'source_system_id',
            jsonb_build_object('code', 'I1235_DB', 'name', 'I12 test DB source', 'is_active', true,
              'source_type', 'POSTGRESQL'));
  v_tab := pg_temp.make_row('meta.source_object', 'source_object_id',
            jsonb_build_object('source_system_id', v_db, 'source_schema', 'public', 'source_object', 'i1235_t',
              'object_type', 'TABLE', 'is_active', true));
  PERFORM set_config('t035.file', v_file::text, true);
  PERFORM set_config('t035.db', v_db::text, true);
  PERFORM set_config('t035.tab', v_tab::text, true);
  PERFORM set_config('t035.admin', v_admin::text, true);
END
$setup$;

-- setup 2: published file object and a running run of it
DO $run$
DECLARE
  tk text := repeat('a', 64);
  o bigint;
  v bigint;
  r jsonb;
BEGIN
  o := app.integration_file_object_create(tk, current_setting('t035.file')::bigint, 'services_w', 'test');
  PERFORM app.integration_object_config_save_draft(tk, o, NULL, '{"fields": [], "key": []}'::jsonb);
  v := app.integration_file_config_save(tk, o, '{"mask": "svc_*.json", "format": "JSON", "records_path": "items",
         "date_path": "meta.at", "mode": "ALL_NEW", "time_zone": "Asia/Baku"}'::jsonb);
  CREATE TABLE stg.i1235_services (id bigint PRIMARY KEY);
  UPDATE meta.object_config_version
     SET status = 'PUBLISHED', target_schema = 'stg', target_table = 'i1235_services', definition_hash = repeat('c', 64),
         published_at = clock_timestamp(), published_by = current_setting('t035.admin')::bigint
   WHERE version_id = v;
  INSERT INTO meta.object_config_current (source_object_id, version_id, status, updated_by)
  VALUES (o, v, 'READY', current_setting('t035.admin')::bigint);
  r := etl.object_run_begin(o);
  PERFORM set_config('t035.obj', o::text, true);
  PERFORM set_config('t035.run', r ->> 'run_id', true);
END
$run$;

-- W01 a refused file is written to the error journal as a warning of the run
DO $t$
DECLARE
  run bigint := current_setting('t035.run')::bigint;
  o bigint := current_setting('t035.obj')::bigint;
  e etl.error_log;
  ok boolean;
BEGIN
  ok := etl.object_file_warning(run, 'svc_bad.json', repeat('b', 64), 'FILE_BROKEN', 'not JSON');
  SELECT * INTO e FROM etl.error_log WHERE source_object_id = o AND error_stage = 'FILE_CHECK';
  PERFORM pg_temp.ok('W01 warning in etl.error_log: stage FILE_CHECK, code, file, severity WARNING, ids of the run',
    ok AND e.error_code = 'FILE_BROKEN' AND e.error_message = 'svc_bad.json: not JSON'
    AND e.diagnostic_context ->> 'severity' = 'WARNING' AND e.diagnostic_context ->> 'file' = 'svc_bad.json'
    AND e.diagnostic_context ->> 'sha256' = repeat('b', 64)
    AND e.batch_run_id = (SELECT batch_run_id FROM etl.object_run WHERE run_id = run)
    AND e.job_run_id = (SELECT job_run_id FROM etl.object_run WHERE run_id = run) AND e.job_id IS NOT NULL);
END
$t$;

-- W02 the same content and code is reported once; another code of the same content is a new warning
DO $t$
DECLARE
  run bigint := current_setting('t035.run')::bigint;
  o bigint := current_setting('t035.obj')::bigint;
  again boolean;
  other boolean;
BEGIN
  again := etl.object_file_warning(run, 'svc_bad_copy.json', repeat('b', 64), 'FILE_BROKEN', 'not JSON');
  other := etl.object_file_warning(run, 'svc_bad.json', repeat('b', 64), 'FILE_TOO_LARGE', 'too large');
  PERFORM pg_temp.ok('W02 repeated warning skipped, other code written',
    NOT again AND other
    AND (SELECT count(*) FROM etl.error_log WHERE source_object_id = o AND error_stage = 'FILE_CHECK') = 2);
END
$t$;

-- W03 bad code, hash or name refused
DO $t$
DECLARE
  run bigint := current_setting('t035.run')::bigint;
BEGIN
  PERFORM pg_temp.ok('W03 bad code / hash / name refused',
    pg_temp.err(format('SELECT etl.object_file_warning(%s, %L, %L, %L, NULL)', run, 'a.json', repeat('b', 64), 'BROKEN')) = 'P0001'
    AND pg_temp.err(format('SELECT etl.object_file_warning(%s, %L, %L, %L, NULL)', run, 'a.json', 'XYZ', 'FILE_BROKEN')) = 'P0001'
    AND pg_temp.err(format('SELECT etl.object_file_warning(%s, %L, %L, %L, NULL)', run, '../a.json', repeat('b', 64), 'FILE_BROKEN')) = 'P0001'
    AND pg_temp.err(format('SELECT etl.object_file_warning(%s, %L, %L, %L, NULL)', run, '', repeat('b', 64), 'FILE_BROKEN')) = 'P0001');
END
$t$;

-- W04 load status: file_errors counts the files refused by the last run; the STG error message ignores warnings
DO $t$
DECLARE
  run bigint := current_setting('t035.run')::bigint;
  o bigint := current_setting('t035.obj')::bigint;
  s etl.v_object_load_status;
BEGIN
  PERFORM etl.object_file_record(run, 'svc_bad.json', 10, repeat('b', 64), NULL, 'FAILED', NULL, 'FILE_BROKEN: not JSON');
  PERFORM etl.object_file_record(run, 'svc_nodate.json', 10, repeat('d', 64), NULL, 'NO_DATE', NULL, 'no date');
  PERFORM etl.object_file_record(run, 'svc_1.json', 10, repeat('1', 64), '2026-11-20 10:00+04', 'LOADED', 5, NULL);
  PERFORM etl.object_file_record(run, 'svc_0.json', 10, repeat('0', 64), '2026-11-19 10:00+04', 'NOT_ACTUAL', NULL, 'old');
  SELECT * INTO s FROM etl.v_object_load_status WHERE source_object_id = o;
  PERFORM pg_temp.ok('W04 file_errors = 2 (FAILED + NO_DATE of the last run); stg_error_message without warnings',
    s.file_errors = 2 AND s.stg_run_id = run AND s.stg_error_message IS NULL);
END
$t$;

-- W05 a run that is not running (finished, unknown, of a table object) cannot write warnings
DO $t$
DECLARE
  run bigint := current_setting('t035.run')::bigint;
BEGIN
  PERFORM pg_temp.ok('W05a unknown run refused',
    pg_temp.err(format('SELECT etl.object_file_warning(-1, %L, %L, %L, NULL)', 'a.json', repeat('e', 64), 'FILE_BROKEN')) = 'P0001');
  UPDATE etl.object_run SET status = 'SUCCESS', finished_at = clock_timestamp() WHERE run_id = run;
  PERFORM pg_temp.ok('W05b finished run refused',
    pg_temp.err(format('SELECT etl.object_file_warning(%s, %L, %L, %L, NULL)', run, 'a.json', repeat('e', 64), 'FILE_BROKEN')) = 'P0001');
END
$t$;

-- W06 rights: only dwh_etl executes the function; dwh_app reads the view; no PUBLIC
DO $t$
BEGIN
  PERFORM pg_temp.ok('W06 EXECUTE only for dwh_etl; view readable by dwh_app',
    has_function_privilege('dwh_etl', 'etl.object_file_warning(bigint, text, text, text, text)', 'EXECUTE')
    AND NOT has_function_privilege('dwh_app', 'etl.object_file_warning(bigint, text, text, text, text)', 'EXECUTE')
    AND has_table_privilege('dwh_app', 'etl.v_object_load_status', 'SELECT')
    AND NOT has_table_privilege('dwh_app', 'etl.error_log', 'INSERT'));
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
