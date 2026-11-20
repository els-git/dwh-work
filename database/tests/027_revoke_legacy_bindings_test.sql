-- I02-06 test of 027, run after the package (E0 sandbox, E1 copy): the state it left. Only reads (plus temporary
-- helper functions) in a transaction that is rolled back. Run as the owner: psql -v ON_ERROR_STOP=1 -f <this file>
-- Expected last line: ALL_TESTS_PASS (labels L01-L06).
BEGIN;

CREATE FUNCTION pg_temp.ok(label text, cond boolean) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  IF cond IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', label; END IF;
  RAISE NOTICE 'PASS %', label;
END
$fn$;

DO $t$
DECLARE
  v_rev integer;
BEGIN
  SELECT count(*) INTO v_rev FROM secret_store.operation_event a
   WHERE a.operation_type = 'REVOKE' AND a.phase = 'ATTEMPT' AND a.actor_ref = 'I02-06.package-027';
  PERFORM pg_temp.ok('L01 no ACTIVE or PENDING binding without profile',
    NOT EXISTS (SELECT 1 FROM secret_store.binding WHERE profile_code IS NULL AND state <> 'REVOKED'));
  PERFORM pg_temp.ok('L02 each revocation of 027: ATTEMPT PERMITTED and OUTCOME SUCCEEDED (' || v_rev || ')',
    NOT EXISTS (SELECT 1 FROM secret_store.operation_event a
                 WHERE a.operation_type = 'REVOKE' AND a.phase = 'ATTEMPT' AND a.actor_ref = 'I02-06.package-027'
                   AND (a.code <> 'PERMITTED_ATTEMPT' OR NOT EXISTS (
                        SELECT 1 FROM secret_store.operation_event o
                         WHERE o.operation_id = a.operation_id AND o.phase = 'OUTCOME' AND o.code = 'SUCCEEDED'))));
  PERFORM pg_temp.ok('L03 the revoked bindings are those without profile, with revoked_at',
    NOT EXISTS (SELECT 1 FROM secret_store.operation_event a JOIN secret_store.binding b
                    ON b.create_operation_id = a.create_operation_id
                 WHERE a.actor_ref = 'I02-06.package-027'
                   AND (b.profile_code IS NOT NULL OR b.state <> 'REVOKED' OR b.revoked_at IS NULL)));
  PERFORM pg_temp.ok('L04 bindings with a profile still have at most one ACTIVE per source/profile',
    NOT EXISTS (SELECT 1 FROM secret_store.binding WHERE state = 'ACTIVE' AND profile_code IS NOT NULL
                 GROUP BY source_system_id, profile_code HAVING count(*) > 1));
  PERFORM pg_temp.ok('L05 one audit event per revocation, with the binding and the decision',
    (SELECT count(*) FROM audit.security_event e WHERE e.event_type = 'SECRET_STORE_LEGACY_REVOKE'
       AND e.details ->> 'actor_ref' = 'I02-06.package-027' AND e.details ? 'create_operation_id') = v_rev);
  PERFORM pg_temp.ok('L06 marker present',
    to_regprocedure('secret_store.legacy_bindings_revoked()') IS NOT NULL);
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
