-- I04 (step 3): connection test, credential READ and schema snapshot of a source.
--
-- meta.source_connection gets the result of the last connection test
-- (last_test_at/_ok/_code/_by/_server_version). Rules:
--   * saving new settings clears the result and returns READY to DRAFT (as in 005);
--   * a successful test of a DRAFT connection makes it READY, a failed test
--     returns READY to DRAFT; DISABLED stays DISABLED;
--   * set_status may set READY only when the last test succeeded.
-- Credential READ (secret_store operation READ, M-SS03 rules): read_begin journals
--   the ATTEMPT and returns to the API server the secret_ref and key_id of the ACTIVE
--   credential, so that it can decrypt the file; read_finish journals the OUTCOME.
--   secret_ref never leaves the API server (it is not part of any HTTP response).
-- Schema snapshot: one capture per source with ALL its active registered objects,
--   written to the existing meta.metadata_capture / source_object_snapshot /
--   source_column_snapshot (meta.v_latest_source_structure shows the latest
--   successful capture per source, so a capture must cover every active object).
--   Schema and object names come from meta.source_object, not from the caller.
-- All wrappers check the DWH session (app.integration_admin_session_user) and
--   write audit.log_security_event, like package 005.
-- Grants: dwh_app may EXECUTE the new wrappers; default grants on new objects are
--   removed and reported (rule since 005).
-- Requires 001-005. Run as the owner of schemas app, meta, secret_store:
--   psql -1 -v ON_ERROR_STOP=1 -f 006_source_connection_test_read_snapshot.sql

ALTER TABLE meta.source_connection
  ADD COLUMN last_test_at timestamptz,
  ADD COLUMN last_test_ok boolean,
  ADD COLUMN last_test_code text CHECK (last_test_code ~ '^[A-Z][A-Z0-9_]{0,39}$'),
  ADD COLUMN last_test_by bigint,
  ADD COLUMN last_test_server_version text CHECK (length(last_test_server_version) <= 200);
ALTER TABLE meta.source_connection ADD CONSTRAINT source_connection_test_complete CHECK (
  (last_test_at IS NULL AND last_test_ok IS NULL AND last_test_code IS NULL AND last_test_by IS NULL
     AND last_test_server_version IS NULL)
  OR (last_test_at IS NOT NULL AND last_test_ok IS NOT NULL AND last_test_code IS NOT NULL AND last_test_by IS NOT NULL));
ALTER TABLE meta.source_connection ADD CONSTRAINT source_connection_ready_tested
  CHECK (status <> 'READY' OR last_test_ok IS TRUE);

-- get: the result of the last test is added to the output (return type changes).
DROP FUNCTION app.integration_source_connection_get(text, bigint);
CREATE FUNCTION app.integration_source_connection_get(p_token_hash text, p_source_system_id bigint)
RETURNS TABLE (
  source_system_id bigint, source_kind text, connection_config jsonb, status text, row_version integer,
  created_at timestamptz, updated_at timestamptz,
  secret_state text, secret_key_id text, secret_activated_at timestamptz,
  last_test_at timestamptz, last_test_ok boolean, last_test_code text, last_test_server_version text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, secret_store AS $fn$
BEGIN
  PERFORM app.integration_admin_session_user(p_token_hash);
  RETURN QUERY
  SELECT c.source_system_id, c.source_kind, c.connection_config, c.status, c.row_version,
         c.created_at, c.updated_at, coalesce(b.state, 'NONE'), b.key_id, b.activated_at,
         c.last_test_at, c.last_test_ok, c.last_test_code, c.last_test_server_version
    FROM meta.source_connection c
    LEFT JOIN LATERAL (
      SELECT sb.state, sb.key_id, sb.activated_at FROM secret_store.binding sb
       WHERE sb.source_system_id = c.source_system_id AND sb.profile_code = 'SOURCE'
         AND sb.state IN ('ACTIVE', 'PENDING')
       ORDER BY (sb.state = 'ACTIVE') DESC, sb.created_at DESC LIMIT 1) b ON true
   WHERE c.source_system_id = p_source_system_id;
END;
$fn$;

-- save: as in 005, and new settings clear the last test result.
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
           last_test_at = NULL, last_test_ok = NULL, last_test_code = NULL, last_test_by = NULL,
           last_test_server_version = NULL,
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

-- set_status: READY is allowed after a successful test.
CREATE OR REPLACE FUNCTION app.integration_source_connection_set_status(
  p_token_hash text, p_source_system_id bigint, p_expected_row_version integer, p_status text)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  v_status text := upper(btrim(coalesce(p_status, '')));
  v_version integer;
BEGIN
  IF v_status NOT IN ('DRAFT', 'READY', 'DISABLED') THEN RAISE EXCEPTION 'Unknown connection status'; END IF;
  UPDATE meta.source_connection
     SET status = v_status, row_version = row_version + 1,
         updated_at = clock_timestamp(), updated_by = v_actor
   WHERE source_system_id = p_source_system_id AND row_version = p_expected_row_version
     AND (v_status <> 'READY' OR last_test_ok IS TRUE)
  RETURNING row_version INTO v_version;
  IF NOT FOUND THEN
    IF v_status = 'READY' AND EXISTS (SELECT 1 FROM meta.source_connection c
         WHERE c.source_system_id = p_source_system_id AND c.row_version = p_expected_row_version) THEN
      RAISE EXCEPTION 'READY requires a successful connection test';
    END IF;
    RAISE EXCEPTION 'Connection not found or changed by another user';
  END IF;
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_CONNECTION_STATUS', 'SOURCE_SYSTEM', p_source_system_id::text,
    'UPDATE', 'SUCCESS', jsonb_build_object('status', v_status, 'actor_user_id', v_actor));
  RETURN v_version;
END;
$fn$;

-- READ step 1: ATTEMPT; returns what the API server needs to decrypt the credential.
CREATE FUNCTION app.integration_source_secret_read_begin(p_token_hash text, p_source_system_id bigint)
RETURNS TABLE (operation_id uuid, secret_ref uuid, key_id text, row_version integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  c meta.source_connection;
  b secret_store.binding;
  v_op uuid;
BEGIN
  SELECT * INTO c FROM meta.source_connection sc WHERE sc.source_system_id = p_source_system_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Save the connection settings of the source first'; END IF;
  IF c.status = 'DISABLED' THEN RAISE EXCEPTION 'Source connection is disabled'; END IF;
  SELECT * INTO b FROM secret_store.binding sb
   WHERE sb.source_system_id = p_source_system_id AND sb.profile_code = 'SOURCE' AND sb.state = 'ACTIVE';
  IF NOT FOUND THEN RAISE EXCEPTION 'Source has no active credential'; END IF;
  IF b.key_id IS NULL THEN RAISE EXCEPTION 'Credential has no key id; replace it'; END IF;
  v_op := secret_store.begin_operation('READ', p_source_system_id, 'DWH_USER', v_actor::text, b.create_operation_id, NULL);
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_SECRET_READ', 'SOURCE_SYSTEM', p_source_system_id::text,
    'READ', 'SUCCESS', jsonb_build_object('operation_id', v_op, 'key_id', b.key_id, 'actor_user_id', v_actor));
  RETURN QUERY SELECT v_op, b.secret_ref, b.key_id, c.row_version;
END;
$fn$;

-- READ step 2: OUTCOME (the file was decrypted or not). Same user only.
CREATE FUNCTION app.integration_source_secret_read_finish(
  p_token_hash text, p_source_system_id bigint, p_operation_id uuid, p_succeeded boolean)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  a record;
BEGIN
  SELECT e.* INTO a FROM secret_store.operation_event e
   WHERE e.operation_id = p_operation_id AND e.phase = 'ATTEMPT' AND e.code = 'PERMITTED_ATTEMPT'
     AND e.operation_type = 'READ' AND e.source_system_id = p_source_system_id;
  IF NOT FOUND OR a.actor_ref IS DISTINCT FROM v_actor::text THEN
    RAISE EXCEPTION 'Credential read not found for this source and user';
  END IF;
  PERFORM secret_store.finish_operation(p_operation_id, p_succeeded);
  RETURN p_succeeded;
END;
$fn$;

-- Result of a connection test made by the API server. Codes are short and secret-free.
CREATE FUNCTION app.integration_source_connection_record_test(
  p_token_hash text, p_source_system_id bigint, p_expected_row_version integer,
  p_ok boolean, p_code text, p_server_version text DEFAULT NULL)
RETURNS TABLE (status text, row_version integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  v_status text;
  v_version integer;
BEGIN
  IF p_ok IS NULL OR p_code IS NULL OR p_code !~ '^[A-Z][A-Z0-9_]{0,39}$' THEN
    RAISE EXCEPTION 'Invalid connection test result';
  END IF;
  UPDATE meta.source_connection c
     SET last_test_at = clock_timestamp(), last_test_ok = p_ok, last_test_code = p_code,
         last_test_by = v_actor, last_test_server_version = left(p_server_version, 200),
         status = CASE WHEN p_ok AND c.status = 'DRAFT' THEN 'READY'
                       WHEN NOT p_ok AND c.status = 'READY' THEN 'DRAFT'
                       ELSE c.status END,
         row_version = c.row_version + 1, updated_at = clock_timestamp(), updated_by = v_actor
   WHERE c.source_system_id = p_source_system_id AND c.row_version = p_expected_row_version
  RETURNING c.status, c.row_version INTO v_status, v_version;
  IF NOT FOUND THEN RAISE EXCEPTION 'Connection not found or changed by another user'; END IF;
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_CONNECTION_TESTED', 'SOURCE_SYSTEM', p_source_system_id::text,
    'TEST', CASE WHEN p_ok THEN 'SUCCESS' ELSE 'FAILURE' END,
    jsonb_build_object('code', p_code, 'status', v_status, 'actor_user_id', v_actor));
  RETURN QUERY SELECT v_status, v_version;
END;
$fn$;

-- Schema snapshot of a source: every active registered object with its columns.
-- p_objects: [{"source_object_id": 16, "relkind": "r", "object_type": "TABLE",
--   "columns": [{"ordinal_position": 1, "column_name": "id", "formatted_type": "bigint",
--   "udt_name": "int8", "is_nullable": false, "is_primary_key": true, "is_unique_key": false,
--   "is_identity": false, "is_generated": false, "default_expression": null,
--   "column_comment": null}, ...]}, ...]
CREATE FUNCTION app.integration_source_metadata_capture(
  p_token_hash text, p_source_system_id bigint, p_source_database text, p_source_version text, p_objects jsonb)
RETURNS TABLE (metadata_capture_id bigint, objects_captured integer, columns_captured integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  v_capture bigint;
  v_snapshot bigint;
  v_objects integer := 0;
  v_columns integer := 0;
  o jsonb;
  col jsonb;
  so meta.source_object;
BEGIN
  IF jsonb_typeof(p_objects) IS DISTINCT FROM 'array' OR jsonb_array_length(p_objects) = 0 THEN
    RAISE EXCEPTION 'Snapshot must contain objects';
  END IF;
  IF EXISTS (SELECT 1 FROM meta.source_object x WHERE x.source_system_id = p_source_system_id AND x.is_active
               AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p_objects) e
                                WHERE (e ->> 'source_object_id')::bigint = x.source_object_id))
     OR (SELECT count(DISTINCT e ->> 'source_object_id') FROM jsonb_array_elements(p_objects) e)
        <> jsonb_array_length(p_objects) THEN
    RAISE EXCEPTION 'Snapshot must contain every active object of the source exactly once';
  END IF;
  INSERT INTO meta.metadata_capture AS mc (source_system_id, status, started_at, finished_at, source_database,
      source_version, created_by, message)
  VALUES (p_source_system_id, 'RUNNING', clock_timestamp(), NULL, left(p_source_database, 200),
      left(p_source_version, 200), 'dwh_user:' || v_actor, 'Source schema snapshot from integration administration')
  RETURNING mc.metadata_capture_id INTO v_capture;
  FOR o IN SELECT * FROM jsonb_array_elements(p_objects) LOOP
    SELECT * INTO so FROM meta.source_object x
     WHERE x.source_object_id = (o ->> 'source_object_id')::bigint
       AND x.source_system_id = p_source_system_id AND x.is_active;
    IF NOT FOUND THEN RAISE EXCEPTION 'Object is not an active object of this source'; END IF;
    IF jsonb_typeof(o -> 'columns') IS DISTINCT FROM 'array' OR jsonb_array_length(o -> 'columns') = 0 THEN
      RAISE EXCEPTION 'Source object has no columns: %.%', so.source_schema, so.source_object;
    END IF;
    INSERT INTO meta.source_object_snapshot(metadata_capture_id, source_object_id, source_schema, source_object,
        relkind, object_type)
    VALUES (v_capture, so.source_object_id, so.source_schema, so.source_object, o ->> 'relkind', o ->> 'object_type')
    RETURNING object_snapshot_id INTO v_snapshot;
    FOR col IN SELECT * FROM jsonb_array_elements(o -> 'columns') LOOP
      INSERT INTO meta.source_column_snapshot(object_snapshot_id, ordinal_position, column_name, formatted_type,
          udt_name, is_nullable, is_primary_key, is_unique_key, is_identity, is_generated,
          default_expression, column_comment)
      VALUES (v_snapshot, (col ->> 'ordinal_position')::integer, col ->> 'column_name', col ->> 'formatted_type',
          col ->> 'udt_name', (col ->> 'is_nullable')::boolean, coalesce((col ->> 'is_primary_key')::boolean, false),
          coalesce((col ->> 'is_unique_key')::boolean, false), coalesce((col ->> 'is_identity')::boolean, false),
          coalesce((col ->> 'is_generated')::boolean, false), col ->> 'default_expression', col ->> 'column_comment');
      v_columns := v_columns + 1;
    END LOOP;
    v_objects := v_objects + 1;
  END LOOP;
  UPDATE meta.metadata_capture mc
     SET status = 'SUCCESS', finished_at = clock_timestamp(),
         objects_captured = v_objects, columns_captured = v_columns
   WHERE mc.metadata_capture_id = v_capture;
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_METADATA_CAPTURED', 'SOURCE_SYSTEM', p_source_system_id::text,
    'CREATE', 'SUCCESS', jsonb_build_object('metadata_capture_id', v_capture, 'objects', v_objects,
    'columns', v_columns, 'actor_user_id', v_actor));
  RETURN QUERY SELECT v_capture, v_objects, v_columns;
END;
$fn$;

REVOKE ALL ON FUNCTION
  app.integration_source_connection_get(text, bigint),
  app.integration_source_secret_read_begin(text, bigint),
  app.integration_source_secret_read_finish(text, bigint, uuid, boolean),
  app.integration_source_connection_record_test(text, bigint, integer, boolean, text, text),
  app.integration_source_metadata_capture(text, bigint, text, text, jsonb)
FROM PUBLIC;

-- Default privileges (see 005): remove every grant on the new functions except the
-- owner's and report it, then grant only what is intended.
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT 'FUNCTION ' || p.oid::regprocedure::text AS obj, x.grantee
      FROM pg_proc p, aclexplode(p.proacl) x
     WHERE p.oid IN ('app.integration_source_connection_get(text, bigint)'::regprocedure,
                     'app.integration_source_secret_read_begin(text, bigint)'::regprocedure,
                     'app.integration_source_secret_read_finish(text, bigint, uuid, boolean)'::regprocedure,
                     'app.integration_source_connection_record_test(text, bigint, integer, boolean, text, text)'::regprocedure,
                     'app.integration_source_metadata_capture(text, bigint, text, text, jsonb)'::regprocedure)
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
  app.integration_source_connection_get(text, bigint),
  app.integration_source_secret_read_begin(text, bigint),
  app.integration_source_secret_read_finish(text, bigint, uuid, boolean),
  app.integration_source_connection_record_test(text, bigint, integer, boolean, text, text),
  app.integration_source_metadata_capture(text, bigint, text, text, jsonb)
TO dwh_app;
