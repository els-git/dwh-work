-- I11 test of 033 (E0 sandbox, E1 copy), run after the package as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 033_api_source_test.sql
-- One transaction, ROLLED BACK. Inside it app.integration_admin_session_user() is replaced: token 'a'x64 = an
-- active admin, anything else is refused. Test rows are made from the required columns of the catalog.
-- Errors are compared by SQLSTATE (the server writes messages in Russian).
-- Expected last line: ALL_TESTS_PASS (P01-P12).
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
      ELSE quote_literal('i1133')
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
  v_api bigint;
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
      PERFORM set_config('dwh.actor_username', 'i11-test', true);
      RETURN v;
    END; $b$ $f$, v_admin);
  v_api := pg_temp.make_row('meta.source_system', 'source_system_id',
            jsonb_build_object('code', 'I1133_API', 'name', 'I11 test API source', 'is_active', true, 'source_type', 'API'));
  v_db := pg_temp.make_row('meta.source_system', 'source_system_id',
            jsonb_build_object('code', 'I1133_DB', 'name', 'I11 test DB source', 'is_active', true,
              'source_type', 'POSTGRESQL'));
  v_tab := pg_temp.make_row('meta.source_object', 'source_object_id',
            jsonb_build_object('source_system_id', v_db, 'source_schema', 'public', 'source_object', 'i1133_t',
              'object_type', 'TABLE', 'is_active', true));
  PERFORM set_config('t033.api', v_api::text, true);
  PERFORM set_config('t033.db', v_db::text, true);
  PERFORM set_config('t033.tab', v_tab::text, true);
  PERFORM set_config('t033.admin', v_admin::text, true);
END
$setup$;

-- P01 API object: only in an API source, code checked, unique per source
DO $t$
DECLARE
  tk text := repeat('a', 64);
  v_api bigint := current_setting('t033.api')::bigint;
  v_db bigint := current_setting('t033.db')::bigint;
  o bigint;
BEGIN
  o := app.integration_api_object_create(tk, v_api, 'Services_V1', 'test');
  PERFORM pg_temp.ok('P01 API object created (schema api, type API, code lower case)',
    (SELECT source_schema = 'api' AND source_object = 'services_v1' AND object_type = 'API' AND is_active
       FROM meta.source_object WHERE source_object_id = o)
    AND pg_temp.err(format('SELECT app.integration_api_object_create(%L, %s, %L)', tk, v_db, 'x')) = 'P0001'
    AND pg_temp.err(format('SELECT app.integration_api_object_create(%L, %s, %L)', tk, v_api, 'services_v1')) = 'P0001'
    AND pg_temp.err(format('SELECT app.integration_api_object_create(%L, %s, %L)', tk, v_api, '1bad-code')) = 'P0001'
    AND pg_temp.err(format('SELECT app.integration_api_object_create(%L, %s, %L)', repeat('b', 64), v_api, 'y')) = 'P0001');
  PERFORM set_config('t033.obj', o::text, true);
END
$t$;

-- P02 request needs a draft; P03 request saved into the draft, credentials and a missing url refused
DO $t$
DECLARE
  tk text := repeat('a', 64);
  o bigint := current_setting('t033.obj')::bigint;
  req jsonb := '{"url": "https://dwh.example/pv-api/v1/services", "format": "JSON", "records_path": "data.items",
                 "auth": {"kind": "BEARER"}, "pagination": {"kind": "PAGE", "page_param": "page"}}';
  v_draft bigint;
BEGIN
  PERFORM pg_temp.ok('P02 no draft: request refused',
    pg_temp.err(format('SELECT app.integration_api_request_save(%L, %s, %L::jsonb)', tk, o, req)) = 'P0001');
  PERFORM app.integration_object_config_save_draft(tk, o, NULL, '{"fields": [], "key": []}'::jsonb);
  v_draft := app.integration_api_request_save(tk, o, req);
  PERFORM pg_temp.ok('P03 request saved in the draft; credential key and missing url refused',
    (SELECT request ->> 'records_path' = 'data.items' FROM meta.object_api_request WHERE version_id = v_draft)
    AND pg_temp.err(format('SELECT app.integration_api_request_save(%L, %s, %L::jsonb)', tk, o,
          '{"url": "https://x/y", "headers": [{"name": "A", "token": "z"}]}')) = 'P0001'
    AND pg_temp.err(format('SELECT app.integration_api_request_save(%L, %s, %L::jsonb)', tk, o, '{"format": "JSON"}')) = '23514'
    AND pg_temp.err(format('SELECT app.integration_api_request_save(%L, %s, %L::jsonb)', tk,
          current_setting('t033.tab'), req)) = 'P0001');
  PERFORM set_config('t033.draft', v_draft::text, true);
END
$t$;

-- P04 get: draft request, nothing published, no credential
DO $t$
DECLARE
  g record;
BEGIN
  SELECT * INTO g FROM app.integration_api_request_get(repeat('a', 64), current_setting('t033.obj')::bigint);
  PERFORM pg_temp.ok('P04 get shows the draft request and no credential',
    g.draft_version_id = current_setting('t033.draft')::bigint AND g.draft_request ->> 'format' = 'JSON'
    AND g.published_version_id IS NULL AND g.has_credential IS FALSE);
END
$t$;

-- P05 published version: its request is frozen; P06 a new draft copies it
DO $t$
DECLARE
  tk text := repeat('a', 64);
  o bigint := current_setting('t033.obj')::bigint;
  v bigint := current_setting('t033.draft')::bigint;
  v_new bigint;
BEGIN
  UPDATE meta.object_config_version
     SET status = 'PUBLISHED', target_schema = 'stg', target_table = 'i1133_services', definition_hash = repeat('c', 64),
         published_at = clock_timestamp(), published_by = current_setting('t033.admin')::bigint
   WHERE version_id = v;
  INSERT INTO meta.object_config_current (source_object_id, version_id, status, updated_by)
  VALUES (o, v, 'READY', current_setting('t033.admin')::bigint);
  PERFORM pg_temp.ok('P05 request of a published version cannot change',
    pg_temp.err(format('UPDATE meta.object_api_request SET request = request || %L WHERE version_id = %s',
                       '{"format": "XML"}', v)) = 'P0001'
    AND pg_temp.err(format('DELETE FROM meta.object_api_request WHERE version_id = %s', v)) = 'P0001'
    AND pg_temp.err(format('SELECT app.integration_api_request_save(%L, %s, %L::jsonb)', tk, o,
          '{"url": "https://x/y"}')) = 'P0001');
  PERFORM app.integration_object_config_save_draft(tk, o, NULL, '{"fields": [], "key": []}'::jsonb);
  SELECT version_id INTO v_new FROM meta.object_config_version WHERE source_object_id = o AND status = 'DRAFT';
  PERFORM pg_temp.ok('P06 new draft starts with the request of the published version',
    (SELECT request ->> 'records_path' = 'data.items' FROM meta.object_api_request WHERE version_id = v_new)
    AND (SELECT published_request IS NOT NULL AND draft_version_id = v_new
           FROM app.integration_api_request_get(tk, o)));
END
$t$;

-- P07 credential of the object: CREATE, REPLACE (failed), REVOKE; not for a table object
DO $t$
DECLARE
  tk text := repeat('a', 64);
  o bigint := current_setting('t033.obj')::bigint;
  b record;
  r text;
BEGIN
  SELECT * INTO b FROM app.integration_object_secret_begin(tk, o);
  r := app.integration_object_secret_complete(tk, o, b.operation_id, true, repeat('d', 64), 'K2026_04');
  PERFORM pg_temp.ok('P07a CREATE -> ACTIVE, profile OBJECT_<id>',
    b.operation_type = 'CREATE' AND r = 'ACTIVE'
    AND (SELECT count(*) FROM secret_store.binding WHERE profile_code = 'OBJECT_' || o AND state = 'ACTIVE') = 1
    AND (SELECT has_credential AND credential_key_id = 'K2026_04' FROM app.integration_api_request_get(tk, o)));
  SELECT * INTO b FROM app.integration_object_secret_begin(tk, o);
  r := app.integration_object_secret_complete(tk, o, b.operation_id, false);
  PERFORM pg_temp.ok('P07b REPLACE that failed keeps the old credential; other user cannot finish',
    b.operation_type = 'REPLACE' AND r = 'FAILED'
    AND (SELECT count(*) FROM secret_store.binding WHERE profile_code = 'OBJECT_' || o AND state = 'ACTIVE') = 1
    AND pg_temp.err(format('SELECT app.integration_object_secret_begin(%L, %s)', tk, current_setting('t033.tab'))) = 'P0001'
    AND pg_temp.err(format('SELECT app.integration_object_secret_begin(%L, %s)', repeat('b', 64), o)) = 'P0001');
END
$t$;

-- P08 READ by an administrator: ATTEMPT + OUTCOME in the journal
DO $t$
DECLARE
  tk text := repeat('a', 64);
  o bigint := current_setting('t033.obj')::bigint;
  rd record;
BEGIN
  SELECT * INTO rd FROM app.integration_object_secret_read_begin(tk, o);
  -- a statement does not see rows written by a function it calls: finish first, count after
  PERFORM app.integration_object_secret_read_finish(tk, o, rd.operation_id, true);
  PERFORM pg_temp.ok('P08 read: secret_ref and key id given, outcome recorded',
    rd.secret_ref IS NOT NULL AND rd.key_id = 'K2026_04'
    AND (SELECT count(*) FROM secret_store.operation_event WHERE operation_id = rd.operation_id) = 2);
END
$t$;

-- P09 load READ: only for a RUNNING request of this object, once
DO $t$
DECLARE
  o bigint := current_setting('t033.obj')::bigint;
  v_admin bigint := current_setting('t033.admin')::bigint;
  q1 bigint; q2 bigint;
  rd record;
BEGIN
  INSERT INTO etl.object_run_request (source_object_id, run_kind, status, requested_by_user_id, requested_by_username,
                                      claimed_by, claimed_at, started_at)
  VALUES (o, 'RUN', 'RUNNING', v_admin, 't033', 'w', clock_timestamp(), clock_timestamp()) RETURNING request_id INTO q1;
  INSERT INTO etl.object_run_request (source_object_id, run_kind, status, requested_by_user_id, requested_by_username,
                                      claimed_by, claimed_at, started_at)
  VALUES (current_setting('t033.tab')::bigint, 'RUN', 'RUNNING', v_admin, 't033', 'w', clock_timestamp(), clock_timestamp())
  RETURNING request_id INTO q2;
  SELECT * INTO rd FROM etl.object_secret_read_begin(o, q2);
  PERFORM pg_temp.ok('P09a request of another object refused', rd.refusal = 'REQUEST_OF_OTHER_OBJECT');
  SELECT * INTO rd FROM etl.object_secret_read_begin(o, q1);
  PERFORM pg_temp.ok('P09b running request of the object: credential given',
    rd.refusal IS NULL AND rd.secret_ref IS NOT NULL);
  PERFORM etl.source_secret_read_finish(rd.operation_id, true);
  SELECT * INTO rd FROM etl.object_secret_read_begin(o, q1);
  PERFORM pg_temp.ok('P09c second read for the same request refused', rd.refusal = 'CREDENTIAL_ALREADY_READ');
END
$t$;

-- P10 REVOKE; a second REVOKE refused
DO $t$
DECLARE
  tk text := repeat('a', 64);
  o bigint := current_setting('t033.obj')::bigint;
BEGIN
  PERFORM app.integration_object_secret_revoke(tk, o);
  PERFORM pg_temp.ok('P10 revoke, then no credential',
    NOT (SELECT has_credential FROM app.integration_api_request_get(tk, o))
    AND pg_temp.err(format('SELECT app.integration_object_secret_revoke(%L, %s)', tk, o)) = 'P0001');
END
$t$;

-- P11 structure of the object as a snapshot of this one object
DO $t$
DECLARE
  tk text := repeat('a', 64);
  o bigint := current_setting('t033.obj')::bigint;
  c record;
BEGIN
  SELECT * INTO c FROM app.integration_api_object_capture(tk, o,
    '[{"column_name": "id", "formatted_type": "bigint", "is_nullable": false},
      {"column_name": "updated_at", "formatted_type": "timestamp with time zone"}]');
  PERFORM pg_temp.ok('P11 snapshot of the API object with 2 columns; empty structure refused',
    c.columns_captured = 2
    AND (SELECT status = 'SUCCESS' AND objects_captured = 1 FROM meta.metadata_capture WHERE metadata_capture_id = c.metadata_capture_id)
    AND (SELECT count(*) FROM meta.source_column_snapshot cs JOIN meta.source_object_snapshot os USING (object_snapshot_id)
          WHERE os.metadata_capture_id = c.metadata_capture_id AND os.object_type = 'API') = 2
    AND pg_temp.err(format('SELECT app.integration_api_object_capture(%L, %s, %L::jsonb)', tk, o, '[]')) = 'P0001');
END
$t$;

-- P12 object type check: API allowed, other values still refused; run start without a connection reaches the
-- target check for an API object (no 'connection settings' refusal)
DO $t$
DECLARE
  o bigint := current_setting('t033.obj')::bigint;
  msg text;
BEGIN
  BEGIN
    PERFORM etl.object_run_begin(o);
    msg := 'NO ERROR';
  EXCEPTION WHEN OTHERS THEN
    msg := SQLERRM;
  END;
  PERFORM pg_temp.ok('P12 object types; API run start does not ask for a source connection',
    pg_temp.err(format('UPDATE meta.source_object SET object_type = %L WHERE source_object_id = %s', 'FILE', o)) = '23514'
    AND msg NOT LIKE '%connection%');
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
