-- I04 test of 006: connection test result and READY, credential READ, schema snapshot.
-- Everything runs in one transaction and is ROLLED BACK; no row survives.
-- For E0 (sandbox with fixtures) and E1 (restored copy), never the working dwh.
-- As in the 005 test, app.integration_admin_session_user() is replaced inside the
-- transaction by a version mapping two fake tokens to an active admin and to a
-- foreign user id; ROLLBACK restores the real function. Test sources and objects
-- are created inside the transaction (pg_temp.make_object).
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 006_source_connection_test_read_snapshot_test.sql
-- Expected last line: ALL_TESTS_PASS (labels T01-T30: 30 checks).
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
  vals text := format('%s, %L, %L, %L', p_system, 'i04_test', p_name, p_active);
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
      ELSE quote_literal('i04t_' || p_system || '_' || p_name) END;
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

DO $t$
DECLARE
  tok text := repeat('a', 64);
  other text := repeat('b', 64);
  src bigint;
  src2 bigint;
  o1 bigint;
  o2 bigint;
  ofor bigint;
  v integer;
  r record;
  rd record;
  op uuid;
  cap record;
  objs jsonb;
  cols jsonb := '[{"ordinal_position": 1, "column_name": "id", "formatted_type": "bigint", "udt_name": "int8",
                   "is_nullable": false, "is_primary_key": true},
                  {"ordinal_position": 2, "column_name": "name", "formatted_type": "text", "udt_name": "text",
                   "is_nullable": true}]';
  n bigint;
BEGIN
  INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
  VALUES ('I04_TEST_PG', 'I04 test source', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO src;
  INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
  VALUES ('I04_TEST_OTHER', 'I04 other source', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO src2;
  o1 := pg_temp.make_object(src, 'items', true);
  o2 := pg_temp.make_object(src, 'orders', true);
  PERFORM pg_temp.make_object(src, 'old', false);
  ofor := pg_temp.make_object(src2, 'foreign', true);

  -- Connection test result and READY.
  v := app.integration_source_connection_save(tok, src, 'DATABASE', '{"host": "db.local", "port": 5432}');
  SELECT * INTO r FROM app.integration_source_connection_get(tok, src);
  PERFORM pg_temp.ok('T01 new connection: DRAFT, no test result',
    r.status = 'DRAFT' AND r.last_test_at IS NULL AND r.last_test_ok IS NULL AND r.last_test_code IS NULL);
  PERFORM pg_temp.expect_fail('T02 set_status READY without a test',
    format('SELECT app.integration_source_connection_set_status(%L, %s, %s, %L)', tok, src, v, 'READY'));
  PERFORM pg_temp.expect_fail('T03 invalid test code (lower case)',
    format('SELECT * FROM app.integration_source_connection_record_test(%L, %s, %s, false, %L)', tok, src, v, 'auth failed'));
  PERFORM pg_temp.expect_fail('T04 test result for an old row_version',
    format('SELECT * FROM app.integration_source_connection_record_test(%L, %s, %s, true, %L)', tok, src, v + 5, 'OK'));
  SELECT * INTO r FROM app.integration_source_connection_record_test(tok, src, v, false, 'AUTH_FAILED');
  PERFORM pg_temp.ok('T05 failed test: stays DRAFT, version +1', r.status = 'DRAFT' AND r.row_version = v + 1);
  v := r.row_version;
  SELECT * INTO r FROM app.integration_source_connection_record_test(tok, src, v, true, 'OK', 'PostgreSQL 16.4');
  PERFORM pg_temp.ok('T06 successful test of DRAFT: READY', r.status = 'READY' AND r.row_version = v + 1);
  v := r.row_version;
  SELECT * INTO r FROM app.integration_source_connection_get(tok, src);
  PERFORM pg_temp.ok('T07 get shows the result',
    r.last_test_ok AND r.last_test_code = 'OK' AND r.last_test_server_version = 'PostgreSQL 16.4' AND r.last_test_at IS NOT NULL);
  SELECT * INTO r FROM app.integration_source_connection_record_test(tok, src, v, false, 'CONNECT_TIMEOUT');
  PERFORM pg_temp.ok('T08 failed test of READY: back to DRAFT', r.status = 'DRAFT');
  v := r.row_version;
  SELECT * INTO r FROM app.integration_source_connection_record_test(tok, src, v, true, 'OK');
  v := r.row_version;
  v := app.integration_source_connection_save(tok, src, 'DATABASE', '{"host": "db2.local", "port": 5432}', v);
  SELECT * INTO r FROM app.integration_source_connection_get(tok, src);
  PERFORM pg_temp.ok('T09 new settings: READY -> DRAFT, test result cleared',
    r.status = 'DRAFT' AND r.last_test_at IS NULL AND r.last_test_ok IS NULL);
  SELECT * INTO r FROM app.integration_source_connection_record_test(tok, src, v, true, 'OK');
  v := r.row_version;
  v := app.integration_source_connection_set_status(tok, src, v, 'DISABLED');
  SELECT * INTO r FROM app.integration_source_connection_record_test(tok, src, v, true, 'OK');
  PERFORM pg_temp.ok('T10 test of DISABLED keeps DISABLED', r.status = 'DISABLED');
  v := r.row_version;
  v := app.integration_source_connection_set_status(tok, src, v, 'READY');
  PERFORM pg_temp.ok('T11 READY allowed after a successful test',
    (SELECT status FROM meta.source_connection WHERE source_system_id = src) = 'READY');
  PERFORM pg_temp.expect_fail('T12 table constraint: READY needs a successful test',
    format('UPDATE meta.source_connection SET last_test_ok = false, row_version = row_version + 1 WHERE source_system_id = %s', src));
  PERFORM pg_temp.expect_fail('T13 table constraint: incomplete test result',
    format('UPDATE meta.source_connection SET status = %L, last_test_code = NULL, row_version = row_version + 1 WHERE source_system_id = %s', 'DRAFT', src));

  -- Credential READ.
  PERFORM pg_temp.expect_fail('T14 READ without an active credential',
    format('SELECT * FROM app.integration_source_secret_read_begin(%L, %s)', tok, src));
  SELECT * INTO rd FROM app.integration_source_secret_begin(tok, src);
  PERFORM app.integration_source_secret_complete(tok, src, rd.operation_id, true, repeat('0', 64), 'K_TEST_01');
  SELECT * INTO r FROM app.integration_source_secret_read_begin(tok, src);
  PERFORM pg_temp.ok('T15 READ returns the ACTIVE secret_ref and key_id',
    r.secret_ref = rd.secret_ref AND r.key_id = 'K_TEST_01' AND r.row_version = v);
  op := r.operation_id;
  PERFORM pg_temp.ok('T16 READ ATTEMPT journaled with the actor',
    EXISTS (SELECT 1 FROM secret_store.operation_event e WHERE e.operation_id = op AND e.operation_type = 'READ'
             AND e.phase = 'ATTEMPT' AND e.code = 'PERMITTED_ATTEMPT' AND e.actor_kind = 'DWH_USER'
             AND e.create_operation_id = rd.operation_id));
  PERFORM pg_temp.expect_fail('T17 READ finish by another user',
    format('SELECT app.integration_source_secret_read_finish(%L, %s, %L, true)', other, src, op));
  PERFORM pg_temp.expect_fail('T18 READ finish for another source',
    format('SELECT app.integration_source_secret_read_finish(%L, %s, %L, true)', tok, src2, op));
  PERFORM app.integration_source_secret_read_finish(tok, src, op, true);
  PERFORM app.integration_source_secret_read_finish(tok, src, op, true);
  PERFORM pg_temp.ok('T19 READ OUTCOME SUCCEEDED once (repeat is a no-op)',
    (SELECT count(*) FROM secret_store.operation_event e WHERE e.operation_id = op AND e.phase = 'OUTCOME'
       AND e.code = 'SUCCEEDED') = 1);
  PERFORM pg_temp.expect_fail('T20 READ finished again with another outcome',
    format('SELECT app.integration_source_secret_read_finish(%L, %s, %L, false)', tok, src, op));
  PERFORM pg_temp.ok('T21 READ leaves the credential ACTIVE',
    (SELECT state FROM secret_store.binding WHERE create_operation_id = rd.operation_id) = 'ACTIVE');
  SELECT row_version INTO v FROM meta.source_connection WHERE source_system_id = src;
  v := app.integration_source_connection_set_status(tok, src, v, 'DISABLED');
  PERFORM pg_temp.expect_fail('T22 READ of a DISABLED connection',
    format('SELECT * FROM app.integration_source_secret_read_begin(%L, %s)', tok, src));
  v := app.integration_source_connection_set_status(tok, src, v, 'DRAFT');

  -- Schema snapshot.
  objs := jsonb_build_array(
    jsonb_build_object('source_object_id', o1, 'relkind', 'r', 'object_type', 'TABLE', 'columns', cols),
    jsonb_build_object('source_object_id', o2, 'relkind', 'r', 'object_type', 'TABLE', 'columns', cols));
  PERFORM pg_temp.expect_fail('T23 snapshot without every active object',
    format('SELECT * FROM app.integration_source_metadata_capture(%L, %s, %L, %L, %L)', tok, src, 'db', '16',
           jsonb_build_array(objs -> 0)));
  PERFORM pg_temp.expect_fail('T24 snapshot with an object twice',
    format('SELECT * FROM app.integration_source_metadata_capture(%L, %s, %L, %L, %L)', tok, src, 'db', '16',
           objs || jsonb_build_array(objs -> 0)));
  PERFORM pg_temp.expect_fail('T25 snapshot with an object of another source',
    format('SELECT * FROM app.integration_source_metadata_capture(%L, %s, %L, %L, %L)', tok, src, 'db', '16',
           objs || jsonb_build_array(jsonb_build_object('source_object_id', ofor, 'relkind', 'r',
                                                         'object_type', 'TABLE', 'columns', cols))));
  PERFORM pg_temp.expect_fail('T26 snapshot of an object without columns',
    format('SELECT * FROM app.integration_source_metadata_capture(%L, %s, %L, %L, %L)', tok, src, 'db', '16',
           jsonb_build_array(objs -> 0, jsonb_set(objs -> 1, '{columns}', '[]'))));
  PERFORM pg_temp.ok('T27 rejected snapshots leave no capture',
    NOT EXISTS (SELECT 1 FROM meta.metadata_capture WHERE source_system_id = src));
  SELECT * INTO cap FROM app.integration_source_metadata_capture(tok, src, 'testdb', 'PostgreSQL 16.4', objs);
  PERFORM pg_temp.ok('T28 snapshot: one SUCCESS capture, 2 objects, 4 columns',
    cap.objects_captured = 2 AND cap.columns_captured = 4
    AND (SELECT status = 'SUCCESS' AND objects_captured = 2 AND columns_captured = 4 AND finished_at IS NOT NULL
           AND created_by LIKE 'dwh_user:%' FROM meta.metadata_capture WHERE metadata_capture_id = cap.metadata_capture_id));
  PERFORM pg_temp.ok('T29 names come from meta.source_object, primary key kept',
    (SELECT string_agg(s.source_schema || '.' || s.source_object, ',' ORDER BY s.source_object)
       FROM meta.source_object_snapshot s WHERE s.metadata_capture_id = cap.metadata_capture_id) = 'i04_test.items,i04_test.orders'
    AND (SELECT count(*) FROM meta.source_column_snapshot c JOIN meta.source_object_snapshot s
           ON s.object_snapshot_id = c.object_snapshot_id
          WHERE s.metadata_capture_id = cap.metadata_capture_id AND c.is_primary_key) = 2);
  SELECT count(*) INTO n FROM audit.security_event
   WHERE entity_key = src::text AND event_type IN ('INTEGRATION_SOURCE_CONNECTION_TESTED',
         'INTEGRATION_SOURCE_SECRET_READ', 'INTEGRATION_SOURCE_METADATA_CAPTURED');
  PERFORM pg_temp.ok('T30 audit: tests, READ and snapshot recorded', n >= 8);
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
