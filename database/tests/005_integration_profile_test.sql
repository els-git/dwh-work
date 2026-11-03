-- I03 test of 005: profile catalog, profile secret operations, grants.
-- Everything runs in one transaction and is ROLLED BACK; no row survives.
-- For E0 (sandbox with fixtures) and E1 (restored copy), never the working dwh.
-- Inside the transaction app.integration_admin_session_user() is replaced by a
-- test version mapping two fake tokens to an existing active admin and to a
-- foreign user id; ROLLBACK restores the real function. Scoped permission,
-- audit and secret_store are the real objects. Needs an active SUPERADMIN/ADMIN
-- user and a source system with an active INTEGRATION_SOURCE_SYSTEM resource.
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 005_integration_profile_test.sql
-- Expected last line: ALL_TESTS_PASS (labels Q01-Q33 and Q21a, Q23a, Q24a: 36 checks).
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
                   AND r.resource_code = 'SOURCE_SYSTEM_' || s.source_system_id AND r.is_active);
  nores bigint;
  p1 bigint; p2 bigint; pd bigint;
  v integer;
  b record;
  b2 record;
  n bigint;
  ev0 bigint := (SELECT count(*) FROM audit.security_event);
BEGIN
  IF src IS NULL THEN RAISE EXCEPTION 'FAIL setup: no source system with an active resource'; END IF;
  INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
  VALUES ('I03_TEST_NO_RESOURCE', 'I03 test source', 'POSTGRESQL', NULL, true, now(), now())
  RETURNING source_system_id INTO nores;

  -- Profile catalog.
  p1 := app.integration_profile_create(tok, src, 'orders', 'database', 'Orders DB', 'test',
          '{"host": "db.local", "port": 5432, "database": "orders", "username": "reader", "options": {"sslmode": "require"}}');
  PERFORM pg_temp.ok('Q01 create profile: code and kind normalised, DRAFT, version 1',
    (SELECT profile_code = 'ORDERS' AND source_kind = 'DATABASE' AND status = 'DRAFT' AND row_version = 1
       FROM meta.integration_profile WHERE profile_id = p1));
  PERFORM pg_temp.expect_fail('Q02 duplicate profile code in the same system', format('SELECT app.integration_profile_create(%L, %s, %L, %L, %L)', tok, src, 'ORDERS', 'API', 'dup'));
  PERFORM pg_temp.expect_fail('Q03 invalid profile code', format('SELECT app.integration_profile_create(%L, %s, %L, %L, %L)', tok, src, '1BAD', 'API', 'x'));
  PERFORM pg_temp.expect_fail('Q04 unknown source kind', format('SELECT app.integration_profile_create(%L, %s, %L, %L, %L)', tok, src, 'K1', 'FTP', 'x'));
  PERFORM pg_temp.expect_fail('Q05 password key in settings', format('SELECT app.integration_profile_create(%L, %s, %L, %L, %L, NULL, %L)', tok, src, 'K2', 'API', 'x', '{"password": "p"}'));
  PERFORM pg_temp.expect_fail('Q06 nested token key in settings', format('SELECT app.integration_profile_create(%L, %s, %L, %L, %L, NULL, %L)', tok, src, 'K3', 'API', 'x', '{"headers": [{"Api-Key": "k"}]}'));
  PERFORM pg_temp.expect_fail('Q07 unknown session', format('SELECT app.integration_profile_create(%L, %s, %L, %L, %L)', repeat('c', 64), src, 'K4', 'API', 'x'));
  PERFORM pg_temp.expect_fail('Q08 unknown source system', format('SELECT app.integration_profile_create(%L, %s, %L, %L, %L)', tok, -1, 'K5', 'API', 'x'));
  PERFORM pg_temp.expect_fail('Q09 update with a stale row_version', format('SELECT app.integration_profile_update(%L, %s, 7, %L, NULL, %L)', tok, p1, 'n', '{}'));
  v := app.integration_profile_update(tok, p1, 1, 'Orders DB v2', NULL, '{"host": "db2.local", "auth_type": "basic"}');
  PERFORM pg_temp.ok('Q10 update: version 2, non-secret auth_type allowed', v = 2
    AND (SELECT connection_config ->> 'auth_type' = 'basic' FROM meta.integration_profile WHERE profile_id = p1));
  PERFORM pg_temp.expect_fail('Q11 READY without a connection test', format('SELECT app.integration_profile_set_status(%L, %s, 2, %L)', tok, p1, 'READY'));
  PERFORM pg_temp.expect_fail('Q12 profile identity is immutable', format('UPDATE meta.integration_profile SET profile_code = %L, row_version = row_version + 1 WHERE profile_id = %s', 'OTHER', p1));
  PERFORM pg_temp.expect_fail('Q13 profile deletion', format('DELETE FROM meta.integration_profile WHERE profile_id = %s', p1));
  PERFORM pg_temp.expect_fail('Q14 TRUNCATE of profiles', 'TRUNCATE meta.integration_profile');
  PERFORM pg_temp.expect_fail('Q15 secret key written directly to the table', format('UPDATE meta.integration_profile SET connection_config = %L, row_version = row_version + 1 WHERE profile_id = %s', '{"secret": "s"}', p1));

  -- Secret CREATE.
  SELECT * INTO b FROM app.integration_profile_secret_begin(tok, p1);
  PERFORM pg_temp.ok('Q16 secret_begin: permitted CREATE with server-generated ids',
    b.permitted AND b.operation_type = 'CREATE' AND b.operation_id IS NOT NULL AND b.secret_ref IS NOT NULL);
  PERFORM pg_temp.ok('Q17 list shows PENDING and has no secret_ref column',
    (SELECT secret_state = 'PENDING' FROM app.integration_profile_list(tok, src) WHERE profile_id = p1)
    AND NOT EXISTS (SELECT 1 FROM pg_proc pp, unnest(pp.proargnames) a
                     WHERE pp.oid = 'app.integration_profile_list(text,bigint)'::regprocedure AND a ILIKE '%secret_ref%'));
  PERFORM pg_temp.expect_fail('Q18 second operation while PENDING', format('SELECT * FROM app.integration_profile_secret_begin(%L, %s)', tok, p1));
  PERFORM pg_temp.expect_fail('Q19 another user cannot finish the operation', format('SELECT app.integration_profile_secret_complete(%L, %s, %L, true, %L, %L)', other, p1, b.operation_id, repeat('a', 64), 'K2026_01'));
  PERFORM pg_temp.expect_fail('Q20 success without key_id', format('SELECT app.integration_profile_secret_complete(%L, %s, %L, true, %L, NULL)', tok, p1, b.operation_id, repeat('a', 64)));
  PERFORM pg_temp.ok('Q21a complete returns ACTIVE', app.integration_profile_secret_complete(tok, p1, b.operation_id, true, repeat('a', 64), 'K2026_01') = 'ACTIVE');
  PERFORM pg_temp.ok('Q21 complete: ACTIVE with key_id',
    (SELECT secret_state = 'ACTIVE' AND secret_key_id = 'K2026_01' FROM app.integration_profile_list(tok, src) WHERE profile_id = p1));

  -- REPLACE, then a failed REPLACE.
  SELECT * INTO b2 FROM app.integration_profile_secret_begin(tok, p1);
  PERFORM app.integration_profile_secret_complete(tok, p1, b2.operation_id, true, repeat('b', 64), 'K2026_02');
  PERFORM pg_temp.ok('Q22 second secret is a REPLACE; old revoked, new ACTIVE',
    b2.operation_type = 'REPLACE'
    AND (SELECT state FROM secret_store.binding WHERE create_operation_id = b.operation_id) = 'REVOKED'
    AND (SELECT state FROM secret_store.binding WHERE create_operation_id = b2.operation_id) = 'ACTIVE');
  SELECT * INTO b FROM app.integration_profile_secret_begin(tok, p1);
  PERFORM pg_temp.ok('Q23a failed REPLACE returns FAILED', app.integration_profile_secret_complete(tok, p1, b.operation_id, false) = 'FAILED');
  PERFORM pg_temp.ok('Q23 failed REPLACE: PENDING revoked, previous ACTIVE kept',
    (SELECT state FROM secret_store.binding WHERE create_operation_id = b.operation_id) = 'REVOKED'
    AND (SELECT secret_state = 'ACTIVE' AND secret_key_id = 'K2026_02' FROM app.integration_profile_list(tok, src) WHERE profile_id = p1));

  -- REVOKE.
  PERFORM pg_temp.ok('Q24a revoke returns true', app.integration_profile_secret_revoke(tok, p1));
  PERFORM pg_temp.ok('Q24 revoke: no active secret left',
    (SELECT secret_state = 'NONE' FROM app.integration_profile_list(tok, src) WHERE profile_id = p1));
  PERFORM pg_temp.expect_fail('Q25 revoke without an active secret', format('SELECT app.integration_profile_secret_revoke(%L, %s)', tok, p1));

  -- Scoped permission: a source system without an active resource.
  p2 := app.integration_profile_create(tok, nores, 'MAIN', 'API', 'No resource');
  SELECT count(*) INTO n FROM secret_store.operation_event WHERE code = 'DENIED_ATTEMPT' AND source_system_id = nores;
  SELECT * INTO b FROM app.integration_profile_secret_begin(tok, p2);
  PERFORM pg_temp.ok('Q26 no scoped permission: not permitted, no secret_ref, denial journaled and audited',
    NOT b.permitted AND b.secret_ref IS NULL
    AND (SELECT count(*) FROM secret_store.operation_event WHERE code = 'DENIED_ATTEMPT' AND source_system_id = nores) = n + 1
    AND EXISTS (SELECT 1 FROM audit.security_event WHERE event_type = 'INTEGRATION_PROFILE_SECRET_DENIED'
                 AND entity_key = p2::text AND outcome = 'DENIED')
    AND NOT EXISTS (SELECT 1 FROM secret_store.binding WHERE source_system_id = nores));

  -- Disabled profile.
  pd := app.integration_profile_create(tok, src, 'DISABLED_ONE', 'FILE', 'Disabled');
  PERFORM app.integration_profile_set_status(tok, pd, 1, 'DISABLED');
  PERFORM pg_temp.expect_fail('Q27 secret on a DISABLED profile', format('SELECT * FROM app.integration_profile_secret_begin(%L, %s)', tok, pd));

  -- Audit trail and secrets in it.
  PERFORM pg_temp.ok('Q28 profile and secret operations audited with the actor',
    (SELECT count(*) FROM audit.security_event WHERE security_event_id > 0 AND event_type LIKE 'INTEGRATION_PROFILE_%') >= 10
    AND (SELECT count(*) FROM audit.security_event WHERE event_type LIKE 'INTEGRATION_PROFILE_%' AND actor_user_id IS NULL) = 0
    AND (SELECT count(*) FROM audit.security_event) > ev0);
  PERFORM pg_temp.ok('Q29 audit details contain no secret_ref',
    NOT EXISTS (SELECT 1 FROM audit.security_event e, secret_store.binding sb
                 WHERE e.event_type LIKE 'INTEGRATION_PROFILE_%' AND e.details::text LIKE '%' || sb.secret_ref::text || '%'));
  PERFORM pg_temp.ok('Q30 secret_store journal rows carry the DWH_USER actor',
    NOT EXISTS (SELECT 1 FROM secret_store.operation_event e JOIN secret_store.binding sb
                    ON sb.create_operation_id = e.create_operation_id
                 WHERE sb.profile_code = 'ORDERS' AND sb.source_system_id = src
                   AND (e.actor_kind IS DISTINCT FROM 'DWH_USER' OR e.actor_ref IS NULL)));

  -- Grants (M-SS05).
  PERFORM pg_temp.ok('Q31 dwh_app can execute the 7 API wrappers',
    (SELECT count(*) FROM pg_proc pp JOIN pg_namespace ns ON ns.oid = pp.pronamespace
      WHERE ns.nspname = 'app' AND pp.proname LIKE 'integration\_profile\_%' AND pp.proname <> 'integration_profile_secret_scope'
        AND has_function_privilege('dwh_app', pp.oid, 'EXECUTE')) = 7);
  PERFORM pg_temp.ok('Q32 dwh_app cannot execute the internal scope check',
    NOT has_function_privilege('dwh_app', 'app.integration_profile_secret_scope(bigint, meta.integration_profile, text, uuid)', 'EXECUTE'));
  PERFORM pg_temp.ok('Q33 dwh_app has no access to the profile table or schema secret_store',
    NOT has_table_privilege('dwh_app', 'meta.integration_profile', 'SELECT, INSERT, UPDATE, DELETE')
    AND NOT has_schema_privilege('dwh_app', 'secret_store', 'USAGE'));
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
