-- I03 (I03-01 + M-SS05): connection settings and credentials of a source system.
--
-- Model (user decision 03.11.2026, replaces connection profiles):
--   DATABASE / FILE source: connection settings and at most one credential on the
--     source itself, shared by all its groups and objects;
--   API source: nothing but its name; URL, parameters and a credential per object
--     come with API objects (I11), not here.
--
-- meta.source_connection: one row per DATABASE/FILE source.
--   source_kind never changes; connection_config holds only non-secret settings
--   (no password/token/api-key/... keys at any depth).
--   status: DRAFT | READY | DISABLED. READY needs a connection test, which arrives
--   in I04; here only DRAFT <-> DISABLED, and any change returns READY to DRAFT.
-- The credential is a secret_store binding with slot (profile_code) 'SOURCE':
--   one ACTIVE per source (M-SS02), every operation journaled with the actor (M-SS03)
--   through the M-SS04 functions. The secret value never enters PostgreSQL: the API
--   encrypts it into a file between secret_begin (ATTEMPT + PENDING, committed) and
--   secret_complete (OUTCOME + ACTIVE). No function returns secret_ref or the secret.
--
-- app.integration_source_connection_* / app.integration_source_secret_*: SECURITY
--   DEFINER wrappers for the API (role dwh_app) taking the session token hash, like
--   app.integration_source_system_*: app.integration_admin_session_user() checks the
--   session and sets the audit actor, audit.log_security_event() records each change.
--   Credential operations also require app.has_scoped_permission(...,
--   'INTEGRATION_SOURCE_SYSTEM', 'SOURCE_SYSTEM_<id>'); denials are journaled.
-- Grants (M-SS05): dwh_app may EXECUTE the 6 wrappers only.
-- Requires secret_store 001-004. Run as the owner of schemas app, meta, secret_store:
--   psql -1 -v ON_ERROR_STOP=1 -f 005_source_connection.sql

CREATE TABLE meta.source_connection (
  source_system_id bigint PRIMARY KEY REFERENCES meta.source_system(source_system_id) ON DELETE RESTRICT,
  source_kind text NOT NULL CHECK (source_kind IN ('DATABASE', 'FILE')),
  connection_config jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(connection_config) = 'object'),
  status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'READY', 'DISABLED')),
  row_version integer NOT NULL DEFAULT 1 CHECK (row_version >= 1),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  created_by bigint NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by bigint NOT NULL
);
REVOKE ALL ON meta.source_connection FROM PUBLIC;

-- True when a JSON value contains a key that looks like a credential (any depth).
CREATE FUNCTION meta.integration_config_has_secret_key(p jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog AS $fn$
DECLARE
  k text;
  v jsonb;
BEGIN
  IF jsonb_typeof(p) = 'object' THEN
    FOR k, v IN SELECT * FROM jsonb_each(p) LOOP
      IF k ~* '(pass|pwd|secret|token|credential|private[-_]?key|api[-_]?key|access[-_]?key)' THEN RETURN true; END IF;
      IF meta.integration_config_has_secret_key(v) THEN RETURN true; END IF;
    END LOOP;
  ELSIF jsonb_typeof(p) = 'array' THEN
    FOR v IN SELECT * FROM jsonb_array_elements(p) LOOP
      IF meta.integration_config_has_secret_key(v) THEN RETURN true; END IF;
    END LOOP;
  END IF;
  RETURN false;
END;
$fn$;
ALTER TABLE meta.source_connection ADD CONSTRAINT source_connection_config_no_secrets
  CHECK (NOT meta.integration_config_has_secret_key(connection_config));

CREATE FUNCTION meta.source_connection_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, meta AS $fn$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Source connection deletion prohibited; disable it'; END IF;
  IF NEW.source_system_id IS DISTINCT FROM OLD.source_system_id
     OR NEW.source_kind IS DISTINCT FROM OLD.source_kind
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
    RAISE EXCEPTION 'Source connection identity immutable';
  END IF;
  IF NEW.row_version <> OLD.row_version + 1 THEN
    RAISE EXCEPTION 'Source connection row_version must increase by one';
  END IF;
  RETURN NEW;
END;
$fn$;
CREATE TRIGGER source_connection_guard BEFORE UPDATE OR DELETE ON meta.source_connection
FOR EACH ROW EXECUTE FUNCTION meta.source_connection_guard();
CREATE FUNCTION meta.source_connection_reject_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $fn$
BEGIN RAISE EXCEPTION 'TRUNCATE of source_connection prohibited'; END;
$fn$;
CREATE TRIGGER source_connection_truncate_guard BEFORE TRUNCATE ON meta.source_connection
FOR EACH STATEMENT EXECUTE FUNCTION meta.source_connection_reject_truncate();

-- Connection of a source as the API sees it, with the state of its credential.
-- No row when the source has no connection yet (or is an API source).
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

-- Create (p_expected_row_version NULL) or change the connection of a source.
CREATE FUNCTION app.integration_source_connection_save(
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

CREATE FUNCTION app.integration_source_connection_set_status(
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

-- Scoped permission for credential operations; a denial is journaled and audited.
CREATE FUNCTION app.integration_source_secret_scope(
  p_actor bigint, p_source_system_id bigint, p_operation text, p_target uuid)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, audit, secret_store AS $fn$
BEGIN
  IF app.has_scoped_permission(p_actor, 'ADMIN_INTEGRATION_MANAGE', 'INTEGRATION_SOURCE_SYSTEM',
                               'SOURCE_SYSTEM_' || p_source_system_id) IS TRUE THEN
    RETURN true;
  END IF;
  PERFORM secret_store.record_denied_attempt(p_operation, p_source_system_id, 'DWH_USER',
                                             p_actor::text, p_target, NULL);
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_SECRET_DENIED', 'SOURCE_SYSTEM',
    p_source_system_id::text, p_operation, 'DENIED', jsonb_build_object('actor_user_id', p_actor));
  RETURN false;
END;
$fn$;

-- Locks the connection row and checks that the source may hold a credential.
CREATE FUNCTION app.integration_source_secret_connection(p_source_system_id bigint)
RETURNS meta.source_connection
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
DECLARE
  c meta.source_connection;
BEGIN
  SELECT * INTO c FROM meta.source_connection WHERE source_system_id = p_source_system_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Save the connection settings of the source first'; END IF;
  RETURN c;
END;
$fn$;

-- Step 1 of CREATE/REPLACE: ATTEMPT + PENDING. Returns the operation id and the
-- server-generated secret_ref under which the API stores the encrypted file.
-- On denial the journal and audit rows are kept and the result has no secret_ref.
CREATE FUNCTION app.integration_source_secret_begin(p_token_hash text, p_source_system_id bigint)
RETURNS TABLE (operation_id uuid, secret_ref uuid, operation_type text, permitted boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  c meta.source_connection := app.integration_source_secret_connection(p_source_system_id);
  v_active uuid;
  v_type text;
  v_op uuid;
  v_ref uuid := gen_random_uuid();
BEGIN
  IF c.status = 'DISABLED' THEN RAISE EXCEPTION 'Source connection is disabled'; END IF;
  IF EXISTS (SELECT 1 FROM secret_store.binding b WHERE b.source_system_id = p_source_system_id
               AND b.profile_code = 'SOURCE' AND b.state = 'PENDING') THEN
    RAISE EXCEPTION 'Another credential operation for this source is in progress';
  END IF;
  SELECT b.create_operation_id INTO v_active FROM secret_store.binding b
   WHERE b.source_system_id = p_source_system_id AND b.profile_code = 'SOURCE' AND b.state = 'ACTIVE';
  v_type := CASE WHEN v_active IS NULL THEN 'CREATE' ELSE 'REPLACE' END;
  IF NOT app.integration_source_secret_scope(v_actor, p_source_system_id, v_type, NULL) THEN
    RETURN QUERY SELECT NULL::uuid, NULL::uuid, v_type, false;
    RETURN;
  END IF;
  v_op := secret_store.begin_operation(v_type, p_source_system_id, 'DWH_USER', v_actor::text, NULL, v_active);
  PERFORM secret_store.register_pending(v_op, v_ref, 'SOURCE');
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_SECRET_STARTED', 'SOURCE_SYSTEM',
    p_source_system_id::text, v_type, 'SUCCESS', jsonb_build_object('operation_id', v_op, 'actor_user_id', v_actor));
  RETURN QUERY SELECT v_op, v_ref, v_type, true;
END;
$fn$;

-- Step 2: OUTCOME; on success the credential becomes ACTIVE (REPLACE revokes the
-- old one). Only the user who started the operation can finish it.
CREATE FUNCTION app.integration_source_secret_complete(
  p_token_hash text, p_source_system_id bigint, p_operation_id uuid, p_succeeded boolean,
  p_ciphertext_sha256 text DEFAULT NULL, p_key_id text DEFAULT NULL)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  c meta.source_connection := app.integration_source_secret_connection(p_source_system_id);
  a record;
BEGIN
  SELECT e.* INTO a FROM secret_store.operation_event e JOIN secret_store.binding b
      ON b.create_operation_id = e.operation_id
   WHERE e.operation_id = p_operation_id AND e.phase = 'ATTEMPT' AND e.code = 'PERMITTED_ATTEMPT'
     AND e.operation_type IN ('CREATE', 'REPLACE')
     AND b.source_system_id = p_source_system_id AND b.profile_code = 'SOURCE';
  IF NOT FOUND OR a.actor_ref IS DISTINCT FROM v_actor::text THEN
    RAISE EXCEPTION 'Credential operation not found for this source and user';
  END IF;
  PERFORM secret_store.finish_operation(p_operation_id, p_succeeded);
  IF p_succeeded THEN
    PERFORM secret_store.activate_binding(p_operation_id, p_ciphertext_sha256, p_key_id);
  END IF;
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_SECRET_FINISHED', 'SOURCE_SYSTEM',
    p_source_system_id::text, a.operation_type, CASE WHEN p_succeeded THEN 'SUCCESS' ELSE 'FAILURE' END,
    jsonb_build_object('operation_id', p_operation_id, 'key_id', p_key_id, 'actor_user_id', v_actor));
  RETURN CASE WHEN p_succeeded THEN 'ACTIVE' ELSE 'FAILED' END;
END;
$fn$;

-- REVOKE of the source's ACTIVE credential in one call (no file operation needed).
CREATE FUNCTION app.integration_source_secret_revoke(p_token_hash text, p_source_system_id bigint)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  c meta.source_connection := app.integration_source_secret_connection(p_source_system_id);
  v_active uuid;
  v_op uuid;
BEGIN
  SELECT b.create_operation_id INTO v_active FROM secret_store.binding b
   WHERE b.source_system_id = p_source_system_id AND b.profile_code = 'SOURCE' AND b.state = 'ACTIVE';
  IF v_active IS NULL THEN RAISE EXCEPTION 'Source has no active credential'; END IF;
  IF NOT app.integration_source_secret_scope(v_actor, p_source_system_id, 'REVOKE', v_active) THEN
    RETURN false;
  END IF;
  v_op := secret_store.begin_operation('REVOKE', p_source_system_id, 'DWH_USER', v_actor::text, v_active, NULL);
  PERFORM secret_store.finish_operation(v_op, true);
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_SECRET_REVOKED', 'SOURCE_SYSTEM',
    p_source_system_id::text, 'REVOKE', 'SUCCESS', jsonb_build_object('operation_id', v_op, 'actor_user_id', v_actor));
  RETURN true;
END;
$fn$;

REVOKE ALL ON FUNCTION
  meta.integration_config_has_secret_key(jsonb),
  meta.source_connection_guard(),
  meta.source_connection_reject_truncate(),
  app.integration_source_connection_get(text, bigint),
  app.integration_source_connection_save(text, bigint, text, jsonb, integer),
  app.integration_source_connection_set_status(text, bigint, integer, text),
  app.integration_source_secret_scope(bigint, bigint, text, uuid),
  app.integration_source_secret_connection(bigint),
  app.integration_source_secret_begin(text, bigint),
  app.integration_source_secret_complete(text, bigint, uuid, boolean, text, text),
  app.integration_source_secret_revoke(text, bigint)
FROM PUBLIC;

-- M-SS05: the API role may call the session-checked wrappers, nothing else.
GRANT EXECUTE ON FUNCTION
  app.integration_source_connection_get(text, bigint),
  app.integration_source_connection_save(text, bigint, text, jsonb, integer),
  app.integration_source_connection_set_status(text, bigint, integer, text),
  app.integration_source_secret_begin(text, bigint),
  app.integration_source_secret_complete(text, bigint, uuid, boolean, text, text),
  app.integration_source_secret_revoke(text, bigint)
TO dwh_app;
