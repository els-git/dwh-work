-- I02-04 M-SS03 test: secret_store rules after 001 + 002 + 003.
-- Supersedes the 002 test once 003 is applied. Everything runs in one
-- transaction and is ROLLED BACK; no row survives. For an isolated test DB
-- only (E1), never the working dwh. Requires at least one meta.source_system row.
-- Run: psql -v ON_ERROR_STOP=1 -f 003_secret_store_journal_operations_test.sql
-- Expected last line: ALL_TESTS_PASS (labels: T core, R read, V revoke,
-- P replace, L bindings created before 003).
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

-- Journal row as SQL: (operation, binding, source, type, phase, code, replaced, actor kind).
CREATE FUNCTION pg_temp.ev(op uuid, target uuid, src bigint, typ text, ph text, cd text,
                           replaced uuid DEFAULT NULL, kind text DEFAULT 'DWH_USER') RETURNS text
LANGUAGE sql AS $fn$
  SELECT format('INSERT INTO secret_store.operation_event(operation_id, create_operation_id, source_system_id, '
                'operation_type, phase, code, replaced_create_operation_id, actor_kind, actor_ref) '
                'VALUES (%L, %L, %s, %L, %L, %L, %L, %L, %L)',
                op, target, src, typ, ph, cd, replaced, kind,
                CASE WHEN kind IS NULL THEN NULL WHEN kind = 'SERVICE' THEN 'ETL_WORKER' ELSE '7' END)
$fn$;

CREATE FUNCTION pg_temp.bd(ref uuid, src bigint, op uuid, profile text) RETURNS text
LANGUAGE sql AS $fn$
  SELECT format('INSERT INTO secret_store.binding(secret_ref, source_system_id, create_operation_id, profile_code) '
                'VALUES (%L, %s, %L, %L)', ref, src, op, profile)
$fn$;

CREATE FUNCTION pg_temp.act(ref uuid, key text DEFAULT 'K2026_01') RETURNS text
LANGUAGE sql AS $fn$
  SELECT format('UPDATE secret_store.binding SET state = ''ACTIVE'', activated_at = now(), '
                'ciphertext_sha256 = %L, key_id = %L WHERE secret_ref = %L', repeat('a', 64), key, ref)
$fn$;

CREATE FUNCTION pg_temp.rev(ref uuid) RETURNS text
LANGUAGE sql AS $fn$
  SELECT format('UPDATE secret_store.binding SET state = ''REVOKED'', revoked_at = now() WHERE secret_ref = %L', ref)
$fn$;

-- Full CREATE up to ACTIVE; secret_ref = operation id for brevity.
CREATE FUNCTION pg_temp.create_active(op uuid, src bigint, profile text) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  EXECUTE pg_temp.ev(op, op, src, 'CREATE', 'ATTEMPT', 'PERMITTED_ATTEMPT');
  EXECUTE pg_temp.bd(op, src, op, profile);
  EXECUTE pg_temp.ev(op, op, src, 'CREATE', 'OUTCOME', 'SUCCEEDED');
  EXECUTE pg_temp.act(op);
END
$fn$;

DO $t$
DECLARE
  src bigint := (SELECT min(source_system_id) FROM meta.source_system);
  u uuid[] := ARRAY(SELECT ('00000000-0000-4000-8000-0000000000' || lpad(to_hex(i), 2, '0'))::uuid
                      FROM generate_series(1, 40) AS i);
  o uuid;
  n bigint;
BEGIN
  IF src IS NULL THEN RAISE EXCEPTION 'FAIL setup: meta.source_system is empty'; END IF;

  -- Core CREATE rules.
  PERFORM pg_temp.expect_fail('T01 PENDING without ATTEMPT', pg_temp.bd(u[1], src, u[1], 'MAIN'));
  PERFORM pg_temp.expect_fail('T02 OUTCOME without binding', pg_temp.ev(u[1], u[1], src, 'CREATE', 'OUTCOME', 'SUCCEEDED'));
  PERFORM pg_temp.expect_fail('T03 CREATE with operation <> binding', pg_temp.ev(u[1], u[2], src, 'CREATE', 'ATTEMPT', 'PERMITTED_ATTEMPT'));
  PERFORM pg_temp.expect_fail('T04 code not valid for phase', pg_temp.ev(u[1], u[1], src, 'CREATE', 'ATTEMPT', 'SUCCEEDED'));
  PERFORM pg_temp.expect_fail('T05 event without actor (A05)', pg_temp.ev(u[1], u[1], src, 'CREATE', 'ATTEMPT', 'PERMITTED_ATTEMPT', NULL, NULL));
  PERFORM pg_temp.expect_fail('T06 unknown actor kind', pg_temp.ev(u[1], u[1], src, 'CREATE', 'ATTEMPT', 'PERMITTED_ATTEMPT', NULL, 'ROOT'));
  PERFORM pg_temp.expect_fail('T08 CREATE naming a replaced binding', pg_temp.ev(u[1], u[1], src, 'CREATE', 'ATTEMPT', 'PERMITTED_ATTEMPT', u[2]));
  PERFORM pg_temp.expect_ok('T09 CREATE ATTEMPT', pg_temp.ev(u[1], u[1], src, 'CREATE', 'ATTEMPT', 'PERMITTED_ATTEMPT'));
  PERFORM pg_temp.expect_fail('T10 duplicate ATTEMPT', pg_temp.ev(u[1], u[1], src, 'CREATE', 'ATTEMPT', 'PERMITTED_ATTEMPT'));
  PERFORM pg_temp.expect_ok('T11 PENDING after ATTEMPT', pg_temp.bd(u[1], src, u[1], 'MAIN'));
  PERFORM pg_temp.expect_fail('T07 unknown operation type', pg_temp.ev(u[2], u[1], src, 'EXPORT', 'ATTEMPT', 'PERMITTED_ATTEMPT'));
  PERFORM pg_temp.expect_fail('T12 ACTIVE before SUCCEEDED', pg_temp.act(u[1]));
  PERFORM pg_temp.expect_fail('T13 OUTCOME of another type', pg_temp.ev(u[1], u[1], src, 'REPLACE', 'OUTCOME', 'SUCCEEDED', u[2]));
  PERFORM pg_temp.expect_ok('T14 CREATE OUTCOME SUCCEEDED', pg_temp.ev(u[1], u[1], src, 'CREATE', 'OUTCOME', 'SUCCEEDED'));
  PERFORM pg_temp.expect_fail('T15 ACTIVE without key_id (K08)', pg_temp.act(u[1], NULL));
  PERFORM pg_temp.expect_fail('T16 bad key_id format', pg_temp.act(u[1], 'key with spaces'));
  PERFORM pg_temp.expect_ok('T17 PENDING -> ACTIVE with key_id', pg_temp.act(u[1]));
  PERFORM pg_temp.expect_fail('T18 change key_id', format('UPDATE secret_store.binding SET state = ''REVOKED'', revoked_at = now(), key_id = %L WHERE secret_ref = %L', 'K2027', u[1]));
  PERFORM pg_temp.expect_fail('T19 ACTIVE -> REVOKED without REVOKE outcome', pg_temp.rev(u[1]));
  PERFORM pg_temp.expect_fail('T20 TRUNCATE journal', 'TRUNCATE secret_store.operation_event');
  PERFORM pg_temp.expect_fail('T21 UPDATE journal', format('UPDATE secret_store.operation_event SET code = ''FAILED'' WHERE operation_id = %L', u[1]));
  PERFORM pg_temp.expect_fail('T22 DELETE binding', format('DELETE FROM secret_store.binding WHERE secret_ref = %L', u[1]));

  -- READ (service): only an ACTIVE binding with a profile (S03, E09).
  PERFORM pg_temp.expect_fail('R01 READ with operation = binding', pg_temp.ev(u[1], u[1], src, 'READ', 'ATTEMPT', 'PERMITTED_ATTEMPT', NULL, 'SERVICE'));
  PERFORM pg_temp.expect_ok('R02 READ ATTEMPT on ACTIVE', pg_temp.ev(u[3], u[1], src, 'READ', 'ATTEMPT', 'PERMITTED_ATTEMPT', NULL, 'SERVICE'));
  PERFORM pg_temp.expect_ok('R03 READ OUTCOME', pg_temp.ev(u[3], u[1], src, 'READ', 'OUTCOME', 'SUCCEEDED', NULL, 'SERVICE'));
  PERFORM pg_temp.expect_ok('R04 READ DENIED recorded (A03)', pg_temp.ev(u[4], u[40], src, 'READ', 'ATTEMPT', 'DENIED_ATTEMPT', NULL, 'SERVICE'));
  EXECUTE pg_temp.ev(u[5], u[5], src, 'CREATE', 'ATTEMPT', 'PERMITTED_ATTEMPT');
  EXECUTE pg_temp.bd(u[5], src, u[5], 'OTHER');
  PERFORM pg_temp.expect_fail('R05 READ on PENDING (S03)', pg_temp.ev(u[6], u[5], src, 'READ', 'ATTEMPT', 'PERMITTED_ATTEMPT', NULL, 'SERVICE'));

  -- REVOKE.
  PERFORM pg_temp.expect_fail('V01 REVOKE outcome without attempt', pg_temp.ev(u[7], u[1], src, 'REVOKE', 'OUTCOME', 'SUCCEEDED'));
  PERFORM pg_temp.expect_ok('V02 REVOKE ATTEMPT', pg_temp.ev(u[7], u[1], src, 'REVOKE', 'ATTEMPT', 'PERMITTED_ATTEMPT'));
  PERFORM pg_temp.expect_ok('V03 REVOKE OUTCOME', pg_temp.ev(u[7], u[1], src, 'REVOKE', 'OUTCOME', 'SUCCEEDED'));
  PERFORM pg_temp.expect_ok('V04 ACTIVE -> REVOKED after REVOKE outcome', pg_temp.rev(u[1]));
  PERFORM pg_temp.expect_fail('V05 READ on REVOKED', pg_temp.ev(u[8], u[1], src, 'READ', 'ATTEMPT', 'PERMITTED_ATTEMPT', NULL, 'SERVICE'));
  PERFORM pg_temp.expect_fail('V06 REVOKE of REVOKED', pg_temp.ev(u[9], u[1], src, 'REVOKE', 'ATTEMPT', 'PERMITTED_ATTEMPT'));
  PERFORM pg_temp.expect_fail('V07 PENDING -> REVOKED without outcome', pg_temp.rev(u[5]));
  EXECUTE pg_temp.ev(u[5], u[5], src, 'CREATE', 'OUTCOME', 'FAILED');
  PERFORM pg_temp.expect_ok('V08 PENDING -> REVOKED after own FAILED', pg_temp.rev(u[5]));

  -- REPLACE: source "microservice" with profiles ORDERS and CLIENTS.
  PERFORM pg_temp.create_active(u[10], src, 'ORDERS');
  PERFORM pg_temp.create_active(u[11], src, 'CLIENTS');
  RAISE NOTICE 'PASS P01 two profiles of one source ACTIVE: accepted';
  PERFORM pg_temp.expect_fail('P02 REPLACE of a REVOKED binding', pg_temp.ev(u[12], u[12], src, 'REPLACE', 'ATTEMPT', 'PERMITTED_ATTEMPT', u[1]));
  PERFORM pg_temp.expect_fail('P03 REPLACE without replaced binding', pg_temp.ev(u[12], u[12], src, 'REPLACE', 'ATTEMPT', 'PERMITTED_ATTEMPT'));
  PERFORM pg_temp.expect_ok('P04 REPLACE ATTEMPT for ORDERS', pg_temp.ev(u[12], u[12], src, 'REPLACE', 'ATTEMPT', 'PERMITTED_ATTEMPT', u[10]));
  EXECUTE pg_temp.bd(u[12], src, u[12], 'ORDERS');
  PERFORM pg_temp.expect_fail('P05 revoke old before REPLACE outcome', pg_temp.rev(u[10]));
  PERFORM pg_temp.expect_ok('P06 REPLACE OUTCOME', pg_temp.ev(u[12], u[12], src, 'REPLACE', 'OUTCOME', 'SUCCEEDED', u[10]));
  PERFORM pg_temp.expect_fail('P07 activate new before revoking old', pg_temp.act(u[12]));
  SELECT count(*) INTO n FROM secret_store.binding WHERE secret_ref = u[10] AND state = 'ACTIVE';
  IF n <> 1 THEN RAISE EXCEPTION 'FAIL P08: previous ACTIVE not preserved'; END IF;
  RAISE NOTICE 'PASS P08 failed step keeps previous ACTIVE (S06)';
  PERFORM pg_temp.expect_ok('P09 REPLACE: revoke old, activate new', pg_temp.rev(u[10]) || '; ' || pg_temp.act(u[12], 'K2026_02'));
  SELECT count(*) INTO n FROM secret_store.binding WHERE source_system_id = src AND profile_code = 'ORDERS' AND state = 'ACTIVE';
  IF n <> 1 THEN RAISE EXCEPTION 'FAIL P10: % ACTIVE for ORDERS', n; END IF;
  RAISE NOTICE 'PASS P10 exactly one ACTIVE per profile after REPLACE';
  -- REPLACE into another profile is not allowed.
  EXECUTE pg_temp.ev(u[13], u[13], src, 'REPLACE', 'ATTEMPT', 'PERMITTED_ATTEMPT', u[11]);
  EXECUTE pg_temp.bd(u[13], src, u[13], 'BILLING');
  EXECUTE pg_temp.ev(u[13], u[13], src, 'REPLACE', 'OUTCOME', 'SUCCEEDED', u[11]);
  PERFORM pg_temp.expect_fail('P11 REPLACE into another profile', pg_temp.rev(u[11]) || '; ' || pg_temp.act(u[13]));

  -- Bindings ACTIVE before 003 (no profile, no key_id), as found in dwh.
  SELECT b.create_operation_id INTO o FROM secret_store.binding b
   WHERE b.state = 'ACTIVE' AND b.profile_code IS NULL AND b.key_id IS NULL
   ORDER BY b.created_at LIMIT 1;
  IF o IS NULL THEN
    RAISE NOTICE 'SKIP L01 READ of an ACTIVE binding without profile: none present';
    RAISE NOTICE 'SKIP L02 REVOKE ATTEMPT on an ACTIVE binding without profile: none present';
    RAISE NOTICE 'SKIP L03 REVOKE OUTCOME: none present';
    RAISE NOTICE 'SKIP L04 revoke an ACTIVE binding without profile: none present';
  ELSE
    SELECT source_system_id INTO n FROM secret_store.binding WHERE create_operation_id = o;
    PERFORM pg_temp.expect_fail('L01 READ of an ACTIVE binding without profile', pg_temp.ev(u[20], o, n, 'READ', 'ATTEMPT', 'PERMITTED_ATTEMPT', NULL, 'SERVICE'));
    PERFORM pg_temp.expect_ok('L02 REVOKE ATTEMPT on an ACTIVE binding without profile', pg_temp.ev(u[21], o, n, 'REVOKE', 'ATTEMPT', 'PERMITTED_ATTEMPT'));
    PERFORM pg_temp.expect_ok('L03 REVOKE OUTCOME', pg_temp.ev(u[21], o, n, 'REVOKE', 'OUTCOME', 'SUCCEEDED'));
    PERFORM pg_temp.expect_ok('L04 revoke an ACTIVE binding without profile', format('UPDATE secret_store.binding SET state = ''REVOKED'', revoked_at = now() WHERE create_operation_id = %L', o));
  END IF;
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
