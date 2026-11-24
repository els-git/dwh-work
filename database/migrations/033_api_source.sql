-- I11 (plan docs/DWH_I11_API_CONNECTOR_PLAN_APPROVED_20261124.md, step 5): objects of an API source.
-- Run as the owner of the schemas (dwh_admin) in one transaction:
--   psql -v ON_ERROR_STOP=1 -1 -f 033_api_source.sql
--
-- B11: an API source has no connection; its object (object_type API) is added by hand
--      (app.integration_api_object_create), schema 'api', object name = code of the request.
-- B1:  the HTTP request of the object is part of its configuration version: meta.object_api_request
--      (one row per version, changed only while the version is DRAFT, copied into a new draft from the
--      version it is based on; no credentials: meta.integration_config_has_secret_key).
-- B2:  the credential of the object (token, password, key) is a SecretStore binding of the object's source with
--      profile OBJECT_<source_object_id>: the same journal, one ACTIVE per object, never shown.
--      CREATE/REPLACE/REVOKE and READ by an administrator: app.integration_object_secret_*;
--      READ by the load only for a RUNNING request of this object (etl.object_secret_read_begin, as package 026).
-- B8:  structure found by "Check the request" is saved as a schema snapshot of this one object
--      (app.integration_api_object_capture) - the mapping editor reads it like a database snapshot.
-- etl.object_run_begin: an API object needs no source connection; its run gets the request of the published version.
-- Existing tables and rows are not changed except the object_type check of meta.source_object (API added).

DO $pre$
BEGIN
  IF to_regprocedure('etl.object_secret_read_begin(bigint, bigint)') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 033 is already applied';
  END IF;
  IF to_regprocedure('etl.source_secret_read_begin(text, bigint)') IS NULL
     OR to_regclass('etl.object_run_request_secret_read') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 026 is required';
  END IF;
  IF to_regclass('meta.object_config_version') IS NULL OR to_regprocedure('etl.object_run_begin(bigint)') IS NULL THEN
    RAISE EXCEPTION 'STOP: packages 008-011 are required';
  END IF;
END
$pre$;

-- ------------------------------------------------------------------ object type API

-- the check on object_type is found by its definition (its name may differ between databases)
DO $ot$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'meta.source_object'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) ~ '\mobject_type\M' LOOP
    EXECUTE format('ALTER TABLE meta.source_object DROP CONSTRAINT %I', r.conname);
  END LOOP;
END
$ot$;
ALTER TABLE meta.source_object ADD CONSTRAINT source_object_object_type_check
  CHECK (object_type::text = ANY (ARRAY['TABLE', 'VIEW', 'API']));

-- ------------------------------------------------------------------ HTTP request of a configuration version

CREATE TABLE meta.object_api_request (
  version_id bigint PRIMARY KEY REFERENCES meta.object_config_version (version_id) ON DELETE CASCADE,
  request jsonb NOT NULL CHECK (jsonb_typeof(request) = 'object'),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by bigint,
  CONSTRAINT object_api_request_no_secrets CHECK (NOT meta.integration_config_has_secret_key(request)),
  CONSTRAINT object_api_request_url CHECK (coalesce(request ->> 'url', '') ~ '^https?://[^\s/?#]+')
);
REVOKE ALL ON meta.object_api_request FROM PUBLIC;

-- Changes only while the version is a draft (the published request is frozen with the version).
CREATE FUNCTION meta.object_api_request_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, meta AS $fn$
DECLARE
  v_status text;
BEGIN
  SELECT status INTO v_status FROM meta.object_config_version
   WHERE version_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
  IF v_status IS DISTINCT FROM 'DRAFT' AND NOT (TG_OP = 'DELETE' AND v_status IS NULL) THEN
    RAISE EXCEPTION 'HTTP request of a published version is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.version_id IS DISTINCT FROM OLD.version_id THEN
    RAISE EXCEPTION 'HTTP request cannot move to another version';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$fn$;
CREATE TRIGGER trg_object_api_request_guard BEFORE INSERT OR UPDATE OR DELETE ON meta.object_api_request
  FOR EACH ROW EXECUTE FUNCTION meta.object_api_request_guard();

CREATE FUNCTION meta.object_api_request_reject_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $fn$
BEGIN
  RAISE EXCEPTION 'TRUNCATE of meta.object_api_request is not allowed';
END;
$fn$;
CREATE TRIGGER trg_object_api_request_no_truncate BEFORE TRUNCATE ON meta.object_api_request
  FOR EACH STATEMENT EXECUTE FUNCTION meta.object_api_request_reject_truncate();

-- A new draft starts with the request of the version it is based on.
CREATE FUNCTION meta.object_api_request_copy_to_draft() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, meta AS $fn$
BEGIN
  IF NEW.status = 'DRAFT' AND NEW.based_on_version_id IS NOT NULL THEN
    INSERT INTO meta.object_api_request (version_id, request, updated_by)
    SELECT NEW.version_id, r.request, NEW.created_by FROM meta.object_api_request r
     WHERE r.version_id = NEW.based_on_version_id;
  END IF;
  RETURN NEW;
END;
$fn$;
CREATE TRIGGER trg_object_api_request_copy AFTER INSERT ON meta.object_config_version
  FOR EACH ROW EXECUTE FUNCTION meta.object_api_request_copy_to_draft();

-- ------------------------------------------------------------------ administration (dwh_app, session checked)

CREATE FUNCTION app.integration_api_object(p_source_object_id bigint, p_lock boolean DEFAULT false)
RETURNS meta.source_object
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, meta AS $fn$
DECLARE
  o meta.source_object;
BEGIN
  IF p_lock THEN
    SELECT * INTO o FROM meta.source_object x WHERE x.source_object_id = p_source_object_id FOR UPDATE;
  ELSE
    SELECT * INTO o FROM meta.source_object x WHERE x.source_object_id = p_source_object_id;
  END IF;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source object not found'; END IF;
  IF o.object_type <> 'API' THEN RAISE EXCEPTION 'Object is not an API object'; END IF;
  RETURN o;
END;
$fn$;

CREATE FUNCTION app.integration_api_object_create(p_token_hash text, p_source_system_id bigint, p_code text,
  p_description text DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  s meta.source_system;
  v_code text := lower(btrim(coalesce(p_code, '')));
  v_id bigint;
BEGIN
  SELECT * INTO s FROM meta.source_system x WHERE x.source_system_id = p_source_system_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source system not found'; END IF;
  IF upper(btrim(s.source_type)) <> 'API' THEN RAISE EXCEPTION 'Source is not an API source'; END IF;
  IF v_code !~ '^[a-z][a-z0-9_]{0,62}$' THEN
    RAISE EXCEPTION 'Request code: latin letters, digits and _, starts with a letter, up to 63';
  END IF;
  IF EXISTS (SELECT 1 FROM meta.source_object x WHERE x.source_system_id = p_source_system_id
               AND x.source_schema = 'api' AND x.source_object = v_code) THEN
    RAISE EXCEPTION 'Request code already used in this source';
  END IF;
  INSERT INTO meta.source_object (source_system_id, source_schema, source_object, object_type, load_strategy,
      is_active, description)
  VALUES (p_source_system_id, 'api', v_code, 'API', 'FULL', true, nullif(btrim(coalesce(p_description, '')), ''))
  RETURNING source_object_id INTO v_id;
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_OBJECT_CREATED', 'SOURCE_OBJECT', v_id::text, 'CREATE',
    'SUCCESS', jsonb_build_object('source_system_id', p_source_system_id, 'object_type', 'API',
                                  'source_object', v_code, 'actor_user_id', v_actor));
  RETURN v_id;
END;
$fn$;

-- Request of the draft (if any) and of the published version, and whether the object has a credential.
CREATE FUNCTION app.integration_api_request_get(p_token_hash text, p_source_object_id bigint)
RETURNS TABLE (draft_version_id bigint, draft_revision integer, draft_request jsonb,
               published_version_id bigint, published_request jsonb,
               has_credential boolean, credential_key_id text, credential_activated_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, secret_store AS $fn$
DECLARE
  o meta.source_object;
BEGIN
  PERFORM app.integration_admin_session_user(p_token_hash);
  o := app.integration_api_object(p_source_object_id);
  RETURN QUERY
  SELECT d.version_id, d.draft_revision, dr.request, p.version_id, pr.request,
         b.secret_ref IS NOT NULL, b.key_id, b.activated_at
    FROM (SELECT 1) one
    LEFT JOIN meta.object_config_version d ON d.source_object_id = o.source_object_id AND d.status = 'DRAFT'
    LEFT JOIN meta.object_api_request dr ON dr.version_id = d.version_id
    LEFT JOIN meta.object_config_current c ON c.source_object_id = o.source_object_id
    LEFT JOIN meta.object_config_version p ON p.version_id = c.version_id
    LEFT JOIN meta.object_api_request pr ON pr.version_id = p.version_id
    LEFT JOIN secret_store.binding b ON b.source_system_id = o.source_system_id
          AND b.profile_code = 'OBJECT_' || o.source_object_id AND b.state = 'ACTIVE';
END;
$fn$;

-- Saves the request into the draft of the object (the draft must exist: the mapping editor creates it).
CREATE FUNCTION app.integration_api_request_save(p_token_hash text, p_source_object_id bigint, p_request jsonb)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  o meta.source_object := app.integration_api_object(p_source_object_id, true);
  d meta.object_config_version;
BEGIN
  IF p_request IS NULL OR jsonb_typeof(p_request) <> 'object' THEN RAISE EXCEPTION 'Invalid HTTP request document'; END IF;
  IF meta.integration_config_has_secret_key(p_request) THEN
    RAISE EXCEPTION 'HTTP request must not contain credentials; store them as the credential of the object';
  END IF;
  SELECT * INTO d FROM meta.object_config_version x
   WHERE x.source_object_id = o.source_object_id AND x.status = 'DRAFT' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Object has no draft configuration'; END IF;
  INSERT INTO meta.object_api_request (version_id, request, updated_by) VALUES (d.version_id, p_request, v_actor)
  ON CONFLICT (version_id) DO UPDATE SET request = EXCLUDED.request, updated_at = clock_timestamp(),
                                         updated_by = EXCLUDED.updated_by;
  PERFORM audit.log_security_event('INTEGRATION_API_REQUEST_SAVED', 'SOURCE_OBJECT', o.source_object_id::text,
    'UPDATE', 'SUCCESS', jsonb_build_object('version_id', d.version_id, 'actor_user_id', v_actor));
  RETURN d.version_id;
END;
$fn$;

-- Credential of the object: step 1 of CREATE/REPLACE (ATTEMPT + PENDING), as 005 for a source.
CREATE FUNCTION app.integration_object_secret_begin(p_token_hash text, p_source_object_id bigint)
RETURNS TABLE (operation_id uuid, secret_ref uuid, operation_type text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  o meta.source_object := app.integration_api_object(p_source_object_id, true);
  v_profile text := 'OBJECT_' || p_source_object_id;
  v_active uuid;
  v_type text;
  v_op uuid;
  v_ref uuid := gen_random_uuid();
BEGIN
  IF EXISTS (SELECT 1 FROM secret_store.binding b WHERE b.source_system_id = o.source_system_id
               AND b.profile_code = v_profile AND b.state = 'PENDING') THEN
    RAISE EXCEPTION 'Another credential operation for this object is in progress';
  END IF;
  SELECT b.create_operation_id INTO v_active FROM secret_store.binding b
   WHERE b.source_system_id = o.source_system_id AND b.profile_code = v_profile AND b.state = 'ACTIVE';
  v_type := CASE WHEN v_active IS NULL THEN 'CREATE' ELSE 'REPLACE' END;
  v_op := secret_store.begin_operation(v_type, o.source_system_id, 'DWH_USER', v_actor::text, NULL, v_active);
  PERFORM secret_store.register_pending(v_op, v_ref, v_profile);
  PERFORM audit.log_security_event('INTEGRATION_OBJECT_SECRET_STARTED', 'SOURCE_OBJECT', p_source_object_id::text,
    v_type, 'SUCCESS', jsonb_build_object('operation_id', v_op, 'actor_user_id', v_actor));
  RETURN QUERY SELECT v_op, v_ref, v_type;
END;
$fn$;

-- Step 2: OUTCOME; on success the credential becomes ACTIVE. Only the user who started it.
CREATE FUNCTION app.integration_object_secret_complete(p_token_hash text, p_source_object_id bigint,
  p_operation_id uuid, p_succeeded boolean, p_ciphertext_sha256 text DEFAULT NULL, p_key_id text DEFAULT NULL)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  o meta.source_object := app.integration_api_object(p_source_object_id, true);
  a record;
BEGIN
  SELECT e.* INTO a FROM secret_store.operation_event e JOIN secret_store.binding b
      ON b.create_operation_id = e.operation_id
   WHERE e.operation_id = p_operation_id AND e.phase = 'ATTEMPT' AND e.code = 'PERMITTED_ATTEMPT'
     AND e.operation_type IN ('CREATE', 'REPLACE')
     AND b.source_system_id = o.source_system_id AND b.profile_code = 'OBJECT_' || p_source_object_id;
  IF NOT FOUND OR a.actor_ref IS DISTINCT FROM v_actor::text THEN
    RAISE EXCEPTION 'Credential operation not found for this object and user';
  END IF;
  PERFORM secret_store.finish_operation(p_operation_id, p_succeeded);
  IF p_succeeded THEN
    PERFORM secret_store.activate_binding(p_operation_id, p_ciphertext_sha256, p_key_id);
  END IF;
  PERFORM audit.log_security_event('INTEGRATION_OBJECT_SECRET_FINISHED', 'SOURCE_OBJECT', p_source_object_id::text,
    a.operation_type, CASE WHEN p_succeeded THEN 'SUCCESS' ELSE 'FAILURE' END,
    jsonb_build_object('operation_id', p_operation_id, 'key_id', p_key_id, 'actor_user_id', v_actor));
  RETURN CASE WHEN p_succeeded THEN 'ACTIVE' ELSE 'FAILED' END;
END;
$fn$;

CREATE FUNCTION app.integration_object_secret_revoke(p_token_hash text, p_source_object_id bigint)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  o meta.source_object := app.integration_api_object(p_source_object_id, true);
  v_active uuid;
  v_op uuid;
BEGIN
  SELECT b.create_operation_id INTO v_active FROM secret_store.binding b
   WHERE b.source_system_id = o.source_system_id AND b.profile_code = 'OBJECT_' || p_source_object_id
     AND b.state = 'ACTIVE';
  IF v_active IS NULL THEN RAISE EXCEPTION 'Object has no active credential'; END IF;
  v_op := secret_store.begin_operation('REVOKE', o.source_system_id, 'DWH_USER', v_actor::text, v_active, NULL);
  PERFORM secret_store.finish_operation(v_op, true);
  PERFORM audit.log_security_event('INTEGRATION_OBJECT_SECRET_REVOKED', 'SOURCE_OBJECT', p_source_object_id::text,
    'REVOKE', 'SUCCESS', jsonb_build_object('operation_id', v_op, 'actor_user_id', v_actor));
  RETURN true;
END;
$fn$;

-- READ by an administrator ("Check the request"): ATTEMPT before decrypting, OUTCOME after.
CREATE FUNCTION app.integration_object_secret_read_begin(p_token_hash text, p_source_object_id bigint)
RETURNS TABLE (operation_id uuid, secret_ref uuid, key_id text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  o meta.source_object := app.integration_api_object(p_source_object_id);
  b secret_store.binding;
  v_op uuid;
BEGIN
  SELECT * INTO b FROM secret_store.binding sb
   WHERE sb.source_system_id = o.source_system_id AND sb.profile_code = 'OBJECT_' || p_source_object_id
     AND sb.state = 'ACTIVE';
  IF NOT FOUND THEN RAISE EXCEPTION 'Object has no active credential'; END IF;
  IF b.key_id IS NULL THEN RAISE EXCEPTION 'Credential has no key id; replace it'; END IF;
  v_op := secret_store.begin_operation('READ', o.source_system_id, 'DWH_USER', v_actor::text, b.create_operation_id, NULL);
  PERFORM audit.log_security_event('INTEGRATION_OBJECT_SECRET_READ', 'SOURCE_OBJECT', p_source_object_id::text,
    'READ', 'SUCCESS', jsonb_build_object('operation_id', v_op, 'key_id', b.key_id, 'actor_user_id', v_actor));
  RETURN QUERY SELECT v_op, b.secret_ref, b.key_id;
END;
$fn$;

CREATE FUNCTION app.integration_object_secret_read_finish(p_token_hash text, p_source_object_id bigint,
  p_operation_id uuid, p_succeeded boolean)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit, secret_store AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  o meta.source_object := app.integration_api_object(p_source_object_id);
  a record;
BEGIN
  SELECT e.* INTO a FROM secret_store.operation_event e
   WHERE e.operation_id = p_operation_id AND e.phase = 'ATTEMPT' AND e.code = 'PERMITTED_ATTEMPT'
     AND e.operation_type = 'READ' AND e.source_system_id = o.source_system_id;
  IF NOT FOUND OR a.actor_ref IS DISTINCT FROM v_actor::text THEN
    RAISE EXCEPTION 'Credential read not found for this object and user';
  END IF;
  PERFORM secret_store.finish_operation(p_operation_id, p_succeeded);
  RETURN p_succeeded;
END;
$fn$;

-- Structure found by "Check the request": a snapshot of this one object (the mapping editor reads the
-- latest snapshot that contains the object).
CREATE FUNCTION app.integration_api_object_capture(p_token_hash text, p_source_object_id bigint, p_columns jsonb)
RETURNS TABLE (metadata_capture_id bigint, columns_captured integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  o meta.source_object := app.integration_api_object(p_source_object_id);
  v_capture bigint;
  v_snapshot bigint;
  v_columns integer := 0;
  col jsonb;
BEGIN
  IF NOT o.is_active THEN RAISE EXCEPTION 'Source object is not active'; END IF;
  IF jsonb_typeof(p_columns) IS DISTINCT FROM 'array' OR jsonb_array_length(p_columns) = 0 THEN
    RAISE EXCEPTION 'Structure must contain columns';
  END IF;
  INSERT INTO meta.metadata_capture AS mc (source_system_id, status, started_at, finished_at, source_database,
      source_version, created_by, message)
  VALUES (o.source_system_id, 'RUNNING', clock_timestamp(), NULL, 'API', 'HTTP', 'dwh_user:' || v_actor,
      'Structure of an API object from "Check the request"')
  RETURNING mc.metadata_capture_id INTO v_capture;
  INSERT INTO meta.source_object_snapshot (metadata_capture_id, source_object_id, source_schema, source_object,
      relkind, object_type)
  VALUES (v_capture, o.source_object_id, o.source_schema, o.source_object, 'api', 'API')
  RETURNING object_snapshot_id INTO v_snapshot;
  FOR col IN SELECT * FROM jsonb_array_elements(p_columns) LOOP
    IF coalesce(col ->> 'column_name', '') = '' OR coalesce(col ->> 'formatted_type', '') = '' THEN
      RAISE EXCEPTION 'Column needs a name and a type';
    END IF;
    INSERT INTO meta.source_column_snapshot (object_snapshot_id, ordinal_position, column_name, formatted_type,
        udt_name, is_nullable, is_primary_key, is_unique_key, is_identity, is_generated)
    VALUES (v_snapshot, v_columns + 1, col ->> 'column_name', col ->> 'formatted_type', col ->> 'formatted_type',
        coalesce((col ->> 'is_nullable')::boolean, true), false, false, false, false);
    v_columns := v_columns + 1;
  END LOOP;
  UPDATE meta.metadata_capture mc SET status = 'SUCCESS', finished_at = clock_timestamp(),
         objects_captured = 1, columns_captured = v_columns
   WHERE mc.metadata_capture_id = v_capture;
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_METADATA_CAPTURED', 'SOURCE_OBJECT', p_source_object_id::text,
    'CREATE', 'SUCCESS', jsonb_build_object('metadata_capture_id', v_capture, 'columns', v_columns,
                                           'actor_user_id', v_actor));
  RETURN QUERY SELECT v_capture, v_columns;
END;
$fn$;

-- ------------------------------------------------------------------ load (dwh_etl)

-- READ of the object's credential only for a RUNNING request of this object (as 026 for a source).
CREATE FUNCTION etl.object_secret_read_begin(p_source_object_id bigint, p_request_id bigint)
RETURNS TABLE (operation_id uuid, secret_ref uuid, key_id text, refusal text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, meta, etl, audit, secret_store AS $fn$
DECLARE
  v_actor text := session_user::text;
  o meta.source_object;
  b secret_store.binding;
  r etl.object_run_request;
  v_refusal text;
  v_op uuid;
BEGIN
  SELECT * INTO o FROM meta.source_object x WHERE x.source_object_id = p_source_object_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source object not found'; END IF;
  IF o.object_type <> 'API' THEN RAISE EXCEPTION 'Object is not an API object'; END IF;
  SELECT * INTO b FROM secret_store.binding sb
   WHERE sb.source_system_id = o.source_system_id AND sb.profile_code = 'OBJECT_' || p_source_object_id
     AND sb.state = 'ACTIVE';
  IF NOT FOUND THEN RAISE EXCEPTION 'Object has no active credential'; END IF;
  IF b.key_id IS NULL THEN RAISE EXCEPTION 'Credential has no key id; replace it'; END IF;

  SELECT * INTO r FROM etl.object_run_request q WHERE q.request_id = p_request_id FOR UPDATE;
  IF NOT FOUND THEN
    v_refusal := 'REQUEST_NOT_FOUND';
  ELSIF r.status <> 'RUNNING' THEN
    v_refusal := 'REQUEST_NOT_RUNNING';
  ELSIF r.run_kind NOT IN ('RUN', 'FULL_RERUN') THEN
    v_refusal := 'REQUEST_DOES_NOT_READ_SOURCE';
  ELSIF r.source_object_id <> p_source_object_id THEN
    v_refusal := 'REQUEST_OF_OTHER_OBJECT';
  ELSIF r.started_at IS NULL OR r.started_at < clock_timestamp() - interval '15 minutes' THEN
    v_refusal := 'REQUEST_STARTED_TOO_LONG_AGO';
  ELSIF EXISTS (SELECT 1 FROM etl.object_run_request_secret_read x WHERE x.request_id = p_request_id) THEN
    v_refusal := 'CREDENTIAL_ALREADY_READ';
  END IF;

  IF v_refusal IS NOT NULL THEN
    PERFORM secret_store.record_denied_attempt('READ', o.source_system_id, 'SERVICE', v_actor, b.create_operation_id, NULL);
    PERFORM set_config('dwh.security_source', 'ETL', true);
    PERFORM audit.log_security_event('ETL_OBJECT_SECRET_READ', 'SOURCE_OBJECT', p_source_object_id::text, 'READ',
      'FAILURE', jsonb_build_object('request_id', p_request_id, 'refusal', v_refusal, 'actor_role', v_actor));
    RETURN QUERY SELECT NULL::uuid, NULL::uuid, NULL::text, v_refusal;
    RETURN;
  END IF;

  v_op := secret_store.begin_operation('READ', o.source_system_id, 'SERVICE', v_actor, b.create_operation_id, NULL);
  INSERT INTO etl.object_run_request_secret_read (request_id, operation_id, source_system_id)
  VALUES (p_request_id, v_op, o.source_system_id);
  PERFORM set_config('dwh.security_source', 'ETL', true);
  PERFORM audit.log_security_event('ETL_OBJECT_SECRET_READ', 'SOURCE_OBJECT', p_source_object_id::text, 'READ',
    'SUCCESS', jsonb_build_object('operation_id', v_op, 'key_id', b.key_id, 'actor_role', v_actor,
                                  'request_id', p_request_id));
  RETURN QUERY SELECT v_op, b.secret_ref, b.key_id, NULL::text;
END;
$fn$;

-- Run start (body of package 011 with one change): an API object needs no source connection;
-- its run gets object_type and the HTTP request of the published version.
CREATE OR REPLACE FUNCTION etl.object_run_begin(p_source_object_id bigint)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl, meta, audit AS $fn$
DECLARE
  o meta.source_object;
  s meta.source_system;
  c meta.object_config_current;
  v meta.object_config_version;
  sc meta.source_connection;
  v_request jsonb;
  v_job bigint;
  v_batch bigint;
  v_job_run bigint;
  v_run bigint;
  stale record;
BEGIN
  SELECT * INTO o FROM meta.source_object x WHERE x.source_object_id = p_source_object_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source object not found'; END IF;
  IF NOT o.is_active THEN RAISE EXCEPTION 'Source object is not active'; END IF;
  IF meta.source_is_legacy(o.source_system_id) THEN
    RAISE EXCEPTION 'Legacy CRM sources are loaded by their own ETL';
  END IF;
  SELECT * INTO s FROM meta.source_system x WHERE x.source_system_id = o.source_system_id;
  IF NOT s.is_active THEN RAISE EXCEPTION 'Source system is not active'; END IF;
  SELECT * INTO c FROM meta.object_config_current x WHERE x.source_object_id = p_source_object_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Object has no published configuration'; END IF;
  IF c.status <> 'READY' THEN RAISE EXCEPTION 'Object configuration is %, resolve it before a run', c.status; END IF;
  SELECT * INTO v FROM meta.object_config_version x WHERE x.version_id = c.version_id;
  IF o.object_type = 'API' THEN
    SELECT r.request INTO v_request FROM meta.object_api_request r WHERE r.version_id = v.version_id;
    IF v_request IS NULL THEN RAISE EXCEPTION 'Published version has no HTTP request'; END IF;
  ELSE
    SELECT * INTO sc FROM meta.source_connection x WHERE x.source_system_id = o.source_system_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'Source has no connection settings'; END IF;
    IF sc.status = 'DISABLED' THEN RAISE EXCEPTION 'Source connection is disabled'; END IF;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace
                  WHERE n.nspname = v.target_schema AND t.relname = v.target_table AND t.relkind IN ('r', 'p')) THEN
    RAISE EXCEPTION 'Target table does not exist';
  END IF;
  -- One run per object: the lock lives as long as the calling session.
  IF NOT pg_try_advisory_lock(hashtext('dwh.object_run'), p_source_object_id::integer) THEN
    RAISE EXCEPTION 'Another run of this object is in progress';
  END IF;
  PERFORM set_config('dwh.security_source', 'ETL', true);
  FOR stale IN SELECT r.run_id FROM etl.object_run r
                WHERE r.source_object_id = p_source_object_id AND r.status = 'RUNNING' ORDER BY r.run_id LOOP
    PERFORM etl.object_run_close(stale.run_id, false, 0, 0, 0, 0, 'ABANDONED',
      'Previous run did not finish (session ended)', NULL, NULL);
  END LOOP;
  SELECT j.job_id INTO v_job FROM etl.job j WHERE j.code = 'OBJECT_LOAD_' || p_source_object_id;
  IF NOT FOUND THEN
    INSERT INTO etl.job(code, name, job_type, source_system_id, source_object_id, target_schema, target_object,
        description)
    VALUES ('OBJECT_LOAD_' || p_source_object_id, left('Object load: ' || o.source_schema || '.' || o.source_object, 200),
        'EXTRACT', o.source_system_id, p_source_object_id, v.target_schema, v.target_table,
        'I06: published object configuration')
    RETURNING job_id INTO v_job;
  ELSE
    UPDATE etl.job SET target_schema = v.target_schema, target_object = v.target_table, updated_at = now()
     WHERE job_id = v_job AND (target_schema IS DISTINCT FROM v.target_schema OR target_object IS DISTINCT FROM v.target_table);
  END IF;
  INSERT INTO etl.batch_run(batch_code, business_date, trigger_type, status, started_at, message, created_by,
      pipeline_code, run_mode, attempt_no)
  VALUES ('OBJECT_LOAD_' || p_source_object_id, current_date, 'MANUAL', 'RUNNING', clock_timestamp(),
      left('Object load ' || s.code || ' ' || o.source_schema || '.' || o.source_object || ' v' || v.version_no, 2000),
      left(session_user::text, 128), 'OBJECT_LOAD', 'FULL', 1)
  RETURNING batch_run_id INTO v_batch;
  INSERT INTO etl.job_run(batch_run_id, job_id, status, started_at)
  VALUES (v_batch, v_job, 'RUNNING', clock_timestamp()) RETURNING job_run_id INTO v_job_run;
  INSERT INTO etl.object_run(source_object_id, version_id, definition_hash, batch_run_id, job_run_id, started_by)
  VALUES (p_source_object_id, v.version_id, v.definition_hash, v_batch, v_job_run, session_user::text)
  RETURNING run_id INTO v_run;
  PERFORM audit.log_security_event('ETL_OBJECT_RUN_STARTED', 'SOURCE_OBJECT', p_source_object_id::text, 'RUN', 'SUCCESS',
    jsonb_build_object('run_id', v_run, 'batch_run_id', v_batch, 'version_id', v.version_id,
                       'actor_role', session_user::text));
  RETURN jsonb_build_object(
    'run_id', v_run, 'batch_run_id', v_batch, 'job_run_id', v_job_run,
    'version_id', v.version_id, 'version_no', v.version_no, 'definition_hash', v.definition_hash,
    'definition', meta.object_config_definition(v.version_id),
    'watermark', (SELECT r.watermark_to FROM etl.object_run r
                   WHERE r.source_object_id = p_source_object_id AND r.version_id = v.version_id
                     AND r.status = 'SUCCESS' AND r.watermark_to IS NOT NULL
                   ORDER BY r.run_id DESC LIMIT 1),
    'source', jsonb_build_object('source_system_id', s.source_system_id, 'code', s.code, 'source_type', s.source_type,
                                 'source_schema', o.source_schema, 'source_object', o.source_object,
                                 'source_object_id', o.source_object_id, 'object_type', o.object_type,
                                 'connection_config', sc.connection_config, 'api_request', v_request));
END;
$fn$;

-- ------------------------------------------------------------------ privileges

REVOKE ALL ON FUNCTION
  meta.object_api_request_guard(),
  meta.object_api_request_reject_truncate(),
  meta.object_api_request_copy_to_draft(),
  app.integration_api_object(bigint, boolean),
  app.integration_api_object_create(text, bigint, text, text),
  app.integration_api_request_get(text, bigint),
  app.integration_api_request_save(text, bigint, jsonb),
  app.integration_object_secret_begin(text, bigint),
  app.integration_object_secret_complete(text, bigint, uuid, boolean, text, text),
  app.integration_object_secret_revoke(text, bigint),
  app.integration_object_secret_read_begin(text, bigint),
  app.integration_object_secret_read_finish(text, bigint, uuid, boolean),
  app.integration_api_object_capture(text, bigint, jsonb),
  etl.object_secret_read_begin(bigint, bigint)
FROM PUBLIC;

-- rule since 005: default grants of the schemas are removed from the new objects and reported
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT 'TABLE meta.object_api_request' AS obj, x.grantee
      FROM pg_class c, aclexplode(c.relacl) x
     WHERE c.oid = 'meta.object_api_request'::regclass AND x.grantee <> c.relowner
    UNION
    SELECT 'FUNCTION ' || p.oid::regprocedure::text, x.grantee
      FROM pg_proc p, aclexplode(p.proacl) x
     WHERE p.oid IN ('meta.object_api_request_guard()'::regprocedure,
                     'meta.object_api_request_reject_truncate()'::regprocedure,
                     'meta.object_api_request_copy_to_draft()'::regprocedure,
                     'app.integration_api_object(bigint, boolean)'::regprocedure,
                     'app.integration_api_object_create(text, bigint, text, text)'::regprocedure,
                     'app.integration_api_request_get(text, bigint)'::regprocedure,
                     'app.integration_api_request_save(text, bigint, jsonb)'::regprocedure,
                     'app.integration_object_secret_begin(text, bigint)'::regprocedure,
                     'app.integration_object_secret_complete(text, bigint, uuid, boolean, text, text)'::regprocedure,
                     'app.integration_object_secret_revoke(text, bigint)'::regprocedure,
                     'app.integration_object_secret_read_begin(text, bigint)'::regprocedure,
                     'app.integration_object_secret_read_finish(text, bigint, uuid, boolean)'::regprocedure,
                     'app.integration_api_object_capture(text, bigint, jsonb)'::regprocedure,
                     'etl.object_secret_read_begin(bigint, bigint)'::regprocedure)
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
  app.integration_api_object_create(text, bigint, text, text),
  app.integration_api_request_get(text, bigint),
  app.integration_api_request_save(text, bigint, jsonb),
  app.integration_object_secret_begin(text, bigint),
  app.integration_object_secret_complete(text, bigint, uuid, boolean, text, text),
  app.integration_object_secret_revoke(text, bigint),
  app.integration_object_secret_read_begin(text, bigint),
  app.integration_object_secret_read_finish(text, bigint, uuid, boolean),
  app.integration_api_object_capture(text, bigint, jsonb)
TO dwh_app;
GRANT EXECUTE ON FUNCTION etl.object_secret_read_begin(bigint, bigint) TO dwh_etl;

-- result line on stdout (dwh_sql_package.py looks for it there)
SELECT 'MIGRATION_033_DONE' AS result;
