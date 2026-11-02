-- I02-04 M-SS01 test: behaviour of secret_store guards. Everything runs in one
-- transaction and is ROLLED BACK; no row survives. For an isolated test DB only
-- (E1), never the working dwh. Requires at least one meta.source_system row.
-- Run: psql -v ON_ERROR_STOP=1 -f 001_secret_store_baseline_test.sql
-- Expected last line: ALL_TESTS_PASS. "GAP" notices are known review findings.
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
  op1 uuid := '00000000-0000-4000-8000-000000000001';
  op2 uuid := '00000000-0000-4000-8000-000000000002';
  ref1 uuid := '00000000-0000-4000-8000-0000000000a1';
  ref2 uuid := '00000000-0000-4000-8000-0000000000a2';
  dig text := repeat('a', 64);
  ev text := 'INSERT INTO secret_store.operation_event(operation_id, create_operation_id, source_system_id, phase, code) VALUES (%L, %L, %s, %L, %L)';
  bd text := 'INSERT INTO secret_store.binding(secret_ref, source_system_id, create_operation_id) VALUES (%L, %s, %L)';
  n bigint;
  o uuid;
BEGIN
  IF src IS NULL THEN RAISE EXCEPTION 'FAIL setup: meta.source_system is empty'; END IF;

  PERFORM pg_temp.expect_fail('T01 PENDING without ATTEMPT', format(bd, ref1, src, op1));
  PERFORM pg_temp.expect_fail('T02 OUTCOME without binding', format(ev, op1, op1, src, 'OUTCOME', 'SUCCEEDED'));
  PERFORM pg_temp.expect_fail('T03 correlation mismatch', format(ev, op1, op2, src, 'ATTEMPT', 'PERMITTED_ATTEMPT'));
  PERFORM pg_temp.expect_fail('T04 code not valid for phase', format(ev, op1, op1, src, 'ATTEMPT', 'SUCCEEDED'));
  PERFORM pg_temp.expect_ok('T05 PERMITTED_ATTEMPT', format(ev, op1, op1, src, 'ATTEMPT', 'PERMITTED_ATTEMPT'));
  PERFORM pg_temp.expect_fail('T06 duplicate ATTEMPT', format(ev, op1, op1, src, 'ATTEMPT', 'PERMITTED_ATTEMPT'));
  PERFORM pg_temp.expect_ok('T07 PENDING after ATTEMPT', format(bd, ref1, src, op1));
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
  PERFORM pg_temp.expect_fail('T22 PENDING after DENIED', format(bd, ref2, src, op2));
  PERFORM pg_temp.expect_ok('T23 ACTIVE -> REVOKED', format('UPDATE secret_store.binding SET state = ''REVOKED'', revoked_at = now() WHERE secret_ref = %L', ref1));
  PERFORM pg_temp.expect_fail('T24 REVOKED -> ACTIVE', format('UPDATE secret_store.binding SET state = ''ACTIVE'', revoked_at = NULL WHERE secret_ref = %L', ref1));

  -- Known review findings: reported, not failed. See the I02-04 plan, section 4.
  FOREACH o IN ARRAY ARRAY[op3, op4] LOOP
    EXECUTE format(ev, o, o, src, 'ATTEMPT', 'PERMITTED_ATTEMPT');
    EXECUTE format(bd, o, src, o);
    EXECUTE format(ev, o, o, src, 'OUTCOME', 'SUCCEEDED');
    EXECUTE format('UPDATE secret_store.binding SET state = ''ACTIVE'', activated_at = now(), ciphertext_sha256 = %L WHERE secret_ref = %L', dig, o);
  END LOOP;
  SELECT count(*) INTO n FROM secret_store.binding WHERE source_system_id = src AND state = 'ACTIVE';
  RAISE NOTICE 'GAP G2: % ACTIVE bindings for one source are accepted', n;

  BEGIN
    EXECUTE 'TRUNCATE secret_store.operation_event, secret_store.binding';
    SELECT count(*) INTO n FROM secret_store.operation_event;
    RAISE NOTICE 'GAP G1: TRUNCATE bypasses append-only triggers (rows after TRUNCATE: %)', n;
  EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'INFO G1: TRUNCATE rejected (%)', SQLERRM;
  END;
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
