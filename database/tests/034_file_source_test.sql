-- I12 test of 034 (E0 sandbox, E1 copy), run after the package as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 034_file_source_test.sql
-- One transaction, ROLLED BACK. Inside it app.integration_admin_session_user() is replaced: token 'a'x64 = an
-- active admin, anything else is refused. Test rows are made from the required columns of the catalog.
-- Errors are compared by SQLSTATE (the server writes messages in Russian).
-- Expected last line: ALL_TESTS_PASS (F01-F12).
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
      PERFORM set_config('dwh.actor_username', 'i12-test', true);
      RETURN v;
    END; $b$ $f$, v_admin);
  v_file := pg_temp.make_row('meta.source_system', 'source_system_id',
            jsonb_build_object('code', 'I1234_FILES', 'name', 'I12 test file source', 'is_active', true,
              'source_type', 'FILE'));
  v_db := pg_temp.make_row('meta.source_system', 'source_system_id',
            jsonb_build_object('code', 'I1234_DB', 'name', 'I12 test DB source', 'is_active', true,
              'source_type', 'POSTGRESQL'));
  v_tab := pg_temp.make_row('meta.source_object', 'source_object_id',
            jsonb_build_object('source_system_id', v_db, 'source_schema', 'public', 'source_object', 'i1234_t',
              'object_type', 'TABLE', 'is_active', true));
  PERFORM set_config('t034.file', v_file::text, true);
  PERFORM set_config('t034.db', v_db::text, true);
  PERFORM set_config('t034.tab', v_tab::text, true);
  PERFORM set_config('t034.admin', v_admin::text, true);
END
$setup$;

-- F01 file object: only in a FILE source, code checked, unique per source, session required
DO $t$
DECLARE
  tk text := repeat('a', 64);
  v_file bigint := current_setting('t034.file')::bigint;
  v_db bigint := current_setting('t034.db')::bigint;
  o bigint;
BEGIN
  o := app.integration_file_object_create(tk, v_file, 'Services_Daily', 'test');
  PERFORM pg_temp.ok('F01 file object created (schema file, type FILE, code lower case)',
    (SELECT source_schema = 'file' AND source_object = 'services_daily' AND object_type = 'FILE' AND is_active
       FROM meta.source_object WHERE source_object_id = o)
    AND pg_temp.err(format('SELECT app.integration_file_object_create(%L, %s, %L)', tk, v_db, 'x')) = 'P0001'
    AND pg_temp.err(format('SELECT app.integration_file_object_create(%L, %s, %L)', tk, v_file, 'services_daily')) = 'P0001'
    AND pg_temp.err(format('SELECT app.integration_file_object_create(%L, %s, %L)', tk, v_file, '1bad-code')) = 'P0001'
    AND pg_temp.err(format('SELECT app.integration_file_object_create(%L, %s, %L)', repeat('b', 64), v_file, 'y')) = 'P0001');
  PERFORM set_config('t034.obj', o::text, true);
END
$t$;

-- F02 settings need a draft; F03 saved into the draft, bad mask / format / mode / credential key refused
DO $t$
DECLARE
  tk text := repeat('a', 64);
  o bigint := current_setting('t034.obj')::bigint;
  cfg jsonb := '{"mask": "services_*.json", "format": "JSON", "records_path": "data.items",
                 "date_path": "meta.actual_at", "mode": "LATEST", "time_zone": "Asia/Baku"}';
  v_draft bigint;
BEGIN
  PERFORM pg_temp.ok('F02 no draft: settings refused',
    pg_temp.err(format('SELECT app.integration_file_config_save(%L, %s, %L::jsonb)', tk, o, cfg)) = 'P0001');
  PERFORM app.integration_object_config_save_draft(tk, o, NULL, '{"fields": [], "key": []}'::jsonb);
  v_draft := app.integration_file_config_save(tk, o, cfg);
  PERFORM pg_temp.ok('F03 settings saved in the draft; bad values refused',
    (SELECT config ->> 'date_path' = 'meta.actual_at' FROM meta.object_file_config WHERE version_id = v_draft)
    AND pg_temp.err(format('SELECT app.integration_file_config_save(%L, %s, %L::jsonb)', tk, o,
          cfg || '{"mask": "../etc/*"}')) = '23514'
    AND pg_temp.err(format('SELECT app.integration_file_config_save(%L, %s, %L::jsonb)', tk, o,
          cfg || '{"format": "CSV"}')) = '23514'
    AND pg_temp.err(format('SELECT app.integration_file_config_save(%L, %s, %L::jsonb)', tk, o,
          cfg || '{"mode": "ANY"}')) = '23514'
    AND pg_temp.err(format('SELECT app.integration_file_config_save(%L, %s, %L::jsonb)', tk, o,
          cfg || '{"password": "z"}')) = '23514'
    AND pg_temp.err(format('SELECT app.integration_file_config_save(%L, %s, %L::jsonb)', tk,
          current_setting('t034.tab'), cfg)) = 'P0001');
  PERFORM set_config('t034.draft', v_draft::text, true);
END
$t$;

-- F04 get: draft settings, nothing published, folder = code of the source
DO $t$
DECLARE
  g record;
BEGIN
  SELECT * INTO g FROM app.integration_file_config_get(repeat('a', 64), current_setting('t034.obj')::bigint);
  PERFORM pg_temp.ok('F04 get shows the draft settings and the source code',
    g.draft_version_id = current_setting('t034.draft')::bigint AND g.draft_config ->> 'format' = 'JSON'
    AND g.published_version_id IS NULL AND g.source_code = 'I1234_FILES');
END
$t$;

-- F05 published version: settings frozen; F06 a new draft copies them
DO $t$
DECLARE
  tk text := repeat('a', 64);
  o bigint := current_setting('t034.obj')::bigint;
  v bigint := current_setting('t034.draft')::bigint;
  v_new bigint;
BEGIN
  CREATE TABLE stg.i1234_services (id bigint PRIMARY KEY);
  UPDATE meta.object_config_version
     SET status = 'PUBLISHED', target_schema = 'stg', target_table = 'i1234_services', definition_hash = repeat('c', 64),
         published_at = clock_timestamp(), published_by = current_setting('t034.admin')::bigint
   WHERE version_id = v;
  INSERT INTO meta.object_config_current (source_object_id, version_id, status, updated_by)
  VALUES (o, v, 'READY', current_setting('t034.admin')::bigint);
  PERFORM pg_temp.ok('F05 settings of a published version cannot change',
    pg_temp.err(format('UPDATE meta.object_file_config SET config = config || %L WHERE version_id = %s',
                       '{"format": "XML"}', v)) = 'P0001'
    AND pg_temp.err(format('DELETE FROM meta.object_file_config WHERE version_id = %s', v)) = 'P0001'
    AND pg_temp.err('TRUNCATE meta.object_file_config') = 'P0001'
    AND pg_temp.err(format('SELECT app.integration_file_config_save(%L, %s, %L::jsonb)', tk, o,
          '{"mask": "a.json", "format": "JSON", "mode": "LATEST"}')) = 'P0001');
  PERFORM app.integration_object_config_save_draft(tk, o, NULL, '{"fields": [], "key": []}'::jsonb);
  SELECT version_id INTO v_new FROM meta.object_config_version WHERE source_object_id = o AND status = 'DRAFT';
  PERFORM pg_temp.ok('F06 new draft starts with the settings of the published version',
    (SELECT config ->> 'mask' = 'services_*.json' FROM meta.object_file_config WHERE version_id = v_new)
    AND (SELECT published_config IS NOT NULL AND draft_version_id = v_new
           FROM app.integration_file_config_get(tk, o)));
END
$t$;

-- F07 structure of the object as a snapshot of this one object
DO $t$
DECLARE
  tk text := repeat('a', 64);
  o bigint := current_setting('t034.obj')::bigint;
  c record;
BEGIN
  SELECT * INTO c FROM app.integration_file_object_capture(tk, o,
    '[{"column_name": "id", "formatted_type": "bigint", "is_nullable": false},
      {"column_name": "name", "formatted_type": "text"}]');
  PERFORM pg_temp.ok('F07 snapshot of the file object with 2 columns; empty structure refused',
    c.columns_captured = 2
    AND (SELECT count(*) FROM meta.source_column_snapshot cs JOIN meta.source_object_snapshot os USING (object_snapshot_id)
          WHERE os.metadata_capture_id = c.metadata_capture_id AND os.object_type = 'FILE') = 2
    AND pg_temp.err(format('SELECT app.integration_file_object_capture(%L, %s, %L::jsonb)', tk, o, '[]')) = 'P0001');
END
$t$;

-- F08 run start: no connection needed, file settings given; register calls refused for a run of a table object
DO $t$
DECLARE
  o bigint := current_setting('t034.obj')::bigint;
  r jsonb;
BEGIN
  r := etl.object_run_begin(o);
  PERFORM pg_temp.ok('F08 run start of a file object gives file_config and the source code',
    r -> 'source' -> 'file_config' ->> 'mode' = 'LATEST' AND r -> 'source' ->> 'code' = 'I1234_FILES'
    AND r -> 'source' -> 'connection_config' = 'null'::jsonb);
  PERFORM set_config('t034.run', r ->> 'run_id', true);
  PERFORM pg_temp.ok('F08b register refused for an unknown run',
    pg_temp.err('SELECT etl.object_file_state(-1)') = 'P0001'
    AND pg_temp.err(format('SELECT etl.object_file_record(-1, %L, 1, %L, now(), %L, 1, NULL)',
          'a.json', repeat('e', 64), 'LOADED')) = 'P0001');
END
$t$;

-- F09 register: results per content; LOADED needs a date and never changes; FAILED is retried
DO $t$
DECLARE
  run bigint := current_setting('t034.run')::bigint;
  s jsonb;
  id1 bigint; id2 bigint;
BEGIN
  s := etl.object_file_state(run);
  PERFORM pg_temp.ok('F09a empty register: no last date, nothing known',
    s -> 'last_loaded_at' = 'null'::jsonb AND s -> 'known' = '{}'::jsonb);
  PERFORM etl.object_file_record(run, 'services_1.json', 10, repeat('1', 64), '2026-11-20 10:00+04', 'LOADED', 5, NULL);
  PERFORM etl.object_file_record(run, 'services_0.json', 10, repeat('0', 64), '2026-11-19 10:00+04', 'NOT_ACTUAL', NULL,
    'older than the last loaded file');
  PERFORM etl.object_file_record(run, 'services_x.json', 10, repeat('3', 64), NULL, 'NO_DATE', NULL, 'no date');
  id1 := etl.object_file_record(run, 'services_2.json', 10, repeat('2', 64), NULL, 'FAILED', NULL, 'FILE_BROKEN');
  id2 := etl.object_file_record(run, 'services_2.json', 12, repeat('2', 64), '2026-11-21 10:00+04', 'LOADED', 7, NULL);
  s := etl.object_file_state(run);
  PERFORM pg_temp.ok('F09b last loaded date, known contents, FAILED retried in the same row',
    (s ->> 'last_loaded_at')::timestamptz = '2026-11-21 10:00+04'::timestamptz
    AND s -> 'known' ->> repeat('0', 64) = 'NOT_ACTUAL' AND s -> 'known' ->> repeat('2', 64) = 'LOADED'
    AND id1 = id2 AND (SELECT attempts = 2 AND file_size = 12 FROM etl.object_file WHERE file_id = id1));
  PERFORM pg_temp.ok('F09c LOADED never applied again; LOADED without a date, bad status, bad name refused',
    pg_temp.err(format('SELECT etl.object_file_record(%s, %L, 1, %L, now(), %L, 1, NULL)',
          run, 'again.json', repeat('1', 64), 'FAILED')) = 'P0001'
    AND pg_temp.err(format('SELECT etl.object_file_record(%s, %L, 1, %L, NULL, %L, 1, NULL)',
          run, 'd.json', repeat('4', 64), 'LOADED')) = '23514'
    AND pg_temp.err(format('SELECT etl.object_file_record(%s, %L, 1, %L, now(), %L, 1, NULL)',
          run, 'd.json', repeat('4', 64), 'SKIPPED')) = '23514'
    AND pg_temp.err(format('SELECT etl.object_file_record(%s, %L, 1, %L, now(), %L, 1, NULL)',
          run, '../d.json', repeat('4', 64), 'FAILED')) = '23514'
    AND pg_temp.err(format('SELECT etl.object_file_record(%s, %L, 1, %L, now(), %L, 1, NULL)',
          run, 'd.json', 'XYZ', 'FAILED')) = '23514');
END
$t$;

-- F10 a finished run cannot write to the register
DO $t$
DECLARE
  run bigint := current_setting('t034.run')::bigint;
BEGIN
  UPDATE etl.object_run SET status = 'SUCCESS', finished_at = clock_timestamp() WHERE run_id = run;
  PERFORM pg_temp.ok('F10 register refused after the run finished',
    pg_temp.err(format('SELECT etl.object_file_state(%s)', run)) = 'P0001'
    AND pg_temp.err(format('SELECT etl.object_file_record(%s, %L, 1, %L, now(), %L, 1, NULL)',
          run, 'e.json', repeat('5', 64), 'FAILED')) = 'P0001');
END
$t$;

-- F11 administrator sees the register (newest first); TRUNCATE of the register refused
DO $t$
DECLARE
  tk text := repeat('a', 64);
  o bigint := current_setting('t034.obj')::bigint;
BEGIN
  PERFORM pg_temp.ok('F11 register for the administrator; no TRUNCATE; no access with another token',
    (SELECT count(*) FROM app.integration_file_registry(tk, o)) = 4
    AND (SELECT status FROM app.integration_file_registry(tk, o, 1)) = 'LOADED'
    AND pg_temp.err('TRUNCATE etl.object_file') = 'P0001'
    AND pg_temp.err(format('SELECT * FROM app.integration_file_registry(%L, %s)', repeat('b', 64), o)) = 'P0001'
    AND pg_temp.err(format('SELECT * FROM app.integration_file_registry(%L, %s)', tk,
          current_setting('t034.tab'))) = 'P0001');
END
$t$;

-- F12 object types: FILE allowed, other values still refused; tables not readable by the service roles
DO $t$
DECLARE
  o bigint := current_setting('t034.obj')::bigint;
BEGIN
  PERFORM pg_temp.ok('F12 object type XLS refused; dwh_etl and dwh_app cannot read the register directly',
    pg_temp.err(format('UPDATE meta.source_object SET object_type = %L WHERE source_object_id = %s', 'XLS', o)) = '23514'
    AND NOT has_table_privilege('dwh_etl', 'etl.object_file', 'SELECT')
    AND NOT has_table_privilege('dwh_app', 'etl.object_file', 'SELECT')
    AND NOT has_table_privilege('dwh_etl', 'meta.object_file_config', 'SELECT'));
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
