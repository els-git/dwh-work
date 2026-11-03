-- I03 (I03-01 + M-SS05): connection profile catalog and profile secret operations.
--
-- meta.integration_profile: one source system -> N profiles (approved model I02-02).
--   Stable identity (source_system_id, profile_code, source_kind) never changes.
--   Current non-secret settings live in connection_config until I05 (decision V1).
--   status: DRAFT | READY | DISABLED. READY needs a connection test, which arrives
--   in I04; here only DRAFT <-> DISABLED, and any change returns READY to DRAFT.
--   connection_config must not carry secret-looking keys (password, token, ...).
--
-- app.integration_profile_* : SECURITY DEFINER wrappers called by the API (role
--   dwh_app) with the session token hash, following app.integration_source_system_*:
--   app.integration_admin_session_user() verifies the session (SUPERADMIN/ADMIN with
--   ADMIN_INTEGRATION_MANAGE) and sets the audit actor; every change is written to
--   audit.log_security_event. Secret operations additionally require
--   app.has_scoped_permission(..., 'INTEGRATION_SOURCE_SYSTEM', 'SOURCE_SYSTEM_<id>')
--   and go through the secret_store functions of M-SS04 (actor DWH_USER:<user_id>).
--   A secret value never enters PostgreSQL: the API encrypts it into a file between
--   secret_begin (ATTEMPT + PENDING, committed) and secret_complete (OUTCOME + ACTIVE).
--   No function returns secret_ref, ciphertext or the secret.
--
-- Grants (M-SS05): dwh_app gets EXECUTE on the app.integration_profile_* wrappers
--   only; no access to meta.integration_profile or schema secret_store.
-- Requires secret_store 001-004. Run as the owner of schemas app, meta, secret_store:
--   psql -1 -v ON_ERROR_STOP=1 -f 005_integration_profile.sql

CREATE TABLE meta.integration_profile (
  profile_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_system_id bigint NOT NULL REFERENCES meta.source_system(source_system_id) ON DELETE RESTRICT,
  profile_code text NOT NULL CHECK (profile_code ~ '^[A-Z][A-Z0-9_]{0,63}$'),
  source_kind text NOT NULL CHECK (source_kind IN ('DATABASE', 'API', 'FILE')),
  name text NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 200),
  description text,
  connection_config jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(connection_config) = 'object'),
  status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'READY', 'DISABLED')),
  row_version integer NOT NULL DEFAULT 1 CHECK (row_version >= 1),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  created_by bigint NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by bigint NOT NULL,
  CONSTRAINT integration_profile_code_per_system UNIQUE (source_system_id, profile_code)
);
REVOKE ALL ON meta.integration_profile FROM PUBLIC;

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
ALTER TABLE meta.integration_profile ADD CONSTRAINT integration_profile_config_no_secrets
  CHECK (NOT meta.integration_config_has_secret_key(connection_config));

CREATE FUNCTION meta.integration_profile_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, meta AS $fn$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Profile deletion prohibited; disable it'; END IF;
  IF NEW.profile_id IS DISTINCT FROM OLD.profile_id
     OR NEW.source_system_id IS DISTINCT FROM OLD.source_system_id
     OR NEW.profile_code IS DISTINCT FROM OLD.profile_code
     OR NEW.source_kind IS DISTINCT FROM OLD.source_kind
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
    RAISE EXCEPTION 'Profile identity immutable';
  END IF;
  IF NEW.row_version <> OLD.row_version + 1 THEN
    RAISE EXCEPTION 'Profile row_version must increase by one';
  END IF;
  RETURN NEW;
END;
$fn$;
CREATE TRIGGER integration_profile_guard BEFORE UPDATE OR DELETE ON meta.integration_profile
FOR EACH ROW EXECUTE FUNCTION meta.integration_profile_guard();
CREATE FUNCTION meta.integration_profile_reject_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $fn$
BEGIN RAISE EXCEPTION 'TRUNCATE of integration_profile prohibited'; END;
$fn$;
CREATE TRIGGER integration_profile_truncate_guard BEFORE TRUNCATE ON meta.integration_profile
FOR EACH STATEMENT EXECUTE FUNCTION meta.integration_profile_reject_truncate();

-- Profile row as the API sees it, with the state of its secret (no secret_ref).
CREATE FUNCTION app.integration_profile_list(p_token_hash text, p_source_system_id bigint)
RETURNS TABLE (
  profile_id bigint, source_system_id bigint, profile_code text, source_kind text,
  name text, description text, connection_config jsonb, status text, row_version integer,
  created_at timestamptz, updated_at timestamptz,
  secret_state text, secret_key_id text, secret_activated_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, secret_store AS $fn$
BEGIN
  PERFORM app.integration_admin_session_user(p_token_hash);
  RETURN QUERY
  SELECT p.profile_id, p.source_system_id, p.profile_code, p.source_kind, p.name, p.description,
         p.connection_config, p.status, p.row_version, p.created_at, p.updated_at,
         coalesce(b.state, 'NONE'), b.key_id, b.activated_at
    FROM meta.integration_profile p
    LEFT JOIN LATERAL (
      SELECT sb.state, sb.key_id, sb.activated_at
        FROM secret_store.binding sb
       WHERE sb.source_system_id = p.source_system_id AND sb.profile_code = p.profile_code
         AND sb.state IN ('ACTIVE', 'PENDING')
       ORDER BY (sb.state = 'ACTIVE') DESC, sb.created_at DESC
       LIMIT 1) b ON true
   WHERE p.source_system_id = p_source_system_id
   ORDER BY p.profile_code;
END;
$fn$;

CREATE FUNCTION app.integration_profile_create(
  p_token_hash text, p_source_system_id bigint, p_profile_code text, p_source_kind text,
  p_name text, p_description text DEFAULT NULL, p_connection_config jsonb DEFAULT '{}'::jsonb)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  v_code text := upper(btrim(coalesce(p_profile_code, '')));
  v_kind text := upper(btrim(coalesce(p_source_kind, '')));
  v_id bigint;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM meta.source_system s WHERE s.source_system_id = p_source_system_id) THEN
    RAISE EXCEPTION 'Source system not found';
  END IF;
  IF p_connection_config IS NULL OR meta.integration_config_has_secret_key(p_connection_config) THEN
    RAISE EXCEPTION 'Connection settings must not contain secrets';
  END IF;
  INSERT INTO meta.integration_profile(source_system_id, profile_code, source_kind, name, description,
                                       connection_config, created_by, updated_by)
  VALUES (p_source_system_id, v_code, v_kind, btrim(coalesce(p_name, '')),
          nullif(btrim(coalesce(p_description, '')), ''), p_connection_config, v_actor, v_actor)
  RETURNING profile_id INTO v_id;
  PERFORM audit.log_security_event('INTEGRATION_PROFILE_CREATED', 'INTEGRATION_PROFILE', v_id::text,
    'CREATE', 'SUCCESS', jsonb_build_object('source_system_id', p_source_system_id,
      'profile_code', v_code, 'source_kind', v_kind, 'actor_user_id', v_actor));
  RETURN v_id;
END;
$fn$;

CREATE FUNCTION app.integration_profile_update(
  p_token_hash text, p_profile_id bigint, p_expected_row_version integer,
  p_name text, p_description text, p_connection_config jsonb)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  v_version integer;
BEGIN
  IF p_connection_config IS NULL OR meta.integration_config_has_secret_key(p_connection_config) THEN
    RAISE EXCEPTION 'Connection settings must not contain secrets';
  END IF;
  -- Any change needs a new connection test, so READY returns to DRAFT.
  UPDATE meta.integration_profile
     SET name = btrim(coalesce(p_name, '')), description = nullif(btrim(coalesce(p_description, '')), ''),
         connection_config = p_connection_config,
         status = CASE WHEN status = 'READY' THEN 'DRAFT' ELSE status END,
         row_version = row_version + 1, updated_at = clock_timestamp(), updated_by = v_actor
   WHERE profile_id = p_profile_id AND row_version = p_expected_row_version
  RETURNING row_version INTO v_version;
  IF NOT FOUND THEN RAISE EXCEPTION 'Profile not found or changed by another user'; END IF;
  PERFORM audit.log_security_event('INTEGRATION_PROFILE_UPDATED', 'INTEGRATION_PROFILE', p_profile_id::text,
    'UPDATE', 'SUCCESS', jsonb_build_object('row_version', v_version, 'actor_user_id', v_actor));
  RETURN v_version;
END;
$fn$;

CREATE FUNCTION app.integration_profile_set_status(
  p_token_hash text, p_profile_id bigint, p_expected_row_version integer, p_status text)
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
  UPDATE meta.integration_profile
     SET status = v_status, row_version = row_version + 1,
         updated_at = clock_timestamp(), updated_by = v_actor
   WHERE profile_id = p_profile_id AND row_version = p_expected_row_version
  RETURNING row_version INTO v_version;
  IF NOT FOUND THEN RAISE EXCEPTION 'Profile not found or changed by another user'; END IF;
  PERFORM audit.log_security_event('INTEGRATION_PROFILE_STATUS_CHANGED', 'INTEGRATION_PROFILE', p_profile_id::text,
    'UPDATE', 'SUCCESS', jsonb_build_object('status', v_status, 'actor_user_id', v_actor));
  RETURN v_version;
END;
$fn$;

-- Scoped permission for secret operations; a denial is journaled and audited.
CREATE FUNCTION app.integration_profile_secret_scope(
  p_actor bigint, p_profile meta.integration_profile, p_operation text, p_target uuid)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
BEGIN
  IF app.has_scoped_permission(p_actor, 'ADMIN_INTEGRATION_MANAGE', 'INTEGRATION_SOURCE_SYSTEM',
                               'SOURCE_SYSTEM_' || p_profile.source_system_id) IS TRUE THEN
    RETURN true;
  END IF;
  PERFORM secret_store.record_denied_attempt(p_operation, p_profile.source_system_id, 'DWH_USER',
                                             p_actor::text, p_target, NULL);
  PERFORM audit.log_security_event('INTEGRATION_PROFILE_SECRET_DENIED', 'INTEGRATION_PROFILE',
    p_profile.profile_id::text, p_operation, 'DENIED',
    jsonb_build_object('source_system_id', p_profile.source_system_id, 'actor_user_id', p_actor));
  RETURN false;
END;
$fn$;

-- Step 1 of CREATE/REPLACE: ATTEMPT + PENDING. Returns the operation id and the
-- server-generated secret_ref under which the API stores the encrypted file.
-- On denial the journal and audit rows are kept and the result has no secret_ref.
CREATE FUNCTION app.integration_profile_secret_begin(p_token_hash text, p_profile_id bigint)
RETURNS TABLE (operation_id uuid, secret_ref uuid, operation_type text, permitted boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  p meta.integration_profile;
  v_active uuid;
  v_type text;
  v_op uuid;
  v_ref uuid := gen_random_uuid();
BEGIN
  SELECT * INTO p FROM meta.integration_profile WHERE profile_id = p_profile_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Profile not found'; END IF;
  IF p.status = 'DISABLED' THEN RAISE EXCEPTION 'Profile is disabled'; END IF;
  IF EXISTS (SELECT 1 FROM secret_store.binding b WHERE b.source_system_id = p.source_system_id
               AND b.profile_code = p.profile_code AND b.state = 'PENDING') THEN
    RAISE EXCEPTION 'Another secret operation for this profile is in progress';
  END IF;
  SELECT b.create_operation_id INTO v_active FROM secret_store.binding b
   WHERE b.source_system_id = p.source_system_id AND b.profile_code = p.profile_code AND b.state = 'ACTIVE';
  v_type := CASE WHEN v_active IS NULL THEN 'CREATE' ELSE 'REPLACE' END;
  IF NOT app.integration_profile_secret_scope(v_actor, p, v_type, NULL) THEN
    RETURN QUERY SELECT NULL::uuid, NULL::uuid, v_type, false;
    RETURN;
  END IF;
  v_op := secret_store.begin_operation(v_type, p.source_system_id, 'DWH_USER', v_actor::text, NULL, v_active);
  PERFORM secret_store.register_pending(v_op, v_ref, p.profile_code);
  PERFORM audit.log_security_event('INTEGRATION_PROFILE_SECRET_STARTED', 'INTEGRATION_PROFILE',
    p.profile_id::text, v_type, 'SUCCESS',
    jsonb_build_object('operation_id', v_op, 'source_system_id', p.source_system_id, 'actor_user_id', v_actor));
  RETURN QUERY SELECT v_op, v_ref, v_type, true;
END;
$fn$;

-- Step 2: OUTCOME; on success the binding becomes ACTIVE (REPLACE revokes the old one).
-- Only the user who started the operation can finish it.
CREATE FUNCTION app.integration_profile_secret_complete(
  p_token_hash text, p_profile_id bigint, p_operation_id uuid, p_succeeded boolean,
  p_ciphertext_sha256 text DEFAULT NULL, p_key_id text DEFAULT NULL)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  p meta.integration_profile;
  a record;
BEGIN
  SELECT * INTO p FROM meta.integration_profile WHERE profile_id = p_profile_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Profile not found'; END IF;
  SELECT e.* INTO a FROM secret_store.operation_event e JOIN secret_store.binding b
      ON b.create_operation_id = e.operation_id
   WHERE e.operation_id = p_operation_id AND e.phase = 'ATTEMPT' AND e.code = 'PERMITTED_ATTEMPT'
     AND e.operation_type IN ('CREATE', 'REPLACE')
     AND b.source_system_id = p.source_system_id AND b.profile_code = p.profile_code;
  IF NOT FOUND OR a.actor_ref IS DISTINCT FROM v_actor::text THEN
    RAISE EXCEPTION 'Secret operation not found for this profile and user';
  END IF;
  PERFORM secret_store.finish_operation(p_operation_id, p_succeeded);
  IF p_succeeded THEN
    PERFORM secret_store.activate_binding(p_operation_id, p_ciphertext_sha256, p_key_id);
  END IF;
  PERFORM audit.log_security_event('INTEGRATION_PROFILE_SECRET_FINISHED', 'INTEGRATION_PROFILE',
    p.profile_id::text, a.operation_type, CASE WHEN p_succeeded THEN 'SUCCESS' ELSE 'FAILURE' END,
    jsonb_build_object('operation_id', p_operation_id, 'key_id', p_key_id, 'actor_user_id', v_actor));
  RETURN CASE WHEN p_succeeded THEN 'ACTIVE' ELSE 'FAILED' END;
END;
$fn$;

-- REVOKE of the profile's ACTIVE secret in one call (no file operation needed).
CREATE FUNCTION app.integration_profile_secret_revoke(p_token_hash text, p_profile_id bigint)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  p meta.integration_profile;
  v_active uuid;
  v_op uuid;
BEGIN
  SELECT * INTO p FROM meta.integration_profile WHERE profile_id = p_profile_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Profile not found'; END IF;
  SELECT b.create_operation_id INTO v_active FROM secret_store.binding b
   WHERE b.source_system_id = p.source_system_id AND b.profile_code = p.profile_code AND b.state = 'ACTIVE';
  IF v_active IS NULL THEN RAISE EXCEPTION 'Profile has no active secret'; END IF;
  IF NOT app.integration_profile_secret_scope(v_actor, p, 'REVOKE', v_active) THEN
    RETURN false;
  END IF;
  v_op := secret_store.begin_operation('REVOKE', p.source_system_id, 'DWH_USER', v_actor::text, v_active, NULL);
  PERFORM secret_store.finish_operation(v_op, true);
  PERFORM audit.log_security_event('INTEGRATION_PROFILE_SECRET_REVOKED', 'INTEGRATION_PROFILE',
    p.profile_id::text, 'REVOKE', 'SUCCESS',
    jsonb_build_object('operation_id', v_op, 'source_system_id', p.source_system_id, 'actor_user_id', v_actor));
  RETURN true;
END;
$fn$;

REVOKE ALL ON FUNCTION
  meta.integration_config_has_secret_key(jsonb),
  meta.integration_profile_guard(),
  meta.integration_profile_reject_truncate(),
  app.integration_profile_list(text, bigint),
  app.integration_profile_create(text, bigint, text, text, text, text, jsonb),
  app.integration_profile_update(text, bigint, integer, text, text, jsonb),
  app.integration_profile_set_status(text, bigint, integer, text),
  app.integration_profile_secret_scope(bigint, meta.integration_profile, text, uuid),
  app.integration_profile_secret_begin(text, bigint),
  app.integration_profile_secret_complete(text, bigint, uuid, boolean, text, text),
  app.integration_profile_secret_revoke(text, bigint)
FROM PUBLIC;

-- M-SS05: the API role may call the session-checked wrappers, nothing else.
GRANT EXECUTE ON FUNCTION
  app.integration_profile_list(text, bigint),
  app.integration_profile_create(text, bigint, text, text, text, text, jsonb),
  app.integration_profile_update(text, bigint, integer, text, text, jsonb),
  app.integration_profile_set_status(text, bigint, integer, text),
  app.integration_profile_secret_begin(text, bigint),
  app.integration_profile_secret_complete(text, bigint, uuid, boolean, text, text),
  app.integration_profile_secret_revoke(text, bigint)
TO dwh_app;
