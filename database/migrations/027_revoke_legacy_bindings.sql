-- I02-06: audited revocation of the old secret_store bindings without profile (G8) -- package 027
-- (plan docs/DWH_I02_06_G8_REVOKE_PLAN_APPROVED_20261120.md, decisions R1-R4 = A, 20.11.2026).
--
-- Bindings created before package 002 (28-29.10.2026, isolated I02-03 contour of October) have no profile and no
-- key_id. No reader uses them (API and ETL read only the profile SOURCE) and they have no file on the server.
-- R1: every such binding that is not REVOKED yet (ACTIVE and PENDING) is revoked.
-- R2: through the write functions of package 004 only (one audited path): REVOKE ATTEMPT, then OUTCOME SUCCEEDED,
--     which revokes the binding; the 001-003 triggers check every rule underneath.
-- R3: actor DWH_USER / I02-06.package-027; audit event SECRET_STORE_LEGACY_REVOKE per binding.
-- R4: files of revoked bindings are not touched (there are none for these bindings).
-- Irreversible by design: the rollback script removes only the marker function; the data come back only from
-- the copy pre-027. Running again after the rollback revokes nothing (no binding without profile is left).
-- Run as the owner of the secret_store functions (dwh_admin): psql -1 -v ON_ERROR_STOP=1 -f 027_revoke_legacy_bindings.sql
DO $pre$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regprocedure('secret_store.legacy_bindings_revoked()') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 027 is already applied';
  END IF;
  IF to_regprocedure('secret_store.begin_operation(text, bigint, text, text, uuid, uuid)') IS NULL
     OR to_regprocedure('secret_store.finish_operation(uuid, boolean)') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 004 is not applied';
  END IF;
  IF NOT pg_has_role(current_user, (SELECT p.proowner FROM pg_proc p
                                      WHERE p.oid = 'secret_store.begin_operation(text, bigint, text, text, uuid, uuid)'::regprocedure), 'USAGE') THEN
    RAISE EXCEPTION 'STOP: run as the owner of the secret_store functions';
  END IF;
END
$pre$;

DO $revoke$
DECLARE
  b record;
  v_op uuid;
  v_state text;
  v_count integer := 0;
BEGIN
  SET LOCAL search_path = pg_catalog;
  PERFORM set_config('dwh.security_source', 'DATABASE', true);
  FOR b IN
    SELECT x.secret_ref, x.source_system_id, x.create_operation_id, x.state, x.created_at
      FROM secret_store.binding x
     WHERE x.profile_code IS NULL AND x.state <> 'REVOKED'
     ORDER BY x.created_at
     FOR UPDATE
  LOOP
    v_op := secret_store.begin_operation('REVOKE', b.source_system_id, 'DWH_USER', 'I02-06.package-027',
                                         b.create_operation_id, NULL);
    PERFORM secret_store.finish_operation(v_op, true);
    SELECT x.state INTO v_state FROM secret_store.binding x WHERE x.secret_ref = b.secret_ref;
    IF v_state IS DISTINCT FROM 'REVOKED' THEN
      RAISE EXCEPTION 'STOP: binding % is % after REVOKE', left(b.secret_ref::text, 8), v_state;
    END IF;
    PERFORM audit.log_security_event('SECRET_STORE_LEGACY_REVOKE', 'SOURCE_SYSTEM', b.source_system_id::text,
      'REVOKE', 'SUCCESS', jsonb_build_object('operation_id', v_op, 'create_operation_id', b.create_operation_id,
        'secret_ref', b.secret_ref, 'previous_state', b.state, 'created_at', b.created_at,
        'actor_ref', 'I02-06.package-027', 'decision', 'I02-06 R1-R4 = A, 20.11.2026'));
    v_count := v_count + 1;
  END LOOP;
  RAISE NOTICE 'LEGACY_BINDINGS_REVOKED %', v_count;
END
$revoke$;

-- marker of the package (nothing calls it)
CREATE FUNCTION secret_store.legacy_bindings_revoked() RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $fn$ SELECT true $fn$;
REVOKE ALL ON FUNCTION secret_store.legacy_bindings_revoked() FROM PUBLIC;

-- result line on stdout (dwh_sql_package.py looks for it there)
SELECT 'MIGRATION_027_DONE' AS result;
