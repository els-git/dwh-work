-- I06 (step 3): run of a published object configuration (plan I06, decisions R4, R5, R7).
--
-- etl.object_run            binds one run to the published version in force (provenance):
--                           object, version, definition hash, batch_run / job_run, counts, result.
-- etl.object_record_change  append-only history of applied rows: business key, INSERT or
--                           UPDATE, changed fields {"col": {"before": .., "after": ..}} (R5 = I02-02).
-- etl.object_run_begin(id)  for dwh_etl: checks the object (active, not legacy, version in force
--                           READY, connection not disabled), takes a session advisory lock per
--                           object (a second concurrent run is refused; a RUNNING run left by a
--                           dead session is closed as ABANDONED), registers the object job in
--                           etl.job once (code OBJECT_LOAD_<id>, type EXTRACT), opens batch_run
--                           (pipeline OBJECT_LOAD, MANUAL, FULL) + job_run + object_run and
--                           returns the definition and the non-secret connection settings.
-- etl.object_run_finish(..) for dwh_etl, same database role only: closes the run (SUCCESS or
--                           FAILED with an error code), writes etl.error_log on failure, releases
--                           the lock. The ETL calls it in the SAME transaction as the data on
--                           success, so data and result commit together.
-- etl.v_integration_run_history (decision R7 = B): also shows pipeline OBJECT_LOAD; the
--                           definition is otherwise unchanged (rebuilt from pg_get_viewdef).
-- Nothing changes in batch_run / job_run / job / error_log structures or CRM rows.
-- Privileges: tables only for the owner, except INSERT on object_record_change for dwh_etl;
-- EXECUTE on the two functions only for dwh_etl; default grants of schema etl removed.
-- Requires 001-009. Run as the owner of schemas etl and meta:
--   psql -1 -v ON_ERROR_STOP=1 -f 010_object_run.sql

CREATE TABLE etl.object_run (
  run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_object_id bigint NOT NULL REFERENCES meta.source_object(source_object_id) ON DELETE RESTRICT,
  version_id bigint NOT NULL REFERENCES meta.object_config_version(version_id) ON DELETE RESTRICT,
  definition_hash text NOT NULL CHECK (definition_hash ~ '^[0-9a-f]{64}$'),
  batch_run_id bigint NOT NULL UNIQUE REFERENCES etl.batch_run(batch_run_id) ON DELETE RESTRICT,
  job_run_id bigint NOT NULL UNIQUE REFERENCES etl.job_run(job_run_id) ON DELETE RESTRICT,
  status text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
  rows_read bigint NOT NULL DEFAULT 0 CHECK (rows_read >= 0),
  rows_inserted bigint NOT NULL DEFAULT 0 CHECK (rows_inserted >= 0),
  rows_updated bigint NOT NULL DEFAULT 0 CHECK (rows_updated >= 0),
  rows_unchanged bigint NOT NULL DEFAULT 0 CHECK (rows_unchanged >= 0),
  error_code text CHECK (error_code ~ '^[A-Z][A-Z0-9_]{0,59}$'),
  started_by text NOT NULL,
  started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  finished_at timestamptz,
  CONSTRAINT object_run_result CHECK (
    (status = 'RUNNING' AND finished_at IS NULL AND error_code IS NULL)
    OR (status = 'SUCCESS' AND finished_at IS NOT NULL AND error_code IS NULL)
    OR (status = 'FAILED' AND finished_at IS NOT NULL AND error_code IS NOT NULL))
);
CREATE INDEX object_run_object ON etl.object_run(source_object_id, run_id);

CREATE TABLE etl.object_record_change (
  change_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id bigint NOT NULL REFERENCES etl.object_run(run_id) ON DELETE RESTRICT,
  change_kind text NOT NULL CHECK (change_kind IN ('INSERT', 'UPDATE')),
  record_key jsonb NOT NULL CHECK (jsonb_typeof(record_key) = 'object'),
  changes jsonb NOT NULL CHECK (jsonb_typeof(changes) = 'object'),
  changed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX object_record_change_run ON etl.object_record_change(run_id);
CREATE INDEX object_record_change_key ON etl.object_record_change USING gin (record_key);

-- History rows only for a RUNNING run; never changed or removed.
CREATE FUNCTION etl.object_record_change_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl AS $fn$
BEGIN
  IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION '% of object record history prohibited', TG_OP; END IF;
  IF NOT EXISTS (SELECT 1 FROM etl.object_run r WHERE r.run_id = NEW.run_id AND r.status = 'RUNNING') THEN
    RAISE EXCEPTION 'Record history requires a running object run';
  END IF;
  RETURN NEW;
END;
$fn$;
CREATE TRIGGER object_record_change_insert BEFORE INSERT ON etl.object_record_change
FOR EACH ROW EXECUTE FUNCTION etl.object_record_change_guard();
CREATE TRIGGER object_record_change_immutable BEFORE UPDATE OR DELETE ON etl.object_record_change
FOR EACH ROW EXECUTE FUNCTION etl.object_record_change_guard();
CREATE TRIGGER object_record_change_truncate BEFORE TRUNCATE ON etl.object_record_change
FOR EACH STATEMENT EXECUTE FUNCTION etl.object_record_change_guard();

-- Close a run (shared by finish and by ABANDONED handling in begin).
CREATE FUNCTION etl.object_run_close(p_run_id bigint, p_ok boolean, p_rows_read bigint, p_rows_inserted bigint,
  p_rows_updated bigint, p_rows_unchanged bigint, p_error_code text, p_error_message text, p_record_key jsonb,
  p_column_name text)
RETURNS text
LANGUAGE plpgsql SET search_path = pg_catalog, etl AS $fn$
DECLARE
  r etl.object_run;
  v_job bigint;
  v_status text := CASE WHEN p_ok THEN 'SUCCESS' ELSE 'FAILED' END;
  v_message text := CASE WHEN p_ok THEN 'Object load completed' ELSE left(coalesce(p_error_message, p_error_code), 2000) END;
BEGIN
  UPDATE etl.object_run
     SET status = v_status, rows_read = coalesce(p_rows_read, 0), rows_inserted = coalesce(p_rows_inserted, 0),
         rows_updated = coalesce(p_rows_updated, 0), rows_unchanged = coalesce(p_rows_unchanged, 0),
         error_code = CASE WHEN p_ok THEN NULL ELSE p_error_code END, finished_at = clock_timestamp()
   WHERE run_id = p_run_id AND status = 'RUNNING'
  RETURNING * INTO r;
  IF NOT FOUND THEN RAISE EXCEPTION 'Object run not found or already finished'; END IF;
  UPDATE etl.job_run
     SET status = v_status, finished_at = clock_timestamp(), rows_read = r.rows_read, rows_inserted = r.rows_inserted,
         rows_updated = r.rows_updated, error_count = CASE WHEN p_ok THEN 0 ELSE 1 END, message = v_message
   WHERE job_run_id = r.job_run_id
  RETURNING job_id INTO v_job;
  UPDATE etl.batch_run SET status = v_status, finished_at = clock_timestamp(), message = v_message
   WHERE batch_run_id = r.batch_run_id;
  IF NOT p_ok THEN
    INSERT INTO etl.error_log(batch_run_id, job_run_id, job_id, source_object_id, error_stage, error_code,
        error_message, record_key, column_name)
    VALUES (r.batch_run_id, r.job_run_id, v_job, r.source_object_id, 'OBJECT_LOAD', p_error_code, v_message,
        p_record_key, left(p_column_name, 200));
  END IF;
  RETURN v_status;
END;
$fn$;

CREATE FUNCTION etl.object_run_begin(p_source_object_id bigint)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl, meta, audit AS $fn$
DECLARE
  o meta.source_object;
  s meta.source_system;
  c meta.object_config_current;
  v meta.object_config_version;
  sc meta.source_connection;
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
  SELECT * INTO sc FROM meta.source_connection x WHERE x.source_system_id = o.source_system_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source has no connection settings'; END IF;
  IF sc.status = 'DISABLED' THEN RAISE EXCEPTION 'Source connection is disabled'; END IF;
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
    'source', jsonb_build_object('source_system_id', s.source_system_id, 'code', s.code, 'source_type', s.source_type,
                                 'source_schema', o.source_schema, 'source_object', o.source_object,
                                 'connection_config', sc.connection_config));
END;
$fn$;

CREATE FUNCTION etl.object_run_finish(p_run_id bigint, p_ok boolean, p_rows_read bigint, p_rows_inserted bigint,
  p_rows_updated bigint, p_rows_unchanged bigint, p_error_code text, p_error_message text, p_record_key jsonb,
  p_column_name text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl, audit AS $fn$
DECLARE
  r etl.object_run;
  v_status text;
BEGIN
  IF p_ok IS NULL OR (NOT p_ok AND (p_error_code IS NULL OR p_error_code !~ '^[A-Z][A-Z0-9_]{0,59}$'))
     OR (p_ok AND p_error_code IS NOT NULL)
     OR (p_record_key IS NOT NULL AND jsonb_typeof(p_record_key) <> 'object') THEN
    RAISE EXCEPTION 'Invalid run result';
  END IF;
  SELECT * INTO r FROM etl.object_run x WHERE x.run_id = p_run_id FOR UPDATE;
  IF NOT FOUND OR r.status <> 'RUNNING' THEN RAISE EXCEPTION 'Object run not found or already finished'; END IF;
  IF r.started_by IS DISTINCT FROM session_user::text THEN RAISE EXCEPTION 'Object run belongs to another role'; END IF;
  PERFORM set_config('dwh.security_source', 'ETL', true);
  v_status := etl.object_run_close(p_run_id, p_ok, p_rows_read, p_rows_inserted, p_rows_updated, p_rows_unchanged,
    p_error_code, p_error_message, p_record_key, p_column_name);
  PERFORM pg_advisory_unlock(hashtext('dwh.object_run'), r.source_object_id::integer);
  PERFORM audit.log_security_event('ETL_OBJECT_RUN_FINISHED', 'SOURCE_OBJECT', r.source_object_id::text, 'RUN',
    CASE WHEN p_ok THEN 'SUCCESS' ELSE 'FAILURE' END,
    jsonb_build_object('run_id', p_run_id, 'status', v_status, 'error_code', p_error_code,
                       'rows_read', p_rows_read, 'rows_inserted', p_rows_inserted, 'rows_updated', p_rows_updated));
  RETURN v_status;
END;
$fn$;

-- R7 = B: the run list of the UI also shows OBJECT_LOAD. Rebuilt from the current definition
-- so that every other part of the view stays as it is.
DO $view$
DECLARE
  v_def text;
  v_old text := '= ''ODS_TRANSFORM''::text';
  v_new text := '= ANY (ARRAY[''ODS_TRANSFORM''::text, ''OBJECT_LOAD''::text])';
BEGIN
  SET LOCAL search_path = pg_catalog;
  v_def := pg_get_viewdef('etl.v_integration_run_history'::regclass, true);
  IF (length(v_def) - length(replace(v_def, v_old, ''))) / length(v_old) <> 1 THEN
    RAISE EXCEPTION 'STOP: etl.v_integration_run_history differs from the definition read on 05.11.2026';
  END IF;
  EXECUTE 'CREATE OR REPLACE VIEW etl.v_integration_run_history AS ' || replace(v_def, v_old, v_new);
  RAISE NOTICE 'VIEW_UPDATED etl.v_integration_run_history';
END
$view$;

REVOKE ALL ON etl.object_run, etl.object_record_change FROM PUBLIC;
REVOKE ALL ON FUNCTION
  etl.object_record_change_guard(),
  etl.object_run_close(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text),
  etl.object_run_begin(bigint),
  etl.object_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text)
FROM PUBLIC;

-- Default privileges of schema etl gave the new tables, sequences and functions to other roles:
-- remove them explicitly (rule since 005) and report.
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT DISTINCT CASE c.relkind WHEN 'S' THEN 'SEQUENCE ' ELSE 'TABLE ' END || c.oid::regclass::text AS obj, x.grantee
      FROM pg_class c, aclexplode(c.relacl) x
     WHERE c.relnamespace = 'etl'::regnamespace
       AND (c.relname IN ('object_run', 'object_record_change')
            OR c.oid IN (SELECT d.objid FROM pg_depend d
                          WHERE d.refobjid IN ('etl.object_run'::regclass, 'etl.object_record_change'::regclass)
                            AND d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')))
       AND x.grantee <> c.relowner
    UNION
    SELECT 'FUNCTION ' || p.oid::regprocedure::text, x.grantee
      FROM pg_proc p, aclexplode(p.proacl) x
     WHERE p.oid IN ('etl.object_record_change_guard()'::regprocedure,
                     'etl.object_run_close(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text)'::regprocedure,
                     'etl.object_run_begin(bigint)'::regprocedure,
                     'etl.object_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text)'::regprocedure)
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
  etl.object_run_begin(bigint),
  etl.object_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text)
TO dwh_etl;
GRANT INSERT ON etl.object_record_change TO dwh_etl;
