-- Rollback of 034 (I12): removes the file objects support. Run as dwh_admin in one transaction:
--   psql -v ON_ERROR_STOP=1 -1 -f 034_file_source.down.sql
-- Refuses (changes nothing) while file objects, their settings or registered files exist:
-- they would lose their meaning; delete the file objects in the interface first.

DO $pre$
BEGIN
  IF to_regprocedure('etl.object_file_record(bigint, text, bigint, text, timestamp with time zone, text, bigint, text)') IS NULL THEN
    RAISE EXCEPTION 'STOP: 034 is not applied';
  END IF;
  IF EXISTS (SELECT 1 FROM meta.source_object WHERE object_type = 'FILE') THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: file objects exist';
  END IF;
  IF EXISTS (SELECT 1 FROM meta.object_file_config) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: file settings exist';
  END IF;
  IF EXISTS (SELECT 1 FROM etl.object_file) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: registered files exist';
  END IF;
END
$pre$;

-- etl.object_run_begin as in package 033
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

DROP FUNCTION etl.object_file_record(bigint, text, bigint, text, timestamptz, text, bigint, text);
DROP FUNCTION etl.object_file_state(bigint);
DROP FUNCTION etl.object_file_running(bigint);
DROP FUNCTION app.integration_file_registry(text, bigint, integer);
DROP FUNCTION app.integration_file_object_capture(text, bigint, jsonb);
DROP FUNCTION app.integration_file_config_save(text, bigint, jsonb);
DROP FUNCTION app.integration_file_config_get(text, bigint);
DROP FUNCTION app.integration_file_object_create(text, bigint, text, text);
DROP FUNCTION app.integration_file_object(bigint, boolean);
DROP TABLE etl.object_file;
DROP TRIGGER trg_object_file_config_copy ON meta.object_config_version;
DROP FUNCTION meta.object_file_config_copy_to_draft();
DROP TABLE meta.object_file_config;
DROP FUNCTION meta.object_file_reject_truncate();
DROP FUNCTION meta.object_file_config_guard();

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

SELECT 'ROLLBACK_034_DONE' AS result;
