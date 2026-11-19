-- I20 test of 024: domain user lookup and objectGUID binding. One transaction, ROLLED BACK. For E0 (sandbox with
-- fixture) and E1 (restored copy), never the working dwh. Test users have ids 900101-900105; the calls run as
-- dwh_app (SET ROLE), as the API does.
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 024_auth_directory_login_test.sql
-- Expected last line: ALL_TESTS_PASS (labels A01-A14: 14 checks).
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
  IF EXISTS (SELECT 1 FROM app.user_account WHERE user_id BETWEEN 900101 AND 900105
              OR lower(username) LIKE 'i20\_test\_%') THEN
    RAISE EXCEPTION 'FAIL setup: test users 900101-900105 / i20_test_* are taken';
  END IF;
END
$setup$;

-- user_id is GENERATED ALWAYS in dwh: fixed test ids need OVERRIDING SYSTEM VALUE (the sequence is not used).
INSERT INTO app.user_account (user_id, username, display_name, account_level, auth_provider, password_hash, is_active)
OVERRIDING SYSTEM VALUE
VALUES (900101, 'i20_test_ldap', 'LDAP user', 'USER', 'LDAP', NULL, true),
       (900102, 'i20_test_ad', 'AD user', 'USER', 'AD', NULL, true),
       (900103, 'i20_test_local', 'Local user', 'USER', 'LOCAL', 'x', true),
       (900104, 'i20_test_off', 'Inactive LDAP', 'USER', 'LDAP', NULL, false),
       (900105, 'i20_test_other', 'Other LDAP', 'USER', 'LDAP', NULL, true);
SELECT count(*) AS events_before FROM audit.security_event \gset

SET LOCAL ROLE dwh_app;
SELECT pg_temp.ok('A01 domain user found without case and spaces',
  (SELECT user_id FROM app.auth_get_directory_user('  I20_Test_LDAP ')) = 900101);
SELECT pg_temp.ok('A02 AD login found too', (SELECT auth_provider FROM app.auth_get_directory_user('i20_test_ad')) = 'AD');
SELECT pg_temp.ok('A03 LOCAL user is not a domain user', NOT EXISTS (SELECT 1 FROM app.auth_get_directory_user('i20_test_local')));
SELECT pg_temp.ok('A04 inactive domain user is returned with is_active = false',
  (SELECT is_active FROM app.auth_get_directory_user('i20_test_off')) = false);
SELECT pg_temp.ok('A05 domain user is not a local user', NOT EXISTS (SELECT 1 FROM app.auth_get_local_user('i20_test_ldap')));
-- Result and stored value are read in separate statements: a STABLE function sees the snapshot of its statement.
SELECT app.auth_bind_external_subject(900101, '0A1B2C3D-0000-4000-8000-00000000AAAA') AS bind_a06 \gset
SELECT pg_temp.ok('A06 first login stores the GUID (upper case given, lower case stored)',
  :'bind_a06' = 'BOUND'
  AND (SELECT external_subject FROM app.auth_get_directory_user('i20_test_ldap')) = '0a1b2c3d-0000-4000-8000-00000000aaaa');
SELECT pg_temp.ok('A07 same GUID again: MATCH', app.auth_bind_external_subject(900101, '0a1b2c3d-0000-4000-8000-00000000aaaa') = 'MATCH');
SELECT app.auth_bind_external_subject(900101, '11111111-2222-4333-8444-555555555555') AS bind_a08 \gset
SELECT pg_temp.ok('A08 other GUID: MISMATCH, stored GUID kept',
  :'bind_a08' = 'MISMATCH'
  AND (SELECT external_subject FROM app.auth_get_directory_user('i20_test_ldap')) = '0a1b2c3d-0000-4000-8000-00000000aaaa');
SELECT app.auth_bind_external_subject(900105, '0a1b2c3d-0000-4000-8000-00000000aaaa') AS bind_a09 \gset
SELECT pg_temp.ok('A09 GUID of another user: IN_USE, nothing stored',
  :'bind_a09' = 'IN_USE'
  AND (SELECT external_subject FROM app.auth_get_directory_user('i20_test_other')) IS NULL);
SELECT pg_temp.ok('A10 LOCAL, inactive and unknown users: NOT_DIRECTORY_USER',
  app.auth_bind_external_subject(900103, '22222222-2222-4333-8444-555555555555') = 'NOT_DIRECTORY_USER'
  AND app.auth_bind_external_subject(900104, '22222222-2222-4333-8444-555555555555') = 'NOT_DIRECTORY_USER'
  AND app.auth_bind_external_subject(-1, '22222222-2222-4333-8444-555555555555') = 'NOT_DIRECTORY_USER');
SELECT pg_temp.expect_fail('A11 malformed GUID refused', $s$SELECT app.auth_bind_external_subject(900102, 'not-a-guid')$s$);
SELECT pg_temp.expect_fail('A12 dwh_app cannot read app.user_account directly', $s$SELECT 1 FROM app.user_account LIMIT 1$s$);
RESET ROLE;

SELECT pg_temp.ok('A13 journal: BOUND, MISMATCH and IN_USE written (3 events), MATCH and refusals not',
  (SELECT count(*) FROM audit.security_event) - :events_before = 3
  AND (SELECT count(*) FROM audit.security_event WHERE event_type = 'DIRECTORY_SUBJECT_BOUND' AND entity_key = '900101') = 1
  AND (SELECT count(*) FROM audit.security_event WHERE event_type = 'LDAP_GUID_MISMATCH'
        AND details->>'result' IN ('MISMATCH', 'IN_USE')) = 2);
SELECT pg_temp.ok('A14 journal holds no GUID',
  NOT EXISTS (SELECT 1 FROM audit.security_event WHERE details::text LIKE '%0a1b2c3d%' OR details::text LIKE '%11111111%'));

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
