-- I13-05 test of 029 (E0 sandbox, E1 copy), run after the package as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 029_audit_log_view_test.sql
-- Everything happens in one transaction that is rolled back. Expected last line: ALL_TESTS_PASS (J01-J12).
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
  RETURN SQLERRM;
END
$fn$;

-- test users: the columns that dwh requires (user_id is generated)
INSERT INTO app.user_account (username, display_name, account_level, auth_provider, password_hash, external_subject,
                              is_active, is_system, must_change_password)
VALUES ('i1305_admin', 'Admin', 'ADMIN', 'LOCAL', 'scrypt_v1$32768$8$1$' || repeat('a', 22) || '$' || repeat('b', 43), NULL, true, false, false),
       ('i1305_user', 'User', 'USER', 'LOCAL', 'scrypt_v1$32768$8$1$' || repeat('a', 22) || '$' || repeat('b', 43), NULL, true, false, false),
       ('i1305_newadm', 'New admin', 'ADMIN', 'LOCAL', 'scrypt_v1$32768$8$1$' || repeat('a', 22) || '$' || repeat('b', 43), NULL, true, false, true);
INSERT INTO app.auth_session (user_id, token_hash, expires_at)
SELECT user_id, CASE username WHEN 'i1305_admin' THEN repeat('d', 64) WHEN 'i1305_user' THEN repeat('e', 64)
                              ELSE repeat('f', 64) END, clock_timestamp() + interval '1 hour'
  FROM app.user_account WHERE username IN ('i1305_admin', 'i1305_user', 'i1305_newadm');

-- three events of the test with details that must be masked
INSERT INTO audit.security_event (actor_user_id, actor_username, actor_account_level, event_type, entity_type,
                                  entity_key, action, outcome, source, details)
VALUES (NULL, 'i1305_admin', 'ADMIN', 'I1305_TEST', 'SOURCE_SYSTEM', 'I1305_SRC', 'TEST', 'SUCCESS', 'TEST',
        '{"secret_ref": "11111111-2222-4333-8444-555555555555", "nested": {"password": "pw1", "ok": 1},
          "dsn": "postgresql://u:pw2@h/db", "note": "postgresql://u:pw3@h/db", "must_change_password": true,
          "key_id": "K2026_04", "list": [{"token": "t1"}, "x"]}'),
       (NULL, 'i1305_admin', 'ADMIN', 'I1305_TEST', 'SOURCE_SYSTEM', 'I1305_SRC', 'TEST', 'FAILURE', 'TEST', '{}'),
       (NULL, 'someone_else', 'USER', 'I1305_OTHER', 'USER', 'x', 'TEST', 'SUCCESS', 'TEST', NULL);

DO $t$
DECLARE r jsonb; r2 jsonb; d jsonb; v_views integer; v_before bigint; v_total integer; v_txt text;
BEGIN
  PERFORM pg_temp.ok('J01 no or broken session is refused',
    pg_temp.err($$SELECT app.audit_log_page(NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 10)$$) LIKE '%Invalid authentication session%'
    AND pg_temp.err($$SELECT app.audit_log_page(repeat('0', 64), NULL, NULL, NULL, NULL, NULL, NULL, NULL, 10)$$) LIKE '%Authentication required%');
  PERFORM pg_temp.ok('J02 USER level is refused in all three functions',
    pg_temp.err($$SELECT app.audit_log_page(repeat('e', 64), NULL, NULL, NULL, NULL, NULL, NULL, NULL, 10)$$) LIKE '%Audit log access denied%'
    AND pg_temp.err($$SELECT app.audit_log_filters(repeat('e', 64))$$) LIKE '%Audit log access denied%'
    AND pg_temp.err($$SELECT app.secret_journal_page(repeat('e', 64), NULL, NULL, NULL, NULL, NULL, 10)$$) LIKE '%Audit log access denied%');
  PERFORM pg_temp.ok('J03 ADMIN with a pending password change is refused',
    pg_temp.err($$SELECT app.audit_log_page(repeat('f', 64), NULL, NULL, NULL, NULL, NULL, NULL, NULL, 10)$$) LIKE '%Password change required%');

  SELECT count(*) INTO v_views FROM audit.security_event WHERE event_type = 'AUDIT_LOG_VIEWED';
  r := app.audit_log_page(repeat('d', 64), NULL, NULL, 'I1305_TEST', NULL, NULL, NULL, NULL, 1);
  PERFORM pg_temp.ok('J04 filter by type, newest first, limit and next page marker',
    jsonb_array_length(r -> 'rows') = 1 AND (r ->> 'has_more')::boolean
    AND r -> 'rows' -> 0 ->> 'outcome' = 'FAILURE' AND (r ->> 'next_before_id')::bigint = (r -> 'rows' -> 0 ->> 'id')::bigint);
  r2 := app.audit_log_page(repeat('d', 64), NULL, NULL, 'I1305_TEST', NULL, NULL, NULL, (r ->> 'next_before_id')::bigint, 1);
  d := r2 -> 'rows' -> 0 -> 'details';
  PERFORM pg_temp.ok('J05 second page: the older event, no more pages',
    jsonb_array_length(r2 -> 'rows') = 1 AND NOT (r2 ->> 'has_more')::boolean AND r2 -> 'rows' -> 0 ->> 'outcome' = 'SUCCESS');
  PERFORM pg_temp.ok('J06 secrets masked at every level, flags and key id kept',
    d ->> 'secret_ref' = '***' AND d -> 'nested' ->> 'password' = '***' AND (d -> 'nested' ->> 'ok')::int = 1
    AND d ->> 'dsn' = '***' AND d ->> 'note' = 'postgresql://u:***@h/db' AND (d ->> 'must_change_password')::boolean
    AND d ->> 'key_id' = 'K2026_04' AND d -> 'list' -> 0 ->> 'token' = '***' AND d -> 'list' ->> 1 = 'x'
    AND position('pw1' IN r2::text) = 0 AND position('pw2' IN r2::text) = 0 AND position('pw3' IN r2::text) = 0
    AND position('55555555' IN r2::text) = 0);
  PERFORM pg_temp.ok('J07 only the first page of a request is logged as AUDIT_LOG_VIEWED, with filters and actor',
    (SELECT count(*) FROM audit.security_event WHERE event_type = 'AUDIT_LOG_VIEWED') = v_views + 1
    AND EXISTS (SELECT 1 FROM audit.security_event WHERE event_type = 'AUDIT_LOG_VIEWED' AND actor_username = 'i1305_admin'
                 AND details ->> 'event_type' = 'I1305_TEST' AND source = 'AUDIT_VIEW'));
  r := app.audit_log_page(repeat('d', 64), NULL, NULL, NULL, 'SOMEONE', 'USER', 'SUCCESS', NULL, 50);
  PERFORM pg_temp.ok('J08 filters by actor part, object type and outcome; period excludes the future',
    jsonb_array_length(r -> 'rows') >= 1 AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r -> 'rows') x
                                                           WHERE x ->> 'actor_username' <> 'someone_else')
    AND jsonb_array_length(app.audit_log_page(repeat('d', 64), now() + interval '1 day', NULL, NULL, NULL, NULL, NULL, NULL, 50) -> 'rows') = 0);
  r := app.audit_log_page(repeat('d', 64), NULL, NULL, NULL, NULL, NULL, NULL, NULL, 100000);
  PERFORM pg_temp.ok('J09 page size is at most 200', jsonb_array_length(r -> 'rows') <= 200);
  r := app.audit_log_filters(repeat('d', 64));
  PERFORM pg_temp.ok('J10 filter lists contain the event types and outcomes',
    r -> 'event_types' ? 'I1305_TEST' AND r -> 'outcomes' ? 'FAILURE' AND r -> 'entity_types' ? 'SOURCE_SYSTEM');

  SELECT count(*) INTO v_total FROM secret_store.operation_event;
  SELECT count(*) INTO v_views FROM audit.security_event WHERE event_type = 'SECRET_JOURNAL_VIEWED';
  r := app.secret_journal_page(repeat('d', 64), NULL, NULL, NULL, NULL, NULL, 200);
  v_txt := r::text;
  PERFORM pg_temp.ok('J11 password journal: rows with source code and allowed fields only, view logged',
    jsonb_array_length(r -> 'rows') = least(v_total, 200)
    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r -> 'rows') x, jsonb_object_keys(x) k
                     WHERE k NOT IN ('event_id', 'occurred_at', 'source_system_id', 'source_code', 'source_name',
                                     'operation_type', 'phase', 'code', 'actor_kind', 'actor_ref', 'operation_id'))
    AND (v_total = 0 OR r -> 'rows' -> 0 ->> 'source_code' IS NOT NULL)
    AND position('secret_ref' IN v_txt) = 0 AND position('ciphertext' IN v_txt) = 0
    AND (SELECT count(*) FROM audit.security_event WHERE event_type = 'SECRET_JOURNAL_VIEWED') = v_views + 1);
  IF v_total > 0 THEN
    SELECT source_system_id INTO v_before FROM secret_store.operation_event ORDER BY event_id DESC LIMIT 1;
    r := app.secret_journal_page(repeat('d', 64), v_before, NULL, NULL, 'READ', NULL, 200);
    PERFORM pg_temp.ok('J12 password journal filters by source and operation type',
      NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r -> 'rows') x
                   WHERE (x ->> 'source_system_id')::bigint <> v_before OR x ->> 'operation_type' <> 'READ'));
  ELSE
    PERFORM pg_temp.ok('J12 password journal filters (empty journal)', true);
  END IF;
END
$t$;

ROLLBACK;
SELECT 'ALL_TESTS_PASS' AS result;
