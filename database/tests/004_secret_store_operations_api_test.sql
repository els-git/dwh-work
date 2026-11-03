-- I02-04 M-SS04 test: write functions of secret_store after 001-004.
-- Everything runs in one transaction and is ROLLED BACK; no row survives.
-- For an isolated test DB only (E1), never the working dwh. Requires at least
-- one meta.source_system row. Run as the secret_store owner.
-- Run: psql -v ON_ERROR_STOP=1 -f 004_secret_store_operations_api_test.sql
-- Expected last line: ALL_TESTS_PASS (labels F01-F20).
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

CREATE FUNCTION pg_temp.state(op uuid) RETURNS text
LANGUAGE sql AS $fn$ SELECT state FROM secret_store.binding WHERE create_operation_id = op $fn$;

CREATE FUNCTION pg_temp.events(op uuid) RETURNS text
LANGUAGE sql AS $fn$
  SELECT string_agg(phase || ':' || code, ',' ORDER BY event_id)
    FROM secret_store.operation_event WHERE operation_id = op
$fn$;

DO $t$
DECLARE
  src bigint := (SELECT min(source_system_id) FROM meta.source_system);
  dig text := repeat('a', 64);
  c1 uuid; c2 uuid; f1 uuid; r1 uuid; v1 uuid; v2 uuid; o1 uuid; p1 uuid; p2 uuid; d1 uuid; l1 uuid;
  legacy uuid; lsrc bigint;
BEGIN
  IF src IS NULL THEN RAISE EXCEPTION 'FAIL setup: meta.source_system is empty'; END IF;

  -- CREATE through the functions.
  c1 := secret_store.begin_operation('CREATE', src, 'DWH_USER', '7');
  PERFORM pg_temp.ok('F01 begin_operation returns a server-generated id with ATTEMPT and actor (A01, A05)',
    pg_temp.events(c1) = 'ATTEMPT:PERMITTED_ATTEMPT'
    AND (SELECT actor_kind = 'DWH_USER' AND actor_ref = '7' AND create_operation_id = c1
           FROM secret_store.operation_event WHERE operation_id = c1));
  PERFORM secret_store.register_pending(c1, gen_random_uuid(), 'ORDERS');
  PERFORM pg_temp.ok('F02 register_pending creates PENDING', pg_temp.state(c1) = 'PENDING');
  PERFORM pg_temp.expect_fail('F03 activate before outcome', format('SELECT secret_store.activate_binding(%L, %L, %L)', c1, dig, 'K1'));
  PERFORM secret_store.finish_operation(c1, true);
  PERFORM secret_store.finish_operation(c1, true);
  PERFORM pg_temp.ok('F04 repeating the same outcome is a no-op (A09)', pg_temp.events(c1) = 'ATTEMPT:PERMITTED_ATTEMPT,OUTCOME:SUCCEEDED');
  PERFORM pg_temp.expect_fail('F05 a different outcome after SUCCEEDED', format('SELECT secret_store.finish_operation(%L, false)', c1));
  PERFORM pg_temp.expect_fail('F06 activate without key_id', format('SELECT secret_store.activate_binding(%L, %L, NULL)', c1, dig));
  PERFORM secret_store.activate_binding(c1, dig, 'K1');
  PERFORM pg_temp.ok('F07 activate_binding makes it ACTIVE with key_id',
    (SELECT state = 'ACTIVE' AND key_id = 'K1' FROM secret_store.binding WHERE create_operation_id = c1));

  -- Argument and rule checks.
  PERFORM pg_temp.expect_fail('F08 CREATE with a target binding', format('SELECT secret_store.begin_operation(%L, %s, %L, %L, %L)', 'CREATE', src, 'DWH_USER', '7', c1));
  PERFORM pg_temp.expect_fail('F09 REVOKE without a target binding', format('SELECT secret_store.begin_operation(%L, %s, %L, %L)', 'REVOKE', src, 'DWH_USER', '7'));
  PERFORM pg_temp.expect_fail('F10 operation without actor', format('SELECT secret_store.begin_operation(%L, %s, NULL, NULL)', 'CREATE', src));
  PERFORM pg_temp.expect_fail('F11 second ACTIVE for the same profile', $s$
    DO $x$ DECLARE c uuid; BEGIN
      c := secret_store.begin_operation('CREATE', (SELECT min(source_system_id) FROM meta.source_system), 'DWH_USER', '7');
      PERFORM secret_store.register_pending(c, gen_random_uuid(), 'ORDERS');
      PERFORM secret_store.finish_operation(c, true);
      PERFORM secret_store.activate_binding(c, repeat('b', 64), 'K1');
    END $x$ $s$);

  -- Failed CREATE: the PENDING binding is revoked together with the outcome.
  f1 := secret_store.begin_operation('CREATE', src, 'DWH_USER', '7');
  PERFORM secret_store.register_pending(f1, gen_random_uuid(), 'CLIENTS');
  PERFORM secret_store.finish_operation(f1, false);
  PERFORM pg_temp.ok('F12 FAILED CREATE revokes its PENDING binding', pg_temp.state(f1) = 'REVOKED');

  -- READ by a service.
  r1 := secret_store.begin_operation('READ', src, 'SERVICE', 'ETL_WORKER', c1);
  PERFORM secret_store.finish_operation(r1, true);
  PERFORM pg_temp.ok('F13 READ is journaled and leaves the binding ACTIVE',
    pg_temp.events(r1) = 'ATTEMPT:PERMITTED_ATTEMPT,OUTCOME:SUCCEEDED' AND pg_temp.state(c1) = 'ACTIVE');
  PERFORM pg_temp.expect_fail('F14 register_pending for a READ operation', format('SELECT secret_store.register_pending(%L, gen_random_uuid(), %L)', r1, 'ORDERS'));

  -- REPLACE: the old binding is revoked and the new one activated atomically.
  p1 := secret_store.begin_operation('REPLACE', src, 'DWH_USER', '7', NULL, c1);
  PERFORM secret_store.register_pending(p1, gen_random_uuid(), 'ORDERS');
  PERFORM secret_store.finish_operation(p1, true);
  PERFORM secret_store.activate_binding(p1, dig, 'K2');
  PERFORM pg_temp.ok('F15 REPLACE revokes the old and activates the new binding (S06)',
    pg_temp.state(c1) = 'REVOKED' AND pg_temp.state(p1) = 'ACTIVE');
  -- REPLACE into another profile fails as a whole; the old binding stays ACTIVE.
  p2 := secret_store.begin_operation('REPLACE', src, 'DWH_USER', '7', NULL, p1);
  PERFORM secret_store.register_pending(p2, gen_random_uuid(), 'BILLING');
  PERFORM secret_store.finish_operation(p2, true);
  PERFORM pg_temp.expect_fail('F16 REPLACE into another profile', format('SELECT secret_store.activate_binding(%L, %L, %L)', p2, dig, 'K2'));
  PERFORM pg_temp.ok('F17 failed REPLACE keeps the previous ACTIVE', pg_temp.state(p1) = 'ACTIVE' AND pg_temp.state(p2) = 'PENDING');

  -- REVOKE.
  v1 := secret_store.begin_operation('REVOKE', src, 'DWH_USER', '7', p1);
  PERFORM secret_store.finish_operation(v1, false);
  v2 := secret_store.begin_operation('REVOKE', src, 'DWH_USER', '7', p1);
  PERFORM secret_store.finish_operation(v2, true);
  PERFORM pg_temp.ok('F18 failed REVOKE keeps ACTIVE, succeeded REVOKE revokes',
    pg_temp.events(v1) = 'ATTEMPT:PERMITTED_ATTEMPT,OUTCOME:FAILED' AND pg_temp.state(p1) = 'REVOKED');

  -- Denials are journaled and change nothing.
  d1 := secret_store.record_denied_attempt('READ', src, 'SERVICE', 'ETL_WORKER', p1);
  PERFORM pg_temp.ok('F19 denied attempt is journaled (A03)', pg_temp.events(d1) = 'ATTEMPT:DENIED_ATTEMPT');

  -- G8 path: a binding ACTIVE before 003 (no profile, no key_id) is revoked with audit.
  SELECT create_operation_id, source_system_id INTO legacy, lsrc FROM secret_store.binding
   WHERE state = 'ACTIVE' AND profile_code IS NULL AND key_id IS NULL ORDER BY created_at LIMIT 1;
  IF legacy IS NULL THEN
    RAISE NOTICE 'SKIP F20 audited REVOKE of an ACTIVE binding without profile: none present';
  ELSE
    l1 := secret_store.begin_operation('REVOKE', lsrc, 'DWH_USER', '7', legacy);
    PERFORM secret_store.finish_operation(l1, true);
    PERFORM pg_temp.ok('F20 audited REVOKE of an ACTIVE binding without profile', pg_temp.state(legacy) = 'REVOKED');
  END IF;
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
