-- I02-04 M-SS02 test: secret_store guards after 001 + 002. Supersedes the 001
-- test once 002 is applied: T01-T24 repeat the baseline checks with a profile,
-- N01-N14 cover TRUNCATE protection and one ACTIVE secret per profile.
-- Everything runs in one transaction and is ROLLED BACK; no row survives.
-- For an isolated test DB only (E1), never the working dwh. Requires at least
-- one meta.source_system row.
-- Run: psql -v ON_ERROR_STOP=1 -f 002_secret_store_hardening_test.sql
-- Expected last line: ALL_TESTS_PASS.
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

CREATE FUNCTION pg_temp.expect_ok(label text, stmt text) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  EXECUTE stmt;
  RAISE NOTICE 'PASS %: accepted', label;
END
$fn$;

DO $t$
DECLARE
  src bigint := (SELECT min(source_system_id) FROM meta.source_system);
  src2 bigint := (SELECT min(source_system_id) FROM meta.source_system WHERE source_system_id > (SELECT min(source_system_id) FROM meta.source_system));
  op3 uuid := '00000000-0000-4000-8000-000000000003';
  op4 uuid := '00000000-0000-4000-8000-000000000004';
  op5 uuid := '00000000-0000-4000-8000-000000000005';
  op6 uuid := '00000000-0000-4000-8000-000000000006';
  op1 uuid := '00000000-0000-4000-8000-000000000001';
  op2 uuid := '00000000-0000-4000-8000-000000000002';
  ref1 uuid := '00000000-0000-4000-8000-0000000000a1';
  ref2 uuid := '00000000-0000-4000-8000-0000000000a2';
  dig text := repeat('a', 64);
  ev text := 'INSERT INTO secret_store.operation_event(operation_id, create_operation_id, source_system_id, phase, code) VALUES (%L, %L, %s, %L, %L)';
  bd text := 'INSERT INTO secret_store.binding(secret_ref, source_system_id, create_operation_id, profile_code) VALUES (%L, %s, %L, %L)';
  act text := 'UPDATE secret_store.binding SET state = ''ACTIVE'', activated_at = now(), ciphertext_sha256 = %L WHERE secret_ref = %L';
  rev text := 'UPDATE secret_store.binding SET state = ''REVOKED'', revoked_at = now() WHERE secret_ref = %L';
  n bigint;
  o uuid;
BEGIN
  IF src IS NULL THEN RAISE EXCEPTION 'FAIL setup: meta.source_system is empty'; END IF;

  PERFORM pg_temp.expect_fail('T01 PENDING without ATTEMPT', format(bd, ref1, src, op1, 'MAIN'));
  PERFORM pg_temp.expect_fail('T02 OUTCOME without binding', format(ev, op1, op1, src, 'OUTCOME', 'SUCCEEDED'));
  PERFORM pg_temp.expect_fail('T03 correlation mismatch', format(ev, op1, op2, src, 'ATTEMPT', 'PERMITTED_ATTEMPT'));
  PERFORM pg_temp.expect_fail('T04 code not valid for phase', format(ev, op1, op1, src, 'ATTEMPT', 'SUCCEEDED'));
  PERFORM pg_temp.expect_ok('T05 PERMITTED_ATTEMPT', format(ev, op1, op1, src, 'ATTEMPT', 'PERMITTED_ATTEMPT'));
  PERFORM pg_temp.expect_fail('T06 duplicate ATTEMPT', format(ev, op1, op1, src, 'ATTEMPT', 'PERMITTED_ATTEMPT'));
  PERFORM pg_temp.expect_ok('T07 PENDING after ATTEMPT', format(bd, ref1, src, op1, 'MAIN'));
  PERFORM pg_temp.expect_fail('T08 INSERT not PENDING', 'INSERT INTO secret_store.binding(secret_ref, source_system_id, create_operation_id, state, activated_at, ciphertext_sha256) VALUES (gen_random_uuid(), ' || src || ', gen_random_uuid(), ''ACTIVE'', now(), ''' || dig || ''')');
  PERFORM pg_temp.expect_fail('T09 ACTIVE before SUCCEEDED', format('UPDATE secret_store.binding SET state = ''ACTIVE'', activated_at = now(), ciphertext_sha256 = %L WHERE secret_ref = %L', dig, ref1));
  PERFORM pg_temp.expect_fail('T10 bad digest format', format('UPDATE secret_store.binding SET ciphertext_sha256 = %L WHERE secret_ref = %L', 'XYZ', ref1));
  PERFORM pg_temp.expect_ok('T11 OUTCOME SUCCEEDED', format(ev, op1, op1, src, 'OUTCOME', 'SUCCEEDED'));
  PERFORM pg_temp.expect_fail('T12 second OUTCOME', format(ev, op1, op1, src, 'OUTCOME', 'FAILED'));
  PERFORM pg_temp.expect_ok('T13 PENDING -> ACTIVE', format('UPDATE secret_store.binding SET state = ''ACTIVE'', activated_at = now(), ciphertext_sha256 = %L WHERE secret_ref = %L', dig, ref1));
  PERFORM pg_temp.expect_fail('T14 ACTIVE -> PENDING', format('UPDATE secret_store.binding SET state = ''PENDING'', activated_at = NULL WHERE secret_ref = %L', ref1));
  PERFORM pg_temp.expect_fail('T15 change secret_ref', format('UPDATE secret_store.binding SET secret_ref = %L WHERE secret_ref = %L', ref2, ref1));
  IF src2 IS NULL THEN
    RAISE NOTICE 'SKIP T16 rebind source (S02): only one meta.source_system row';
  ELSE
    PERFORM pg_temp.expect_fail('T16 rebind source (S02)', format('UPDATE secret_store.binding SET source_system_id = %s WHERE secret_ref = %L', src2, ref1));
  END IF;
  PERFORM pg_temp.expect_fail('T17 change activation digest', format('UPDATE secret_store.binding SET ciphertext_sha256 = %L WHERE secret_ref = %L', repeat('b', 64), ref1));
  PERFORM pg_temp.expect_fail('T18 DELETE binding', format('DELETE FROM secret_store.binding WHERE secret_ref = %L', ref1));
  PERFORM pg_temp.expect_fail('T19 UPDATE journal', format('UPDATE secret_store.operation_event SET code = ''FAILED'' WHERE operation_id = %L', op1));
  PERFORM pg_temp.expect_fail('T20 DELETE journal', format('DELETE FROM secret_store.operation_event WHERE operation_id = %L', op1));
  PERFORM pg_temp.expect_ok('T21 DENIED_ATTEMPT recorded (A03)', format(ev, op2, op2, src, 'ATTEMPT', 'DENIED_ATTEMPT'));
  PERFORM pg_temp.expect_fail('T22 PENDING after DENIED', format(bd, ref2, src, op2, 'MAIN'));
  PERFORM pg_temp.expect_ok('T23 ACTIVE -> REVOKED', format('UPDATE secret_store.binding SET state = ''REVOKED'', revoked_at = now() WHERE secret_ref = %L', ref1));
  PERFORM pg_temp.expect_fail('T24 REVOKED -> ACTIVE', format('UPDATE secret_store.binding SET state = ''ACTIVE'', revoked_at = NULL WHERE secret_ref = %L', ref1));

  -- M-SS02. Helper: committed ATTEMPT -> PENDING -> SUCCEEDED for (op, profile).
  FOREACH o IN ARRAY ARRAY[op3, op4, op5, op6] LOOP
    EXECUTE format(ev, o, o, src, 'ATTEMPT', 'PERMITTED_ATTEMPT');
  END LOOP;
  PERFORM pg_temp.expect_fail('N01 TRUNCATE journal', 'TRUNCATE secret_store.operation_event');
  PERFORM pg_temp.expect_fail('N02 TRUNCATE binding', 'TRUNCATE secret_store.binding');
  PERFORM pg_temp.expect_fail('N03 TRUNCATE both', 'TRUNCATE secret_store.operation_event, secret_store.binding');
  PERFORM pg_temp.expect_fail('N04 bad profile_code format', format(bd, op3, src, op3, 'orders'));
  PERFORM pg_temp.expect_ok('N05 PENDING without profile (legacy shape)', format(bd, op3, src, op3, NULL));
  EXECUTE format(ev, op3, op3, src, 'OUTCOME', 'SUCCEEDED');
  PERFORM pg_temp.expect_fail('N06 ACTIVE without profile', format(act, dig, op3));
  PERFORM pg_temp.expect_fail('N07 set profile on existing row', format('UPDATE secret_store.binding SET profile_code = %L WHERE secret_ref = %L', 'ORDERS', op3));
  PERFORM pg_temp.expect_ok('N08 PENDING without profile can be REVOKED', format(rev, op3));

  -- One source "microservice" with two profiles, each with its own ACTIVE secret.
  EXECUTE format(bd, op4, src, op4, 'ORDERS');
  EXECUTE format(ev, op4, op4, src, 'OUTCOME', 'SUCCEEDED');
  EXECUTE format(act, dig, op4);
  EXECUTE format(bd, op5, src, op5, 'CLIENTS');
  EXECUTE format(ev, op5, op5, src, 'OUTCOME', 'SUCCEEDED');
  PERFORM pg_temp.expect_ok('N09 second profile of the same source ACTIVE', format(act, dig, op5));

  -- REPLACE of ORDERS: new PENDING op6, activate before revoke is rejected (S06),
  -- revoke old then activate new in one transaction succeeds.
  EXECUTE format(bd, op6, src, op6, 'ORDERS');
  EXECUTE format(ev, op6, op6, src, 'OUTCOME', 'SUCCEEDED');
  PERFORM pg_temp.expect_fail('N10 two ACTIVE for one profile', format(act, dig, op6));
  SELECT count(*) INTO n FROM secret_store.binding WHERE secret_ref = op4 AND state = 'ACTIVE';
  IF n <> 1 THEN RAISE EXCEPTION 'FAIL N11: previous ACTIVE was not preserved'; END IF;
  RAISE NOTICE 'PASS N11 failed replacement keeps previous ACTIVE (S06)';
  PERFORM pg_temp.expect_fail('N07b change profile while revoking (allowed transition)',
    format('UPDATE secret_store.binding SET state = ''REVOKED'', revoked_at = now(), profile_code = %L WHERE secret_ref = %L', 'CLIENTS', op4));
  PERFORM pg_temp.expect_ok('N12 REPLACE: revoke old, activate new',
    format(rev, op4) || '; ' || format(act, dig, op6));
  SELECT count(*) INTO n FROM secret_store.binding
   WHERE source_system_id = src AND profile_code = 'ORDERS' AND state = 'ACTIVE';
  IF n <> 1 THEN RAISE EXCEPTION 'FAIL N13: % ACTIVE secrets for ORDERS', n; END IF;
  RAISE NOTICE 'PASS N13 exactly one ACTIVE secret per profile after REPLACE';

  -- Bindings ACTIVE before 002 (no profile) stay valid and can be revoked.
  SELECT secret_ref INTO o FROM secret_store.binding
   WHERE state = 'ACTIVE' AND profile_code IS NULL ORDER BY created_at LIMIT 1;
  IF o IS NULL THEN
    RAISE NOTICE 'SKIP N14 revoke ACTIVE binding without profile: none present';
  ELSE
    PERFORM pg_temp.expect_ok('N14 revoke ACTIVE binding without profile', format(rev, o));
  END IF;
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
