-- I20 test of 025: switch of the login method LOCAL <-> domain. One transaction, ROLLED BACK. For E0 (sandbox
-- with fixtures) and E1 (restored copy), never the working dwh. The actor is the existing superadmin with a test
-- session (inserted and rolled back); test users have ids 900201-900204; calls run as dwh_app, as the API does.
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 025_auth_provider_switch_test.sql
-- Expected last line: ALL_TESTS_PASS (labels P01-P15: 15 checks).
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
GRANT EXECUTE ON FUNCTION pg_temp.expect_fail(text, text), pg_temp.ok(text, boolean) TO dwh_app;

DO $setup$
BEGIN
  IF EXISTS (SELECT 1 FROM app.user_account WHERE user_id BETWEEN 900201 AND 900204
              OR lower(username) LIKE 'i20s\_test\_%') THEN
    RAISE EXCEPTION 'FAIL setup: test users 900201-900204 / i20s_test_* are taken';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM app.user_account WHERE username = 'superadmin' AND is_active) THEN
    RAISE EXCEPTION 'FAIL setup: active superadmin not found';
  END IF;
END
$setup$;

-- user_id is GENERATED ALWAYS in dwh: fixed test ids need OVERRIDING SYSTEM VALUE.
INSERT INTO app.user_account (user_id, username, display_name, account_level, auth_provider, password_hash,
                              external_subject, is_active, is_system, must_change_password)
OVERRIDING SYSTEM VALUE
VALUES (900201, 'i20s_test_local', 'Local', 'USER', 'LOCAL',
        'scrypt_v1$32768$8$1$' || repeat('a', 22) || '$' || repeat('b', 43), NULL, true, false, false),
       (900202, 'i20s_test_domain', 'Domain', 'USER', 'LDAP', NULL, '0a1b2c3d-0000-4000-8000-00000000aaaa',
        true, false, false),
       (900203, 'i20s_test_system', 'System', 'USER', 'LOCAL',
        'scrypt_v1$32768$8$1$' || repeat('c', 22) || '$' || repeat('d', 43), NULL, true, true, false),
       (900204, 'i20s_test_ad', 'AD', 'USER', 'AD', NULL, NULL, true, false, false);
-- Test sessions: the administrator (superadmin) and the two users to switch.
INSERT INTO app.auth_session (user_id, token_hash, expires_at)
SELECT user_id, repeat('a', 64), clock_timestamp() + interval '1 hour' FROM app.user_account WHERE username = 'superadmin';
INSERT INTO app.auth_session (user_id, token_hash, expires_at)
VALUES (900201, repeat('b', 64), clock_timestamp() + interval '1 hour'),
       (900202, repeat('c', 64), clock_timestamp() + interval '1 hour');
SELECT coalesce(max(security_event_id), 0) AS events_max FROM audit.security_event \gset

SET LOCAL ROLE dwh_app;
SELECT pg_temp.expect_fail('P01 malformed session token refused',
  $s$SELECT app.user_catalog_set_auth_provider('xyz', 900201, 'LDAP', NULL)$s$);
SELECT pg_temp.expect_fail('P02 unknown session refused',
  $s$SELECT app.user_catalog_set_auth_provider(repeat('f', 64), 900201, 'LDAP', NULL)$s$);
SELECT pg_temp.expect_fail('P03 domain login with a password hash refused',
  $s$SELECT app.user_catalog_set_auth_provider(repeat('a', 64), 900201, 'LDAP',
     'scrypt_v1$32768$8$1$' || repeat('a', 22) || '$' || repeat('b', 43))$s$);
SELECT pg_temp.expect_fail('P04 unknown provider refused',
  $s$SELECT app.user_catalog_set_auth_provider(repeat('a', 64), 900201, 'OIDC', NULL)$s$);
SELECT pg_temp.ok('P05 LOCAL -> LDAP',
  app.user_catalog_set_auth_provider(repeat('a', 64), 900201, 'LDAP', NULL));
SELECT pg_temp.ok('P06 now a domain user (found by auth_get_directory_user, not by auth_get_local_user)',
  EXISTS (SELECT 1 FROM app.auth_get_directory_user('i20s_test_local'))
  AND NOT EXISTS (SELECT 1 FROM app.auth_get_local_user('i20s_test_local')));
SELECT pg_temp.expect_fail('P07 domain -> LOCAL without a password refused',
  $s$SELECT app.user_catalog_set_auth_provider(repeat('a', 64), 900202, 'LOCAL', NULL)$s$);
SELECT pg_temp.expect_fail('P08 domain -> LOCAL with a malformed hash refused',
  $s$SELECT app.user_catalog_set_auth_provider(repeat('a', 64), 900202, 'LOCAL', 'plain-text')$s$);
SELECT pg_temp.ok('P09 LDAP -> LOCAL with a temporary password',
  app.user_catalog_set_auth_provider(repeat('a', 64), 900202, 'LOCAL',
    'scrypt_v1$32768$8$1$' || repeat('e', 22) || '$' || repeat('f', 43)));
SELECT pg_temp.ok('P10 same kind of login (AD -> LDAP): nothing changes',
  app.user_catalog_set_auth_provider(repeat('a', 64), 900204, 'LDAP', NULL)
  AND (SELECT auth_provider FROM app.auth_get_directory_user('i20s_test_ad')) = 'AD');
SELECT pg_temp.expect_fail('P11 system account refused',
  $s$SELECT app.user_catalog_set_auth_provider(repeat('a', 64), 900203, 'LDAP', NULL)$s$);
SELECT pg_temp.expect_fail('P12 superadmin refused',
  $s$SELECT app.user_catalog_set_auth_provider(repeat('a', 64), (SELECT user_id FROM app.auth_get_local_user('superadmin')), 'LDAP', NULL)$s$);
SELECT pg_temp.expect_fail('P13 unknown user refused',
  $s$SELECT app.user_catalog_set_auth_provider(repeat('a', 64), 999999999, 'LDAP', NULL)$s$);
RESET ROLE;

SELECT pg_temp.ok('P14 rows: LDAP without password and GUID; LOCAL with the new hash, must change it, GUID cleared; sessions ended',
  (SELECT auth_provider = 'LDAP' AND password_hash IS NULL AND NOT must_change_password AND external_subject IS NULL
     FROM app.user_account WHERE user_id = 900201)
  AND (SELECT auth_provider = 'LOCAL' AND password_hash LIKE 'scrypt_v1$%' || repeat('f', 43) AND must_change_password
          AND external_subject IS NULL FROM app.user_account WHERE user_id = 900202)
  AND NOT EXISTS (SELECT 1 FROM app.auth_session WHERE user_id IN (900201, 900202) AND revoked_at IS NULL)
  AND EXISTS (SELECT 1 FROM app.auth_session WHERE token_hash = repeat('a', 64) AND revoked_at IS NULL));
SELECT pg_temp.ok('P15 journal: two AUTH_PROVIDER_CHANGED, without password hashes',
  (SELECT count(*) FROM audit.security_event WHERE security_event_id > :events_max
      AND event_type = 'AUTH_PROVIDER_CHANGED' AND entity_key IN ('900201', '900202')) = 2
  AND NOT EXISTS (SELECT 1 FROM audit.security_event WHERE security_event_id > :events_max
                   AND details::text LIKE '%scrypt_v1%'));

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
