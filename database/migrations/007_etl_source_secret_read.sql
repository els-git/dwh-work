-- I04-05 (step 9): service READ of a source credential for the ETL role.
--
-- The ETL runs without a DWH user session, so the 006 READ functions (which need an
-- administrator session) cannot be used. These two functions let the ETL role
-- read the ACTIVE credential of a source by its code:
--   etl.source_secret_read_begin(code)  -> ATTEMPT (operation READ, actor SERVICE /
--                                          session_user) + operation_id, secret_ref, key_id
--   etl.source_secret_read_finish(op, ok) -> OUTCOME; only the same database role.
-- The actor is the database login role (session_user), never a value from the caller.
-- A DISABLED connection blocks the read (administrator stop switch). The value is
-- decrypted by the ETL process from the encrypted file; it never enters PostgreSQL.
-- Schema etl: the ETL role already works there. EXECUTE only to dwh_etl; default
-- grants on the new functions are removed and reported (rule since 005).
-- Requires 001-006. Run as the owner of schemas etl, meta, secret_store:
--   psql -1 -v ON_ERROR_STOP=1 -f 007_etl_source_secret_read.sql

CREATE FUNCTION etl.source_secret_read_begin(p_source_code text)
RETURNS TABLE (operation_id uuid, secret_ref uuid, key_id text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, meta, audit, secret_store AS $fn$
DECLARE
  v_actor text := session_user::text;
  v_source bigint;
  c meta.source_connection;
  b secret_store.binding;
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
  v_op := secret_store.begin_operation('READ', v_source, 'SERVICE', v_actor, b.create_operation_id, NULL);
  PERFORM set_config('dwh.security_source', 'ETL', true);
  PERFORM audit.log_security_event('ETL_SOURCE_SECRET_READ', 'SOURCE_SYSTEM', v_source::text, 'READ', 'SUCCESS',
    jsonb_build_object('operation_id', v_op, 'key_id', b.key_id, 'actor_role', v_actor));
  RETURN QUERY SELECT v_op, b.secret_ref, b.key_id;
END;
$fn$;

CREATE FUNCTION etl.source_secret_read_finish(p_operation_id uuid, p_succeeded boolean)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, secret_store AS $fn$
DECLARE
  a record;
BEGIN
  SELECT e.* INTO a FROM secret_store.operation_event e
   WHERE e.operation_id = p_operation_id AND e.phase = 'ATTEMPT' AND e.code = 'PERMITTED_ATTEMPT'
     AND e.operation_type = 'READ' AND e.actor_kind = 'SERVICE';
  IF NOT FOUND OR a.actor_ref IS DISTINCT FROM session_user::text THEN
    RAISE EXCEPTION 'Credential read not found for this role';
  END IF;
  PERFORM secret_store.finish_operation(p_operation_id, p_succeeded);
  RETURN p_succeeded;
END;
$fn$;

REVOKE ALL ON FUNCTION
  etl.source_secret_read_begin(text),
  etl.source_secret_read_finish(uuid, boolean)
FROM PUBLIC;

DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT 'FUNCTION ' || p.oid::regprocedure::text AS obj, x.grantee
      FROM pg_proc p, aclexplode(p.proacl) x
     WHERE p.oid IN ('etl.source_secret_read_begin(text)'::regprocedure,
                     'etl.source_secret_read_finish(uuid, boolean)'::regprocedure)
       AND x.grantee <> p.proowner
  LOOP
    EXECUTE format('REVOKE ALL ON %s FROM %s', r.obj,
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED % FROM %', r.obj,
                 CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(r.grantee) END;
  END LOOP;
END
$acl$;

GRANT EXECUTE ON FUNCTION
  etl.source_secret_read_begin(text),
  etl.source_secret_read_finish(uuid, boolean)
TO dwh_etl;
