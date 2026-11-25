-- I12 (plan docs/DWH_I12_FILE_CONNECTOR_PLAN_APPROVED_20261125.md, step 3): objects of a file source.
-- Run as the owner of the schemas (dwh_admin) in one transaction:
--   psql -v ON_ERROR_STOP=1 -1 -f 034_file_source.sql
-- F1:  files are read from the folder <DWH_FILE_ROOT>/<code of the source> on the DWH server (only reading);
--      the folder is not a setting: it follows the code of the source (no paths from users).
-- F2': the object (object_type FILE, schema 'file', object name = code) has file settings in its configuration
--      version: meta.object_file_config (mask, format, path to the records, path to the actuality date inside
--      the file, LATEST / ALL_NEW); changed only while the version is DRAFT, copied into a new draft.
-- F6:  register of files etl.object_file: one row per object and content (SHA-256): result, date inside the file,
--      run, attempts; a loaded file is never applied again. Written by the load (dwh_etl) only through
--      etl.object_file_record for a RUNNING run of the object; read by administrators (app.integration_file_registry).
-- F11: a file is actual when its date is later than the date of the last loaded file of the object
--      (etl.object_file_state gives that date and the known contents to the load).
-- etl.object_run_begin: a FILE object needs no source connection; its run gets the file settings.
-- Existing tables and rows are not changed except the object_type check of meta.source_object (FILE added).

DO $pre$
BEGIN
  IF to_regprocedure('etl.object_file_record(bigint, text, bigint, text, timestamp with time zone, text, bigint, text)') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 034 is already applied';
  END IF;
  IF to_regprocedure('etl.object_secret_read_begin(bigint, bigint)') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 033 is required';
  END IF;
END
$pre$;

-- ------------------------------------------------------------------ object type FILE

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
  CHECK (object_type::text = ANY (ARRAY['TABLE', 'VIEW', 'API', 'FILE']));

-- ------------------------------------------------------------------ file settings of a configuration version

CREATE TABLE meta.object_file_config (
  version_id bigint PRIMARY KEY REFERENCES meta.object_config_version (version_id) ON DELETE CASCADE,
  config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by bigint,
  CONSTRAINT object_file_config_no_secrets CHECK (NOT meta.integration_config_has_secret_key(config)),
  CONSTRAINT object_file_config_mask CHECK (coalesce(config ->> 'mask', '') ~ '^[A-Za-z0-9_.*?\[\]-]{1,200}$'),
  CONSTRAINT object_file_config_format CHECK (config ->> 'format' IN ('JSON', 'JSONL', 'XML')),
  CONSTRAINT object_file_config_mode CHECK (config ->> 'mode' IN ('LATEST', 'ALL_NEW'))
);
REVOKE ALL ON meta.object_file_config FROM PUBLIC;

CREATE FUNCTION meta.object_file_config_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, meta AS $fn$
DECLARE
  v_status text;
BEGIN
  SELECT status INTO v_status FROM meta.object_config_version
   WHERE version_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
  IF v_status IS DISTINCT FROM 'DRAFT' AND NOT (TG_OP = 'DELETE' AND v_status IS NULL) THEN
    RAISE EXCEPTION 'File settings of a published version are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.version_id IS DISTINCT FROM OLD.version_id THEN
    RAISE EXCEPTION 'File settings cannot move to another version';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$fn$;
CREATE TRIGGER trg_object_file_config_guard BEFORE INSERT OR UPDATE OR DELETE ON meta.object_file_config
  FOR EACH ROW EXECUTE FUNCTION meta.object_file_config_guard();

CREATE FUNCTION meta.object_file_reject_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $fn$
BEGIN
  RAISE EXCEPTION 'TRUNCATE of % is not allowed', TG_TABLE_NAME;
END;
$fn$;
CREATE TRIGGER trg_object_file_config_no_truncate BEFORE TRUNCATE ON meta.object_file_config
  FOR EACH STATEMENT EXECUTE FUNCTION meta.object_file_reject_truncate();

CREATE FUNCTION meta.object_file_config_copy_to_draft() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, meta AS $fn$
BEGIN
  IF NEW.status = 'DRAFT' AND NEW.based_on_version_id IS NOT NULL THEN
    INSERT INTO meta.object_file_config (version_id, config, updated_by)
    SELECT NEW.version_id, f.config, NEW.created_by FROM meta.object_file_config f
     WHERE f.version_id = NEW.based_on_version_id;
  END IF;
  RETURN NEW;
END;
$fn$;
CREATE TRIGGER trg_object_file_config_copy AFTER INSERT ON meta.object_config_version
  FOR EACH ROW EXECUTE FUNCTION meta.object_file_config_copy_to_draft();

-- ------------------------------------------------------------------ register of files

CREATE TABLE etl.object_file (
  file_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_object_id bigint NOT NULL REFERENCES meta.source_object (source_object_id) ON DELETE RESTRICT,
  file_name text NOT NULL CHECK (file_name ~ '^[^/\\]{1,255}$'),
  file_size bigint NOT NULL CHECK (file_size >= 0),
  sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  actual_at timestamptz,
  status text NOT NULL CHECK (status IN ('LOADED', 'FAILED', 'NO_DATE', 'NOT_ACTUAL')),
  run_id bigint REFERENCES etl.object_run (run_id) ON DELETE RESTRICT,
  rows_read bigint CHECK (rows_read >= 0),
  attempts integer NOT NULL DEFAULT 1 CHECK (attempts >= 1),
  message text CHECK (length(message) <= 2000),
  first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT object_file_content UNIQUE (source_object_id, sha256),
  CONSTRAINT object_file_loaded_date CHECK (status <> 'LOADED' OR actual_at IS NOT NULL)
);
CREATE INDEX object_file_object_actual ON etl.object_file (source_object_id, status, actual_at DESC);
REVOKE ALL ON etl.object_file FROM PUBLIC;
CREATE TRIGGER trg_object_file_no_truncate BEFORE TRUNCATE ON etl.object_file
  FOR EACH STATEMENT EXECUTE FUNCTION meta.object_file_reject_truncate();

-- ------------------------------------------------------------------ administration (dwh_app, session checked)

CREATE FUNCTION app.integration_file_object(p_source_object_id bigint, p_lock boolean DEFAULT false)
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
  IF o.object_type <> 'FILE' THEN RAISE EXCEPTION 'Object is not a file object'; END IF;
  RETURN o;
END;
$fn$;

CREATE FUNCTION app.integration_file_object_create(p_token_hash text, p_source_system_id bigint, p_code text,
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
  IF upper(btrim(s.source_type)) <> 'FILE' THEN RAISE EXCEPTION 'Source is not a file source'; END IF;
  IF v_code !~ '^[a-z][a-z0-9_]{0,62}$' THEN
    RAISE EXCEPTION 'File object code: latin letters, digits and _, starts with a letter, up to 63';
  END IF;
  IF EXISTS (SELECT 1 FROM meta.source_object x WHERE x.source_system_id = p_source_system_id
               AND x.source_schema = 'file' AND x.source_object = v_code) THEN
    RAISE EXCEPTION 'File object code already used in this source';
  END IF;
  INSERT INTO meta.source_object (source_system_id, source_schema, source_object, object_type, load_strategy,
      is_active, description)
  VALUES (p_source_system_id, 'file', v_code, 'FILE', 'FULL', true, nullif(btrim(coalesce(p_description, '')), ''))
  RETURNING source_object_id INTO v_id;
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_OBJECT_CREATED', 'SOURCE_OBJECT', v_id::text, 'CREATE',
    'SUCCESS', jsonb_build_object('source_system_id', p_source_system_id, 'object_type', 'FILE',
                                  'source_object', v_code, 'actor_user_id', v_actor));
  RETURN v_id;
END;
$fn$;

-- File settings of the draft (if any) and of the published version; the folder follows the source code.
CREATE FUNCTION app.integration_file_config_get(p_token_hash text, p_source_object_id bigint)
RETURNS TABLE (draft_version_id bigint, draft_revision integer, draft_config jsonb,
               published_version_id bigint, published_config jsonb, source_code text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
DECLARE
  o meta.source_object;
BEGIN
  PERFORM app.integration_admin_session_user(p_token_hash);
  o := app.integration_file_object(p_source_object_id);
  RETURN QUERY
  SELECT d.version_id, d.draft_revision, dc.config, p.version_id, pc.config, s.code::text
    FROM meta.source_system s
    LEFT JOIN meta.object_config_version d ON d.source_object_id = o.source_object_id AND d.status = 'DRAFT'
    LEFT JOIN meta.object_file_config dc ON dc.version_id = d.version_id
    LEFT JOIN meta.object_config_current c ON c.source_object_id = o.source_object_id
    LEFT JOIN meta.object_config_version p ON p.version_id = c.version_id
    LEFT JOIN meta.object_file_config pc ON pc.version_id = p.version_id
   WHERE s.source_system_id = o.source_system_id;
END;
$fn$;

CREATE FUNCTION app.integration_file_config_save(p_token_hash text, p_source_object_id bigint, p_config jsonb)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  o meta.source_object := app.integration_file_object(p_source_object_id, true);
  d meta.object_config_version;
BEGIN
  IF p_config IS NULL OR jsonb_typeof(p_config) <> 'object' THEN RAISE EXCEPTION 'Invalid file settings document'; END IF;
  SELECT * INTO d FROM meta.object_config_version x
   WHERE x.source_object_id = o.source_object_id AND x.status = 'DRAFT' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Object has no draft configuration'; END IF;
  INSERT INTO meta.object_file_config (version_id, config, updated_by) VALUES (d.version_id, p_config, v_actor)
  ON CONFLICT (version_id) DO UPDATE SET config = EXCLUDED.config, updated_at = clock_timestamp(),
                                         updated_by = EXCLUDED.updated_by;
  PERFORM audit.log_security_event('INTEGRATION_FILE_CONFIG_SAVED', 'SOURCE_OBJECT', o.source_object_id::text,
    'UPDATE', 'SUCCESS', jsonb_build_object('version_id', d.version_id, 'actor_user_id', v_actor));
  RETURN d.version_id;
END;
$fn$;

-- Structure found by "Check the file": a snapshot of this one object.
CREATE FUNCTION app.integration_file_object_capture(p_token_hash text, p_source_object_id bigint, p_columns jsonb)
RETURNS TABLE (metadata_capture_id bigint, columns_captured integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  o meta.source_object := app.integration_file_object(p_source_object_id);
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
  VALUES (o.source_system_id, 'RUNNING', clock_timestamp(), NULL, 'FILE', 'FILE', 'dwh_user:' || v_actor,
      'Structure of a file object from "Check the file"')
  RETURNING mc.metadata_capture_id INTO v_capture;
  INSERT INTO meta.source_object_snapshot (metadata_capture_id, source_object_id, source_schema, source_object,
      relkind, object_type)
  VALUES (v_capture, o.source_object_id, o.source_schema, o.source_object, 'file', 'FILE')
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

-- Register of the object's files (newest first).
CREATE FUNCTION app.integration_file_registry(p_token_hash text, p_source_object_id bigint, p_limit integer DEFAULT 100)
RETURNS TABLE (file_id bigint, file_name text, file_size bigint, sha256 text, actual_at timestamptz, status text,
               run_id bigint, rows_read bigint, attempts integer, message text, first_seen_at timestamptz,
               updated_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, etl AS $fn$
DECLARE
  o meta.source_object;
BEGIN
  PERFORM app.integration_admin_session_user(p_token_hash);
  o := app.integration_file_object(p_source_object_id);
  RETURN QUERY
  SELECT f.file_id, f.file_name, f.file_size, f.sha256, f.actual_at, f.status, f.run_id, f.rows_read, f.attempts,
         f.message, f.first_seen_at, f.updated_at
    FROM etl.object_file f
   WHERE f.source_object_id = o.source_object_id
   ORDER BY f.updated_at DESC, f.file_id DESC
   LIMIT greatest(1, least(coalesce(p_limit, 100), 1000));
END;
$fn$;

-- ------------------------------------------------------------------ load (dwh_etl)

CREATE FUNCTION etl.object_file_running(p_run_id bigint) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl, meta AS $fn$
DECLARE
  v_object bigint;
BEGIN
  SELECT r.source_object_id INTO v_object FROM etl.object_run r
    JOIN meta.source_object o ON o.source_object_id = r.source_object_id
   WHERE r.run_id = p_run_id AND r.status = 'RUNNING' AND o.object_type = 'FILE';
  IF v_object IS NULL THEN RAISE EXCEPTION 'Run % is not a running run of a file object', p_run_id; END IF;
  RETURN v_object;
END;
$fn$;

-- What the load needs to choose files (F11): date of the last loaded file and the known contents.
CREATE FUNCTION etl.object_file_state(p_run_id bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl AS $fn$
DECLARE
  v_object bigint := etl.object_file_running(p_run_id);
BEGIN
  RETURN jsonb_build_object(
    'last_loaded_at', (SELECT max(f.actual_at) FROM etl.object_file f
                        WHERE f.source_object_id = v_object AND f.status = 'LOADED'),
    'known', coalesce((SELECT jsonb_object_agg(f.sha256, f.status) FROM etl.object_file f
                        WHERE f.source_object_id = v_object), '{}'::jsonb));
END;
$fn$;

-- Result for one file of a running run. A loaded content is never changed again.
CREATE FUNCTION etl.object_file_record(p_run_id bigint, p_file_name text, p_file_size bigint, p_sha256 text,
  p_actual_at timestamptz, p_status text, p_rows_read bigint, p_message text)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl AS $fn$
DECLARE
  v_object bigint := etl.object_file_running(p_run_id);
  v_id bigint;
  v_old text;
BEGIN
  SELECT f.file_id, f.status INTO v_id, v_old FROM etl.object_file f
   WHERE f.source_object_id = v_object AND f.sha256 = p_sha256 FOR UPDATE;
  IF v_old = 'LOADED' THEN RAISE EXCEPTION 'File content % is already loaded', left(p_sha256, 12); END IF;
  IF v_id IS NULL THEN
    INSERT INTO etl.object_file (source_object_id, file_name, file_size, sha256, actual_at, status, run_id,
        rows_read, message)
    VALUES (v_object, p_file_name, p_file_size, p_sha256, p_actual_at, p_status, p_run_id, p_rows_read,
        left(p_message, 2000))
    RETURNING file_id INTO v_id;
  ELSE
    UPDATE etl.object_file SET file_name = p_file_name, file_size = p_file_size, actual_at = p_actual_at,
           status = p_status, run_id = p_run_id, rows_read = p_rows_read, attempts = attempts + 1,
           message = left(p_message, 2000), updated_at = clock_timestamp()
     WHERE file_id = v_id;
  END IF;
  RETURN v_id;
END;
$fn$;

-- Run start (body of package 033 with one change): a FILE object needs no source connection;
-- its run gets the file settings of the published version (the folder is the code of the source).
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
  v_file jsonb;
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
  ELSIF o.object_type = 'FILE' THEN
    SELECT f.config INTO v_file FROM meta.object_file_config f WHERE f.version_id = v.version_id;
    IF v_file IS NULL THEN RAISE EXCEPTION 'Published version has no file settings'; END IF;
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
                                 'connection_config', sc.connection_config, 'api_request', v_request,
                                 'file_config', v_file));
END;
$fn$;

-- ------------------------------------------------------------------ privileges

REVOKE ALL ON FUNCTION
  meta.object_file_config_guard(),
  meta.object_file_reject_truncate(),
  meta.object_file_config_copy_to_draft(),
  app.integration_file_object(bigint, boolean),
  app.integration_file_object_create(text, bigint, text, text),
  app.integration_file_config_get(text, bigint),
  app.integration_file_config_save(text, bigint, jsonb),
  app.integration_file_object_capture(text, bigint, jsonb),
  app.integration_file_registry(text, bigint, integer),
  etl.object_file_running(bigint),
  etl.object_file_state(bigint),
  etl.object_file_record(bigint, text, bigint, text, timestamptz, text, bigint, text)
FROM PUBLIC;

-- rule since 005: default grants of the schemas are removed from the new objects and reported
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT 'TABLE ' || c.oid::regclass::text AS obj, x.grantee
      FROM pg_class c, aclexplode(c.relacl) x
     WHERE c.oid IN ('meta.object_file_config'::regclass, 'etl.object_file'::regclass) AND x.grantee <> c.relowner
    UNION
    SELECT 'SEQUENCE ' || s.oid::regclass::text, x.grantee
      FROM pg_class s, aclexplode(s.relacl) x
     WHERE s.oid = pg_get_serial_sequence('etl.object_file', 'file_id')::regclass AND x.grantee <> s.relowner
    UNION
    SELECT 'FUNCTION ' || p.oid::regprocedure::text, x.grantee
      FROM pg_proc p, aclexplode(p.proacl) x
     WHERE p.oid IN ('meta.object_file_config_guard()'::regprocedure,
                     'meta.object_file_reject_truncate()'::regprocedure,
                     'meta.object_file_config_copy_to_draft()'::regprocedure,
                     'app.integration_file_object(bigint, boolean)'::regprocedure,
                     'app.integration_file_object_create(text, bigint, text, text)'::regprocedure,
                     'app.integration_file_config_get(text, bigint)'::regprocedure,
                     'app.integration_file_config_save(text, bigint, jsonb)'::regprocedure,
                     'app.integration_file_object_capture(text, bigint, jsonb)'::regprocedure,
                     'app.integration_file_registry(text, bigint, integer)'::regprocedure,
                     'etl.object_file_running(bigint)'::regprocedure,
                     'etl.object_file_state(bigint)'::regprocedure,
                     'etl.object_file_record(bigint, text, bigint, text, timestamptz, text, bigint, text)'::regprocedure)
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
  app.integration_file_object_create(text, bigint, text, text),
  app.integration_file_config_get(text, bigint),
  app.integration_file_config_save(text, bigint, jsonb),
  app.integration_file_object_capture(text, bigint, jsonb),
  app.integration_file_registry(text, bigint, integer)
TO dwh_app;
GRANT EXECUTE ON FUNCTION
  etl.object_file_state(bigint),
  etl.object_file_record(bigint, text, bigint, text, timestamptz, text, bigint, text)
TO dwh_etl;

-- result line on stdout (dwh_sql_package.py looks for it there)
SELECT 'MIGRATION_034_DONE' AS result;
