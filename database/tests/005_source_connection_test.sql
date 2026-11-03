-- I03 test of 005: source connection settings, source credential operations, grants.
-- Everything runs in one transaction and is ROLLED BACK; no row survives.
-- For E0 (sandbox with fixtures) and E1 (restored copy), never the working dwh.
-- Inside the transaction app.integration_admin_session_user() is replaced by a
-- test version mapping two fake tokens to an existing active admin and to a
-- foreign user id; ROLLBACK restores the real function. Scoped permission,
-- audit and secret_store are the real objects. Needs an active SUPERADMIN/ADMIN
-- user and a source system with an active INTEGRATION_SOURCE_SYSTEM resource.
-- Test sources are created inside the transaction.
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 005_source_connection_test.sql
-- Expected last line: ALL_TESTS_PASS (labels C01-C30 and C22a, C24a, C25a: 33 checks).
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
  other text := repeat('b', 64);
  src bigint := (SELECT min(s.source_system_id) FROM meta.source_system s
                  JOIN app.resource r ON r.resource_type = 'INTEGRATION_SOURCE_SYSTEM'
                   AND r.resource_code = 'SOURCE_SYSTEM_' || s.source_system_id AND r.is_active
                 WHERE NOT EXISTS (SELECT 1 FROM meta.source_connection c WHERE c.source_system_id = s.source_system_id));
  nores bigint;
  fsrc bigint;
  v integer;
  b record;
  b2 record;
  n bigint;
BEGIN
  IF src IS NULL THEN RAISE EXCEPTION 'FAIL setup: no source system with an active resource and without a connection'; END IF;
  INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
  VALUES ('I03_TEST_NO_RESOURCE', 'I03 test source', 'POSTGRESQL', NULL, true, now(), now())
  RETURNING source_system_id INTO nores;
  INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
  VALUES ('I03_TEST_FILES', 'I03 test files', 'POSTGRESQL', NULL, true, now(), now())
  RETURNING source_system_id INTO fsrc;

  -- Connection settings.
  v := app.integration_source_connection_save(tok, src, 'database',
         '{"host": "db.local", "port": 5432, "database": "crm", "username": "reader", "options": {"sslmode": "require"}}');
  PERFORM pg_temp.ok('C01 save DATABASE connection: kind normalised, DRAFT, version 1',
    v = 1 AND (SELECT source_kind = 'DATABASE' AND status = 'DRAFT' FROM meta.source_connection WHERE source_system_id = src));
  PERFORM pg_temp.ok('C02 FILE connection for another source',
    app.integration_source_connection_save(tok, fsrc, 'FILE', '{"folder": "\\\\server\\share\\in"}') = 1);
  PERFORM pg_temp.expect_fail('C03 API source keeps settings on its objects', format('SELECT app.integration_source_connection_save(%L, %s, %L, %L)', tok, nores, 'API', '{}'));
  PERFORM pg_temp.expect_fail('C04 second connection for the same source', format('SELECT app.integration_source_connection_save(%L, %s, %L, %L)', tok, src, 'DATABASE', '{}'));
  PERFORM pg_temp.expect_fail('C05 password key in settings', format('SELECT app.integration_source_connection_save(%L, %s, %L, %L)', tok, nores, 'DATABASE', '{"password": "p"}'));
  PERFORM pg_temp.expect_fail('C06 nested Api-Key in settings', format('SELECT app.integration_source_connection_save(%L, %s, %L, %L)', tok, nores, 'FILE', '{"headers": [{"Api-Key": "k"}]}'));
  PERFORM pg_temp.expect_fail('C07 unknown session', format('SELECT app.integration_source_connection_save(%L, %s, %L, %L)', repeat('c', 64), nores, 'FILE', '{}'));
  PERFORM pg_temp.expect_fail('C08 unknown source system', format('SELECT app.integration_source_connection_save(%L, %s, %L, %L)', tok, -1, 'FILE', '{}'));
  PERFORM pg_temp.expect_fail('C09 update with a stale row_version', format('SELECT app.integration_source_connection_save(%L, %s, %L, %L, 7)', tok, src, 'DATABASE', '{}'));
  PERFORM pg_temp.expect_fail('C10 kind cannot change', format('SELECT app.integration_source_connection_save(%L, %s, %L, %L, 1)', tok, src, 'FILE', '{}'));
  v := app.integration_source_connection_save(tok, src, 'DATABASE', '{"host": "db2.local", "auth_type": "basic"}', 1);
  PERFORM pg_temp.ok('C11 update: version 2, non-secret auth_type allowed', v = 2
    AND (SELECT connection_config ->> 'auth_type' = 'basic' FROM meta.source_connection WHERE source_system_id = src));
  PERFORM pg_temp.expect_fail('C12 READY without a connection test', format('SELECT app.integration_source_connection_set_status(%L, %s, 2, %L)', tok, src, 'READY'));
  PERFORM pg_temp.expect_fail('C13 direct identity change', format('UPDATE meta.source_connection SET source_kind = %L, row_version = row_version + 1 WHERE source_system_id = %s', 'FILE', src));
  PERFORM pg_temp.expect_fail('C14 deletion', format('DELETE FROM meta.source_connection WHERE source_system_id = %s', src));
  PERFORM pg_temp.expect_fail('C15 TRUNCATE', 'TRUNCATE meta.source_connection');

  -- Credential CREATE.
  PERFORM pg_temp.expect_fail('C16 credential before connection settings', format('SELECT * FROM app.integration_source_secret_begin(%L, %s)', tok, nores));
  SELECT * INTO b FROM app.integration_source_secret_begin(tok, src);
  PERFORM pg_temp.ok('C17 secret_begin: permitted CREATE with server-generated ids',
    b.permitted AND b.operation_type = 'CREATE' AND b.operation_id IS NOT NULL AND b.secret_ref IS NOT NULL);
  PERFORM pg_temp.ok('C18 get shows PENDING; no secret_ref in the API result',
    (SELECT secret_state = 'PENDING' FROM app.integration_source_connection_get(tok, src))
    AND NOT EXISTS (SELECT 1 FROM pg_proc pp, unnest(pp.proargnames) a
                     WHERE pp.oid = 'app.integration_source_connection_get(text,bigint)'::regprocedure AND a ILIKE '%secret_ref%'));
  PERFORM pg_temp.expect_fail('C19 second operation while PENDING', format('SELECT * FROM app.integration_source_secret_begin(%L, %s)', tok, src));
  PERFORM pg_temp.expect_fail('C20 another user cannot finish the operation', format('SELECT app.integration_source_secret_complete(%L, %s, %L, true, %L, %L)', other, src, b.operation_id, repeat('a', 64), 'K2026_01'));
  PERFORM pg_temp.expect_fail('C21 success without key_id', format('SELECT app.integration_source_secret_complete(%L, %s, %L, true, %L, NULL)', tok, src, b.operation_id, repeat('a', 64)));
  PERFORM pg_temp.ok('C22a complete returns ACTIVE', app.integration_source_secret_complete(tok, src, b.operation_id, true, repeat('a', 64), 'K2026_01') = 'ACTIVE');
  PERFORM pg_temp.ok('C22 ACTIVE with key_id, slot SOURCE',
    (SELECT secret_state = 'ACTIVE' AND secret_key_id = 'K2026_01' FROM app.integration_source_connection_get(tok, src))
    AND (SELECT profile_code FROM secret_store.binding WHERE create_operation_id = b.operation_id) = 'SOURCE');

  -- REPLACE, then a failed REPLACE.
  SELECT * INTO b2 FROM app.integration_source_secret_begin(tok, src);
  PERFORM app.integration_source_secret_complete(tok, src, b2.operation_id, true, repeat('b', 64), 'K2026_02');
  PERFORM pg_temp.ok('C23 second credential is a REPLACE; old revoked, new ACTIVE',
    b2.operation_type = 'REPLACE'
    AND (SELECT state FROM secret_store.binding WHERE create_operation_id = b.operation_id) = 'REVOKED'
    AND (SELECT state FROM secret_store.binding WHERE create_operation_id = b2.operation_id) = 'ACTIVE');
  SELECT * INTO b FROM app.integration_source_secret_begin(tok, src);
  PERFORM pg_temp.ok('C24a failed REPLACE returns FAILED', app.integration_source_secret_complete(tok, src, b.operation_id, false) = 'FAILED');
  PERFORM pg_temp.ok('C24 failed REPLACE: PENDING revoked, previous ACTIVE kept',
    (SELECT state FROM secret_store.binding WHERE create_operation_id = b.operation_id) = 'REVOKED'
    AND (SELECT secret_state = 'ACTIVE' AND secret_key_id = 'K2026_02' FROM app.integration_source_connection_get(tok, src)));

  -- REVOKE.
  PERFORM pg_temp.ok('C25a revoke returns true', app.integration_source_secret_revoke(tok, src));
  PERFORM pg_temp.ok('C25 no active credential left', (SELECT secret_state = 'NONE' FROM app.integration_source_connection_get(tok, src)));
  PERFORM pg_temp.expect_fail('C26 revoke without an active credential', format('SELECT app.integration_source_secret_revoke(%L, %s)', tok, src));

  -- Scoped permission: a source without an active resource.
  PERFORM app.integration_source_connection_save(tok, nores, 'DATABASE', '{"host": "x"}');
  SELECT count(*) INTO n FROM secret_store.operation_event WHERE code = 'DENIED_ATTEMPT' AND source_system_id = nores;
  SELECT * INTO b FROM app.integration_source_secret_begin(tok, nores);
  PERFORM pg_temp.ok('C27 no scoped permission: not permitted, no secret_ref, denial journaled and audited',
    NOT b.permitted AND b.secret_ref IS NULL
    AND (SELECT count(*) FROM secret_store.operation_event WHERE code = 'DENIED_ATTEMPT' AND source_system_id = nores) = n + 1
    AND EXISTS (SELECT 1 FROM audit.security_event WHERE event_type = 'INTEGRATION_SOURCE_SECRET_DENIED'
                 AND entity_key = nores::text AND outcome = 'DENIED')
    AND NOT EXISTS (SELECT 1 FROM secret_store.binding WHERE source_system_id = nores));

  -- Disabled connection.
  PERFORM app.integration_source_connection_set_status(tok, fsrc, 1, 'DISABLED');
  PERFORM pg_temp.expect_fail('C28 credential on a DISABLED connection', format('SELECT * FROM app.integration_source_secret_begin(%L, %s)', tok, fsrc));

  -- Audit and grants.
  PERFORM pg_temp.ok('C29 operations audited with the actor, no secret_ref in audit details',
    (SELECT count(*) FROM audit.security_event WHERE event_type LIKE 'INTEGRATION_SOURCE_%') >= 10
    AND NOT EXISTS (SELECT 1 FROM audit.security_event WHERE event_type LIKE 'INTEGRATION_SOURCE_%' AND actor_user_id IS NULL
                     AND event_type <> 'INTEGRATION_SOURCE_SYSTEM_CREATED')
    AND NOT EXISTS (SELECT 1 FROM audit.security_event e, secret_store.binding sb
                     WHERE e.event_type LIKE 'INTEGRATION_SOURCE_%' AND e.details::text LIKE '%' || sb.secret_ref::text || '%'));
  PERFORM pg_temp.ok('C30 dwh_app: EXECUTE on the 6 wrappers only; no table or secret_store access',
    (SELECT count(*) FROM pg_proc pp JOIN pg_namespace ns ON ns.oid = pp.pronamespace
      WHERE ns.nspname = 'app' AND (pp.proname LIKE 'integration\_source\_connection\_%' OR pp.proname LIKE 'integration\_source\_secret\_%')
        AND has_function_privilege('dwh_app', pp.oid, 'EXECUTE')) = 6
    AND NOT has_function_privilege('dwh_app', 'app.integration_source_secret_scope(bigint, bigint, text, uuid)', 'EXECUTE')
    AND NOT has_function_privilege('dwh_app', 'app.integration_source_secret_connection(bigint)', 'EXECUTE')
    AND NOT has_table_privilege('dwh_app', 'meta.source_connection', 'SELECT, INSERT, UPDATE, DELETE')
    AND NOT has_schema_privilege('dwh_app', 'secret_store', 'USAGE'));
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
