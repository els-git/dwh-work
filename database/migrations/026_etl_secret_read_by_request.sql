-- I02-03: the ETL reads the password of a source only for a running load request -- package 026
-- (plan docs/DWH_I02_03_SECRETSTORE_ACCEPTANCE_PLAN_APPROVED_20261120.md, decision R2 = A; rules E03-E08, E10).
--
-- Before: etl.source_secret_read_begin(code) (package 007) gave dwh_etl the password of any source by its code.
-- Now:    etl.source_secret_read_begin(code, request_id) -> operation_id, secret_ref, key_id, refusal
--   the password is given only when the request etl.object_run_request (package 015)
--     - is RUNNING (claimed and started by the dispatcher; cancelled or finished requests are refused, E10),
--     - is a request that reads the source (RUN or FULL_RERUN; CONTINUE_ODS does not read the source),
--     - is for an object of this source (E04, E05: source and object come from the trusted request row),
--     - was started at most 15 minutes ago (E07: the load reads the password right after its start),
--     - has not had its password read yet (E08: one read per request, no replay);
--   a refusal is written to the journal as a DENIED READ attempt (A03) and is returned as `refusal`
--   (the function does not raise, so the DENIED row is kept when the caller commits);
--   the rest is as in 007: DISABLED connection and missing ACTIVE credential are errors, ATTEMPT before the
--   file is read, OUTCOME by etl.source_secret_read_finish (unchanged).
-- etl.object_run_request_secret_read keeps which request read which credential (one row per request).
-- EXECUTE of the old one-argument function is revoked from dwh_etl (it stays for the rollback).
-- Nothing of 001-025 changes otherwise. Run as the owner of schemas etl and secret_store (dwh_admin):
--   psql -1 -v ON_ERROR_STOP=1 -f 026_etl_secret_read_by_request.sql
DO $pre$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regprocedure('etl.source_secret_read_begin(text)') IS NULL
     OR to_regprocedure('etl.source_secret_read_finish(uuid, boolean)') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 007 is not applied';
  END IF;
  IF to_regclass('etl.object_run_request') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 015 is not applied';
  END IF;
  IF to_regprocedure('etl.source_secret_read_begin(text, bigint)') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 026 is already applied';
  END IF;
  IF NOT pg_has_role(current_user, (SELECT p.proowner FROM pg_proc p
                                      WHERE p.oid = 'secret_store.begin_operation(text, bigint, text, text, uuid, uuid)'::regprocedure), 'USAGE') THEN
    RAISE EXCEPTION 'STOP: run as the owner of the secret_store functions';
  END IF;
END
$pre$;

CREATE TABLE etl.object_run_request_secret_read (
  request_id bigint PRIMARY KEY REFERENCES etl.object_run_request (request_id) ON DELETE RESTRICT,
  operation_id uuid NOT NULL UNIQUE,
  source_system_id bigint NOT NULL REFERENCES meta.source_system (source_system_id) ON DELETE RESTRICT,
  read_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- rule since 005: default grants of the schema are removed from new objects; only the functions use the table
REVOKE ALL ON etl.object_run_request_secret_read FROM PUBLIC, dwh_app, dwh_etl;

CREATE FUNCTION etl.source_secret_read_begin(p_source_code text, p_request_id bigint)
RETURNS TABLE (operation_id uuid, secret_ref uuid, key_id text, refusal text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, meta, etl, audit, secret_store AS $fn$
DECLARE
  v_actor text := session_user::text;
  v_source bigint;
  c meta.source_connection;
  b secret_store.binding;
  r etl.object_run_request;
  v_object_source bigint;
  v_refusal text;
  v_op uuid;
BEGIN
  SELECT s.source_system_id INTO v_source FROM meta.source_system s WHERE s.code = p_source_code;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source system not found'; END IF;
  SELECT * INTO c FROM meta.source_connection sc WHERE sc.source_system_id = v_source;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source has no connection settings'; END IF;
  IF c.status = 'DISABLED' THEN RAISE EXCEPTION 'Source connection is disabled'; END IF;
  SELECT * INTO b FROM secret_store.binding sb
   WHERE sb.source_system_id = v_source AND sb.profile_code = 'SOURCE' AND sb.state = 'ACTIVE';
  IF NOT FOUND THEN RAISE EXCEPTION 'Source has no active credential'; END IF;
  IF b.key_id IS NULL THEN RAISE EXCEPTION 'Credential has no key id; replace it'; END IF;

  SELECT * INTO r FROM etl.object_run_request q WHERE q.request_id = p_request_id FOR UPDATE;
  IF NOT FOUND THEN
    v_refusal := 'REQUEST_NOT_FOUND';
  ELSE
    SELECT o.source_system_id INTO v_object_source FROM meta.source_object o
     WHERE o.source_object_id = r.source_object_id;
    IF r.status <> 'RUNNING' THEN
      v_refusal := 'REQUEST_NOT_RUNNING';
    ELSIF r.run_kind NOT IN ('RUN', 'FULL_RERUN') THEN
      v_refusal := 'REQUEST_DOES_NOT_READ_SOURCE';
    ELSIF v_object_source IS DISTINCT FROM v_source THEN
      v_refusal := 'REQUEST_OF_OTHER_SOURCE';
    ELSIF r.started_at IS NULL OR r.started_at < clock_timestamp() - interval '15 minutes' THEN
      v_refusal := 'REQUEST_STARTED_TOO_LONG_AGO';
    ELSIF EXISTS (SELECT 1 FROM etl.object_run_request_secret_read x WHERE x.request_id = p_request_id) THEN
      v_refusal := 'CREDENTIAL_ALREADY_READ';
    END IF;
  END IF;

  IF v_refusal IS NOT NULL THEN
    PERFORM secret_store.record_denied_attempt('READ', v_source, 'SERVICE', v_actor, b.create_operation_id, NULL);
    PERFORM set_config('dwh.security_source', 'ETL', true);
    PERFORM audit.log_security_event('ETL_SOURCE_SECRET_READ', 'SOURCE_SYSTEM', v_source::text, 'READ', 'FAILURE',
      jsonb_build_object('request_id', p_request_id, 'refusal', v_refusal, 'actor_role', v_actor));
    RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, v_refusal;
    RETURN;
  END IF;

  v_op := secret_store.begin_operation('READ', v_source, 'SERVICE', v_actor, b.create_operation_id, NULL);
  INSERT INTO etl.object_run_request_secret_read (request_id, operation_id, source_system_id)
  VALUES (p_request_id, v_op, v_source);
  PERFORM set_config('dwh.security_source', 'ETL', true);
  PERFORM audit.log_security_event('ETL_SOURCE_SECRET_READ', 'SOURCE_SYSTEM', v_source::text, 'READ', 'SUCCESS',
    jsonb_build_object('operation_id', v_op, 'key_id', b.key_id, 'actor_role', v_actor, 'request_id', p_request_id));
  RETURN QUERY SELECT v_op, b.secret_ref, b.key_id, NULL::text;
END;
$fn$;

REVOKE ALL ON FUNCTION etl.source_secret_read_begin(text, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION etl.source_secret_read_begin(text, bigint) TO dwh_etl;
REVOKE EXECUTE ON FUNCTION etl.source_secret_read_begin(text) FROM dwh_etl;

DO $post$
BEGIN
  RAISE NOTICE 'MIGRATION_026_DONE: etl.source_secret_read_begin(text, bigint), etl.object_run_request_secret_read';
END
$post$;
