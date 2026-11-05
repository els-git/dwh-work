-- I07 (step 2): load strategy of a published object version (plan I07, decisions D1-D5).
--
-- meta.object_config_version  + load_strategy FULL | INCREMENTAL, change_column (target column
--                             of the field map: date, time or integer), overlap_minutes (default
--                             10), delete_flag_column (BOOLEAN field: soft delete, D5). The
--                             definition (and so the hash of new drafts) contains "strategy";
--                             publish checks it. Existing versions get FULL / NULL / 10 / NULL;
--                             their stored hashes stay as published.
-- etl.object_run              + watermark_from / watermark_to (text of the change value), rows_stale,
--                             rows_deleted (D2: the watermark lives in the run history, not in
--                             the CRM table etl.watermark).
-- etl.object_run_begin        also returns the watermark of the last successful run of the same version.
-- etl.object_run_record_increment(run, from, to, stale, deleted)  for dwh_etl, same role, RUNNING run.
-- etl.object_record_change    change_kind may be DELETE (soft delete by the flag).
-- Functions of 008/010 are replaced in place (CREATE OR REPLACE keeps their grants);
-- the rollback restores the 008/010 definitions.
-- Requires 001-010. Run as the owner of schemas app, meta, etl:
--   psql -1 -v ON_ERROR_STOP=1 -f 011_object_load_strategy.sql

ALTER TABLE meta.object_config_version
  ADD COLUMN load_strategy text NOT NULL DEFAULT 'FULL' CHECK (load_strategy IN ('FULL', 'INCREMENTAL')),
  ADD COLUMN change_column text CHECK (change_column ~ '^[a-z_][a-z0-9_]{0,62}$'),
  ADD COLUMN overlap_minutes integer NOT NULL DEFAULT 10 CHECK (overlap_minutes BETWEEN 0 AND 10080),
  ADD COLUMN delete_flag_column text CHECK (delete_flag_column ~ '^[a-z_][a-z0-9_]{0,62}$');

ALTER TABLE etl.object_run
  ADD COLUMN watermark_from text CHECK (length(watermark_from) <= 100),
  ADD COLUMN watermark_to text CHECK (length(watermark_to) <= 100),
  ADD COLUMN rows_stale bigint NOT NULL DEFAULT 0 CHECK (rows_stale >= 0),
  ADD COLUMN rows_deleted bigint NOT NULL DEFAULT 0 CHECK (rows_deleted >= 0);

ALTER TABLE etl.object_record_change DROP CONSTRAINT object_record_change_change_kind_check;
ALTER TABLE etl.object_record_change ADD CONSTRAINT object_record_change_change_kind_check
  CHECK (change_kind IN ('INSERT', 'UPDATE', 'DELETE'));

CREATE OR REPLACE FUNCTION meta.object_config_definition(p_version_id bigint) RETURNS jsonb
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  SELECT jsonb_build_object(
    'target_schema', v.target_schema, 'target_table', v.target_table,
    'fields', coalesce((SELECT jsonb_agg(jsonb_build_object(
                'target_column', f.target_column, 'source_column', f.source_column,
                'source_type', f.source_type, 'target_type', f.target_type, 'conversion', f.conversion,
                'write_policy', f.write_policy, 'allow_explicit_null', f.allow_explicit_null)
              ORDER BY f.field_position) FROM meta.object_config_field f WHERE f.version_id = v.version_id), '[]'::jsonb),
    'key', coalesce((SELECT jsonb_agg(k.target_column ORDER BY k.key_position)
              FROM meta.object_config_key_part k WHERE k.version_id = v.version_id), '[]'::jsonb),
    'strategy', jsonb_build_object('load_strategy', v.load_strategy, 'change_column', v.change_column,
              'overlap_minutes', v.overlap_minutes, 'delete_flag_column', v.delete_flag_column))
    FROM meta.object_config_version v WHERE v.version_id = p_version_id;
$fn$;

CREATE OR REPLACE FUNCTION app.integration_object_config_save_draft(
  p_token_hash text, p_source_object_id bigint, p_expected_revision integer, p_config jsonb)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  o meta.source_object;
  d meta.object_config_version;
  v_current bigint;
  f jsonb;
  k jsonb;
  i integer := 0;
BEGIN
  SELECT * INTO o FROM meta.source_object x WHERE x.source_object_id = p_source_object_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source object not found'; END IF;
  IF NOT o.is_active THEN RAISE EXCEPTION 'Source object is not active'; END IF;
  IF meta.source_is_legacy(o.source_system_id) THEN
    RAISE EXCEPTION 'Legacy CRM sources keep their existing contracts and mappings';
  END IF;
  IF jsonb_typeof(p_config) IS DISTINCT FROM 'object' OR jsonb_typeof(p_config -> 'fields') IS DISTINCT FROM 'array'
     OR jsonb_typeof(coalesce(p_config -> 'key', '[]'::jsonb)) <> 'array' THEN
    RAISE EXCEPTION 'Invalid configuration document';
  END IF;
  SELECT * INTO d FROM meta.object_config_version x
   WHERE x.source_object_id = p_source_object_id AND x.status = 'DRAFT' FOR UPDATE;
  IF d.version_id IS NULL THEN
    IF p_expected_revision IS NOT NULL THEN RAISE EXCEPTION 'Draft not found or changed by another user'; END IF;
    SELECT version_id INTO v_current FROM meta.object_config_current WHERE source_object_id = p_source_object_id;
    INSERT INTO meta.object_config_version(source_object_id, version_no, based_on_version_id, created_by, updated_by)
    VALUES (p_source_object_id,
            coalesce((SELECT max(version_no) FROM meta.object_config_version WHERE source_object_id = p_source_object_id), 0) + 1,
            v_current, v_actor, v_actor)
    RETURNING * INTO d;
  ELSIF p_expected_revision IS DISTINCT FROM d.draft_revision THEN
    RAISE EXCEPTION 'Draft not found or changed by another user';
  ELSE
    DELETE FROM meta.object_config_key_part WHERE version_id = d.version_id;
    DELETE FROM meta.object_config_field WHERE version_id = d.version_id;
  END IF;
  FOR f IN SELECT * FROM jsonb_array_elements(p_config -> 'fields') LOOP
    i := i + 1;
    INSERT INTO meta.object_config_field(version_id, target_column, field_position, source_column, source_type,
        target_type, conversion, write_policy, allow_explicit_null)
    VALUES (d.version_id, f ->> 'target_column', i, f ->> 'source_column', f ->> 'source_type', f ->> 'target_type',
        coalesce(f ->> 'conversion', 'NONE'), coalesce(f ->> 'write_policy', 'AUTHORITATIVE'),
        coalesce((f ->> 'allow_explicit_null')::boolean, false));
  END LOOP;
  i := 0;
  FOR k IN SELECT * FROM jsonb_array_elements(coalesce(p_config -> 'key', '[]'::jsonb)) LOOP
    i := i + 1;
    INSERT INTO meta.object_config_key_part(version_id, key_position, target_column) VALUES (d.version_id, i, k #>> '{}');
  END LOOP;
  UPDATE meta.object_config_version
     SET target_schema = nullif(p_config ->> 'target_schema', ''), target_table = nullif(p_config ->> 'target_table', ''),
         load_strategy = coalesce(nullif(p_config ->> 'load_strategy', ''), 'FULL'),
         change_column = nullif(p_config ->> 'change_column', ''),
         overlap_minutes = coalesce((p_config ->> 'overlap_minutes')::integer, 10),
         delete_flag_column = nullif(p_config ->> 'delete_flag_column', ''),
         draft_revision = CASE WHEN p_expected_revision IS NULL THEN draft_revision ELSE draft_revision + 1 END,
         updated_at = clock_timestamp(), updated_by = v_actor
   WHERE version_id = d.version_id RETURNING * INTO d;
  UPDATE meta.object_config_version SET definition_hash = meta.object_config_hash(d.version_id)
   WHERE version_id = d.version_id;
  PERFORM audit.log_security_event('INTEGRATION_OBJECT_CONFIG_SAVED', 'SOURCE_OBJECT', p_source_object_id::text,
    CASE WHEN p_expected_revision IS NULL THEN 'CREATE' ELSE 'UPDATE' END, 'SUCCESS',
    jsonb_build_object('version_id', d.version_id, 'version_no', d.version_no, 'draft_revision', d.draft_revision,
                       'actor_user_id', v_actor));
  RETURN d.draft_revision;
END;
$fn$;

CREATE OR REPLACE FUNCTION app.integration_object_config_publish(
  p_token_hash text, p_source_object_id bigint, p_draft_revision integer)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  d meta.object_config_version;
  lv meta.object_config_validation;
  v_missing text;
  v_conflict text;
BEGIN
  SELECT * INTO d FROM meta.object_config_version x
   WHERE x.source_object_id = p_source_object_id AND x.status = 'DRAFT' FOR UPDATE;
  IF NOT FOUND OR d.draft_revision IS DISTINCT FROM p_draft_revision THEN
    RAISE EXCEPTION 'Draft not found or changed by another user';
  END IF;
  IF d.definition_hash IS DISTINCT FROM meta.object_config_hash(d.version_id) THEN
    RAISE EXCEPTION 'Draft hash mismatch';
  END IF;
  SELECT * INTO lv FROM meta.object_config_validation x WHERE x.version_id = d.version_id
   ORDER BY x.validation_id DESC LIMIT 1;
  IF NOT FOUND OR NOT lv.ok OR lv.draft_revision <> d.draft_revision OR lv.definition_hash <> d.definition_hash THEN
    RAISE EXCEPTION 'Publish requires a successful validation of this draft revision';
  END IF;
  IF d.target_schema IS NULL OR d.target_table IS NULL THEN RAISE EXCEPTION 'Target table not selected'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace
                  WHERE n.nspname = d.target_schema AND t.relname = d.target_table AND t.relkind IN ('r', 'p')) THEN
    RAISE EXCEPTION 'Target table %.% does not exist; apply its change proposal first', d.target_schema, d.target_table;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM meta.object_config_field WHERE version_id = d.version_id) THEN
    RAISE EXCEPTION 'Configuration has no fields';
  END IF;
  SELECT string_agg(f.target_column, ', ' ORDER BY f.field_position) INTO v_missing
    FROM meta.object_config_field f
   WHERE f.version_id = d.version_id
     AND NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_class t ON t.oid = a.attrelid
                       JOIN pg_namespace n ON n.oid = t.relnamespace
                      WHERE n.nspname = d.target_schema AND t.relname = d.target_table
                        AND a.attname = f.target_column AND a.attnum > 0 AND NOT a.attisdropped);
  IF v_missing IS NOT NULL THEN RAISE EXCEPTION 'Target columns do not exist: %', v_missing; END IF;
  IF NOT EXISTS (SELECT 1 FROM meta.object_config_key_part WHERE version_id = d.version_id) THEN
    RAISE EXCEPTION 'Business key not selected';
  END IF;
  IF EXISTS (SELECT 1 FROM meta.object_config_key_part k JOIN meta.object_config_field f
               ON f.version_id = k.version_id AND f.target_column = k.target_column
              WHERE k.version_id = d.version_id AND (f.write_policy = 'NO_WRITE' OR f.allow_explicit_null)) THEN
    RAISE EXCEPTION 'Key fields must be written and may not be NULL';
  END IF;
  -- I07: strategy of the version (package 011).
  IF d.load_strategy = 'INCREMENTAL' AND d.change_column IS NULL THEN
    RAISE EXCEPTION 'Incremental strategy requires a change column';
  END IF;
  IF d.change_column IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM meta.object_config_field f
        WHERE f.version_id = d.version_id AND f.target_column = d.change_column AND f.source_column IS NOT NULL
          AND f.write_policy <> 'NO_WRITE' AND f.target_type ~ '^(DATE|TIMESTAMP|TIMESTAMPTZ|INTEGER|BIGINT)$') THEN
    RAISE EXCEPTION 'Change column must be a written date, time or integer field';
  END IF;
  IF d.delete_flag_column IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM meta.object_config_field f
        WHERE f.version_id = d.version_id AND f.target_column = d.delete_flag_column AND f.source_column IS NOT NULL
          AND f.write_policy <> 'NO_WRITE' AND f.target_type = 'BOOLEAN') THEN
    RAISE EXCEPTION 'Delete flag must be a written BOOLEAN field';
  END IF;
  SELECT string_agg(DISTINCT f.target_column, ', ') INTO v_conflict
    FROM meta.object_config_field f
    JOIN meta.object_config_current oc ON oc.source_object_id <> p_source_object_id
    JOIN meta.object_config_version ov ON ov.version_id = oc.version_id
    JOIN meta.object_config_field of2 ON of2.version_id = ov.version_id AND of2.target_column = f.target_column
                                      AND of2.write_policy = 'AUTHORITATIVE'
   WHERE f.version_id = d.version_id AND f.write_policy = 'AUTHORITATIVE'
     AND ov.target_schema = d.target_schema AND ov.target_table = d.target_table;
  IF v_conflict IS NOT NULL THEN
    RAISE EXCEPTION 'Another object is already AUTHORITATIVE for: %', v_conflict;
  END IF;
  UPDATE meta.object_config_version
     SET status = 'PUBLISHED', published_at = clock_timestamp(), published_by = v_actor,
         updated_at = clock_timestamp(), updated_by = v_actor
   WHERE version_id = d.version_id;
  INSERT INTO meta.object_config_current(source_object_id, version_id, status, updated_by)
  VALUES (p_source_object_id, d.version_id, 'READY', v_actor)
  ON CONFLICT (source_object_id) DO UPDATE
     SET version_id = EXCLUDED.version_id, status = 'READY', status_details = '{}'::jsonb,
         updated_at = clock_timestamp(), updated_by = v_actor;
  PERFORM audit.log_security_event('INTEGRATION_OBJECT_CONFIG_PUBLISHED', 'SOURCE_OBJECT', p_source_object_id::text,
    'PUBLISH', 'SUCCESS', jsonb_build_object('version_id', d.version_id, 'version_no', d.version_no,
    'definition_hash', d.definition_hash, 'actor_user_id', v_actor));
  RETURN d.version_no;
END;
$fn$;

CREATE OR REPLACE FUNCTION etl.object_run_begin(p_source_object_id bigint)
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
    'watermark', (SELECT r.watermark_to FROM etl.object_run r
                   WHERE r.source_object_id = p_source_object_id AND r.version_id = v.version_id
                     AND r.status = 'SUCCESS' AND r.watermark_to IS NOT NULL
                   ORDER BY r.run_id DESC LIMIT 1),
    'source', jsonb_build_object('source_system_id', s.source_system_id, 'code', s.code, 'source_type', s.source_type,
                                 'source_schema', o.source_schema, 'source_object', o.source_object,
                                 'connection_config', sc.connection_config));
END;
$fn$;

CREATE FUNCTION etl.object_run_record_increment(p_run_id bigint, p_watermark_from text, p_watermark_to text,
  p_rows_stale bigint, p_rows_deleted bigint)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl AS $fn$
DECLARE
  r etl.object_run;
BEGIN
  IF coalesce(p_rows_stale, 0) < 0 OR coalesce(p_rows_deleted, 0) < 0
     OR length(p_watermark_from) > 100 OR length(p_watermark_to) > 100 THEN
    RAISE EXCEPTION 'Invalid increment result';
  END IF;
  SELECT * INTO r FROM etl.object_run x WHERE x.run_id = p_run_id FOR UPDATE;
  IF NOT FOUND OR r.status <> 'RUNNING' THEN RAISE EXCEPTION 'Object run not found or already finished'; END IF;
  IF r.started_by IS DISTINCT FROM session_user::text THEN RAISE EXCEPTION 'Object run belongs to another role'; END IF;
  UPDATE etl.object_run
     SET watermark_from = p_watermark_from, watermark_to = p_watermark_to,
         rows_stale = coalesce(p_rows_stale, 0), rows_deleted = coalesce(p_rows_deleted, 0)
   WHERE run_id = p_run_id;
  RETURN true;
END;
$fn$;

REVOKE ALL ON FUNCTION etl.object_run_record_increment(bigint, text, text, bigint, bigint) FROM PUBLIC;
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p, aclexplode(p.proacl) x
            WHERE p.oid = 'etl.object_run_record_increment(bigint, text, text, bigint, bigint)'::regprocedure
              AND x.grantee <> p.proowner LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION etl.object_run_record_increment(bigint, text, text, bigint, bigint) FROM %s',
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED FUNCTION etl.object_run_record_increment FROM %',
                 CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(r.grantee) END;
  END LOOP;
END
$acl$;
GRANT EXECUTE ON FUNCTION etl.object_run_record_increment(bigint, text, text, bigint, bigint) TO dwh_etl;
