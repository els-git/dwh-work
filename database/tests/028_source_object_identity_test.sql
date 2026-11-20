-- I03-05 test of 028: change of schema/name and deletion of an unused source object. One transaction, ROLLED BACK.
-- For E0 (sandbox) and E1 (restored copy), never the working dwh. app.integration_admin_session_user() is
-- replaced inside the transaction (as in 008-021). Run as the owner: psql -v ON_ERROR_STOP=1 -f <this file>
-- Expected last line: ALL_TESTS_PASS (labels R01-R12).
BEGIN;

CREATE FUNCTION pg_temp.expect_fail(label text, stmt text, fragment text) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  BEGIN
    EXECUTE stmt;
  EXCEPTION WHEN OTHERS THEN
    IF position(fragment IN SQLERRM) = 0 THEN
      RAISE EXCEPTION 'FAIL %: rejected with another reason (%)', label, SQLERRM;
    END IF;
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

-- Test objects: every NOT NULL column without a default is filled by name or type (dwh has more of them than E0).
CREATE FUNCTION pg_temp.make_object(p_system bigint, p_schema text, p_name text) RETURNS bigint
LANGUAGE plpgsql AS $fn$
DECLARE
  cols text := 'source_system_id, source_schema, source_object';
  vals text := format('%s, %L, %L', p_system, p_schema, p_name);
  a record;
  v bigint;
BEGIN
  FOR a IN SELECT att.attname, format_type(att.atttypid, att.atttypmod) AS typ
             FROM pg_attribute att
            WHERE att.attrelid = 'meta.source_object'::regclass AND att.attnum > 0 AND NOT att.attisdropped
              AND att.attnotnull AND att.attidentity = '' AND att.attgenerated = ''
              AND NOT EXISTS (SELECT 1 FROM pg_attrdef d WHERE d.adrelid = att.attrelid AND d.adnum = att.attnum)
              AND att.attname NOT IN ('source_object_id', 'source_system_id', 'source_schema', 'source_object')
  LOOP
    cols := cols || ', ' || quote_ident(a.attname);
    vals := vals || ', ' || CASE
      WHEN a.attname = 'object_type' THEN quote_literal('TABLE')
      WHEN a.attname = 'load_strategy' THEN quote_literal('FULL')
      WHEN a.attname = 'is_active' THEN 'true'
      WHEN a.typ IN ('integer', 'bigint', 'smallint', 'numeric') THEN '100'
      WHEN a.typ = 'boolean' THEN 'false'
      WHEN a.typ LIKE 'timestamp%' OR a.typ = 'date' THEN 'now()'
      WHEN a.typ = 'jsonb' OR a.typ = 'json' THEN quote_literal('{}')
      ELSE quote_literal('t028_' || p_name) END;
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
      v := CASE p_token_hash WHEN repeat('a', 64) THEN %s END;
      IF v IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
      PERFORM set_config('dwh.actor_user_id', v::text, true);
      PERFORM set_config('dwh.security_source', 'INTEGRATION_CONTROL', true);
      RETURN v;
    END; $b$ $f$, v_admin);
END
$setup$;

DO $t$
DECLARE
  a text := repeat('a', 64);
  v_src bigint;
  v_free bigint; v_other bigint; v_used bigint; v_del bigint;
  v_user bigint := (SELECT min(user_id) FROM app.user_account);
  v_events bigint;
BEGIN
  INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
  VALUES ('T028_SRC', 'test 028', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO v_src;
  v_free := pg_temp.make_object(v_src, 'wrong_schema', 'services');
  v_other := pg_temp.make_object(v_src, 'public', 'app_users');
  v_used := pg_temp.make_object(v_src, 'public', 'audit_log');
  v_del := pg_temp.make_object(v_src, 'public', 'mistake');
  -- the "used" object gets a load request (any table referencing meta.source_object counts)
  INSERT INTO etl.object_run_request(source_object_id, run_kind, status, requested_by_user_id, requested_by_username)
  VALUES (v_used, 'RUN', 'QUEUED', v_user, 't028');
  SELECT count(*) INTO v_events FROM audit.security_event WHERE event_type LIKE 'INTEGRATION_SOURCE_OBJECT_%' ;

  PERFORM pg_temp.ok('R01 unused object: usage list is empty',
    NOT EXISTS (SELECT 1 FROM app.integration_source_object_usage(a, v_free)));
  PERFORM pg_temp.ok('R02 used object: usage names etl.object_run_request',
    EXISTS (SELECT 1 FROM app.integration_source_object_usage(a, v_used) u WHERE u.table_name LIKE '%object_run_request'));
  PERFORM pg_temp.ok('R03 rename of an unused object (schema fixed) returns true',
    app.integration_source_object_rename(a, v_free, '  public ', 'services'));
  PERFORM pg_temp.ok('R04 the object is now public.services',
    EXISTS (SELECT 1 FROM meta.source_object WHERE source_object_id = v_free
             AND source_schema = 'public' AND source_object = 'services'));
  PERFORM pg_temp.ok('R05 the same name again: no change, false',
    NOT app.integration_source_object_rename(a, v_free, 'public', 'services'));
  PERFORM pg_temp.expect_fail('R06 duplicate schema/name of the same system (O4)',
    format('SELECT app.integration_source_object_rename(%L, %s, %L, %L)', a, v_free, 'public', 'app_users'), 'already exists');
  PERFORM pg_temp.expect_fail('R07 rename of a used object',
    format('SELECT app.integration_source_object_rename(%L, %s, %L, %L)', a, v_used, 'public', 'audit_log2'), 'is in use');
  PERFORM pg_temp.expect_fail('R08 empty name',
    format('SELECT app.integration_source_object_rename(%L, %s, %L, %L)', a, v_free, 'public', '  '), 'is required');
  PERFORM pg_temp.expect_fail('R09 without an administrator session',
    format('SELECT app.integration_source_object_rename(%L, %s, %L, %L)', repeat('b', 64), v_free, 'x', 'y'), 'Authentication required');
  PERFORM pg_temp.expect_fail('R10 delete of a used object',
    format('SELECT app.integration_source_object_delete(%L, %s)', a, v_used), 'is in use');
  -- two statements: a query does not see the rows its own function call deleted
  PERFORM pg_temp.ok('R11a delete of an unused object returns true', app.integration_source_object_delete(a, v_del));
  PERFORM pg_temp.ok('R11 the deleted object is gone',
    NOT EXISTS (SELECT 1 FROM meta.source_object WHERE source_object_id = v_del));
  PERFORM pg_temp.ok('R12 audit: one RENAMED and one DELETED event of the test',
    (SELECT count(*) FROM audit.security_event WHERE event_type LIKE 'INTEGRATION_SOURCE_OBJECT_%') = v_events + 2
    AND EXISTS (SELECT 1 FROM audit.security_event WHERE event_type = 'INTEGRATION_SOURCE_OBJECT_RENAMED'
                 AND entity_key = v_free::text AND details ->> 'before' = 'wrong_schema.services')
    AND EXISTS (SELECT 1 FROM audit.security_event WHERE event_type = 'INTEGRATION_SOURCE_OBJECT_DELETED'
                 AND entity_key = v_del::text));
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
