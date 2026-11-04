-- I04 rollback of 006: back to the 005 connection functions.
-- Refuses and changes nothing if a connection is READY or has a recorded test
-- (the result would be lost). Snapshots already written to meta.metadata_capture /
-- source_*_snapshot and secret_store READ events stay: they are history.
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 006_source_connection_test_read_snapshot.down.sql
BEGIN;
LOCK TABLE meta.source_connection IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM meta.source_connection WHERE status = 'READY' OR last_test_at IS NOT NULL) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: connection tests recorded';
  END IF;
END
$do$;
DROP FUNCTION app.integration_source_metadata_capture(text, bigint, text, text, jsonb);
DROP FUNCTION app.integration_source_connection_record_test(text, bigint, integer, boolean, text, text);
DROP FUNCTION app.integration_source_secret_read_finish(text, bigint, uuid, boolean);
DROP FUNCTION app.integration_source_secret_read_begin(text, bigint);
DROP FUNCTION app.integration_source_connection_get(text, bigint);

CREATE FUNCTION app.integration_source_connection_get(p_token_hash text, p_source_system_id bigint)
RETURNS TABLE (
  source_system_id bigint, source_kind text, connection_config jsonb, status text, row_version integer,
  created_at timestamptz, updated_at timestamptz,
  secret_state text, secret_key_id text, secret_activated_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, secret_store AS $fn$
BEGIN
  PERFORM app.integration_admin_session_user(p_token_hash);
  RETURN QUERY
  SELECT c.source_system_id, c.source_kind, c.connection_config, c.status, c.row_version,
         c.created_at, c.updated_at, coalesce(b.state, 'NONE'), b.key_id, b.activated_at
    FROM meta.source_connection c
    LEFT JOIN LATERAL (
      SELECT sb.state, sb.key_id, sb.activated_at FROM secret_store.binding sb
       WHERE sb.source_system_id = c.source_system_id AND sb.profile_code = 'SOURCE'
         AND sb.state IN ('ACTIVE', 'PENDING')
       ORDER BY (sb.state = 'ACTIVE') DESC, sb.created_at DESC LIMIT 1) b ON true
   WHERE c.source_system_id = p_source_system_id;
END;
$fn$;

CREATE OR REPLACE FUNCTION app.integration_source_connection_save(
  p_token_hash text, p_source_system_id bigint, p_source_kind text, p_connection_config jsonb,
  p_expected_row_version integer DEFAULT NULL)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  v_kind text := upper(btrim(coalesce(p_source_kind, '')));
  v_version integer;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM meta.source_system s WHERE s.source_system_id = p_source_system_id) THEN
    RAISE EXCEPTION 'Source system not found';
  END IF;
  IF v_kind = 'API' THEN
    RAISE EXCEPTION 'API sources keep connection settings and credentials on their objects';
  END IF;
  IF p_connection_config IS NULL OR meta.integration_config_has_secret_key(p_connection_config) THEN
    RAISE EXCEPTION 'Connection settings must not contain secrets';
  END IF;
  IF p_expected_row_version IS NULL THEN
    INSERT INTO meta.source_connection(source_system_id, source_kind, connection_config, created_by, updated_by)
    VALUES (p_source_system_id, v_kind, p_connection_config, v_actor, v_actor)
    RETURNING row_version INTO v_version;
  ELSE
    -- Any change needs a new connection test, so READY returns to DRAFT.
    UPDATE meta.source_connection
       SET connection_config = p_connection_config,
           status = CASE WHEN status = 'READY' THEN 'DRAFT' ELSE status END,
           row_version = row_version + 1, updated_at = clock_timestamp(), updated_by = v_actor
     WHERE source_system_id = p_source_system_id AND row_version = p_expected_row_version
       AND source_kind = v_kind
    RETURNING row_version INTO v_version;
    IF NOT FOUND THEN RAISE EXCEPTION 'Connection not found, of another kind or changed by another user'; END IF;
  END IF;
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_CONNECTION_SAVED', 'SOURCE_SYSTEM', p_source_system_id::text,
    CASE WHEN v_version = 1 THEN 'CREATE' ELSE 'UPDATE' END, 'SUCCESS',
    jsonb_build_object('source_kind', v_kind, 'row_version', v_version, 'actor_user_id', v_actor));
  RETURN v_version;
END;
$fn$;

CREATE OR REPLACE FUNCTION app.integration_source_connection_set_status(
  p_token_hash text, p_source_system_id bigint, p_expected_row_version integer, p_status text)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  v_status text := upper(btrim(coalesce(p_status, '')));
  v_version integer;
BEGIN
  IF v_status NOT IN ('DRAFT', 'DISABLED') THEN
    RAISE EXCEPTION 'READY requires a successful connection test (available from I04)';
  END IF;
  UPDATE meta.source_connection
     SET status = v_status, row_version = row_version + 1,
         updated_at = clock_timestamp(), updated_by = v_actor
   WHERE source_system_id = p_source_system_id AND row_version = p_expected_row_version
  RETURNING row_version INTO v_version;
  IF NOT FOUND THEN RAISE EXCEPTION 'Connection not found or changed by another user'; END IF;
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_CONNECTION_STATUS', 'SOURCE_SYSTEM', p_source_system_id::text,
    'UPDATE', 'SUCCESS', jsonb_build_object('status', v_status, 'actor_user_id', v_actor));
  RETURN v_version;
END;
$fn$;

ALTER TABLE meta.source_connection DROP CONSTRAINT source_connection_ready_tested;
ALTER TABLE meta.source_connection DROP CONSTRAINT source_connection_test_complete;
ALTER TABLE meta.source_connection
  DROP COLUMN last_test_server_version, DROP COLUMN last_test_by, DROP COLUMN last_test_code,
  DROP COLUMN last_test_ok, DROP COLUMN last_test_at;

REVOKE ALL ON FUNCTION app.integration_source_connection_get(text, bigint) FROM PUBLIC;
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT x.grantee FROM pg_proc p, aclexplode(p.proacl) x
            WHERE p.oid = 'app.integration_source_connection_get(text, bigint)'::regprocedure
              AND x.grantee <> p.proowner
  LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION app.integration_source_connection_get(text, bigint) FROM %s',
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
  END LOOP;
END
$acl$;
GRANT EXECUTE ON FUNCTION app.integration_source_connection_get(text, bigint) TO dwh_app;
COMMIT;
