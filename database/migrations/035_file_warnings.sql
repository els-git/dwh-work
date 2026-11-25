-- I12 (plan docs/DWH_I12_FILE_CONNECTOR_PLAN_APPROVED_20261125.md, section 7.7, decision A of 25.11.2026):
-- a file refused while the load chooses files (broken, XML with DOCTYPE, too large, no actuality date) is
-- visible outside the register: a warning in the error journal and a count on the object in "Загрузки".
-- Run as the owner of the schemas (dwh_admin) in one transaction:
--   psql -v ON_ERROR_STOP=1 -1 -f 035_file_warnings.sql
-- etl.object_file_warning (dwh_etl, only for a RUNNING run of a FILE object): one row in etl.error_log with
--   error_stage 'FILE_CHECK' and diagnostic_context {"severity": "WARNING", "file", "sha256"} - once per object,
--   content and code (a file retried by the next runs is not reported again). The run stays SUCCESS.
-- etl.v_object_load_status: new last column file_errors (files refused by the last run of the object);
--   the error message of the last STG run ignores these warnings.
-- No table and no row is changed.

DO $pre$
BEGIN
  IF to_regprocedure('etl.object_file_warning(bigint, text, text, text, text)') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 035 is already applied';
  END IF;
  IF to_regprocedure('etl.object_file_record(bigint, text, bigint, text, timestamp with time zone, text, bigint, text)') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 034 is required';
  END IF;
END
$pre$;

CREATE FUNCTION etl.object_file_warning(p_run_id bigint, p_file_name text, p_sha256 text, p_code text,
  p_message text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl AS $fn$
DECLARE
  v_object bigint := etl.object_file_running(p_run_id);
  r etl.object_run;
  v_job bigint;
BEGIN
  IF p_code IS NULL OR p_code !~ '^FILE_[A-Z_]{1,60}$' THEN RAISE EXCEPTION 'Bad file warning code'; END IF;
  IF p_sha256 IS NULL OR p_sha256 !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'Bad file content hash'; END IF;
  IF p_file_name IS NULL OR p_file_name = '' OR p_file_name ~ '[/\\]' OR length(p_file_name) > 255 THEN
    RAISE EXCEPTION 'Bad file name';
  END IF;
  IF EXISTS (SELECT 1 FROM etl.error_log e
              WHERE e.source_object_id = v_object AND e.error_stage = 'FILE_CHECK' AND e.error_code = p_code
                AND e.diagnostic_context ->> 'sha256' = p_sha256) THEN
    RETURN false;
  END IF;
  SELECT * INTO r FROM etl.object_run x WHERE x.run_id = p_run_id;
  SELECT j.job_id INTO v_job FROM etl.job_run j WHERE j.job_run_id = r.job_run_id;
  INSERT INTO etl.error_log (batch_run_id, job_run_id, job_id, source_object_id, error_stage, error_code,
      error_message, diagnostic_context)
  VALUES (r.batch_run_id, r.job_run_id, v_job, v_object, 'FILE_CHECK', p_code,
      left(p_file_name || ': ' || coalesce(p_message, p_code), 2000),
      jsonb_build_object('severity', 'WARNING', 'file', p_file_name, 'sha256', p_sha256));
  RETURN true;
END;
$fn$;

CREATE OR REPLACE VIEW etl.v_object_load_status AS
SELECT o.source_object_id, s.code AS source_system_code, o.source_schema, o.source_object, o.is_active,
  (EXISTS (SELECT 1 FROM meta.column_contract cc WHERE cc.source_system_code = s.code)
   OR EXISTS (SELECT 1 FROM meta.mapping_rule mr WHERE mr.source_system_code = s.code)) AS is_legacy,
  cv.version_no AS load_version_no, cc.status AS load_status, cv.load_strategy,
  cv.target_schema AS stg_schema, cv.target_table AS stg_table,
  ov.version_no AS ods_version_no, oc.status AS ods_status, ov.target_table AS ods_table,
  lr.run_id AS stg_run_id, lr.batch_run_id AS stg_batch_run_id, lr.status AS stg_run_status,
  lr.started_at AS stg_started_at, lr.finished_at AS stg_finished_at, lr.rows_read AS stg_rows_read,
  lr.rows_inserted AS stg_rows_inserted, lr.rows_updated AS stg_rows_updated, lr.error_code AS stg_error_code,
  (SELECT left(e.error_message, 2000) FROM etl.error_log e WHERE e.batch_run_id = lr.batch_run_id AND e.error_stage IS DISTINCT FROM 'FILE_CHECK'
    ORDER BY e.error_id DESC LIMIT 1) AS stg_error_message,
  lo.ods_run_id, lo.batch_run_id AS ods_batch_run_id, lo.status AS ods_run_status, lo.stg_run_id AS ods_from_stg_run_id,
  lo.started_at AS ods_started_at, lo.finished_at AS ods_finished_at, lo.rows_read AS ods_rows_read,
  lo.rows_inserted AS ods_rows_inserted, lo.rows_updated AS ods_rows_updated, lo.error_code AS ods_error_code,
  (SELECT left(e.error_message, 2000) FROM etl.error_log e WHERE e.batch_run_id = lo.batch_run_id
    ORDER BY e.error_id DESC LIMIT 1) AS ods_error_message,
  coalesce(lr.status = 'SUCCESS' AND lo.status = 'FAILED' AND lo.started_at >= lr.finished_at
   AND oc.status = 'READY', false) AS can_continue_ods,
  rq.request_id AS last_request_id, rq.run_kind AS last_request_kind, rq.status AS last_request_status,
  rq.requested_by_username AS last_request_by, rq.requested_at AS last_request_at,
  rq.finished_at AS last_request_finished_at, rq.result_code AS last_request_result, rq.message AS last_request_message,
  coalesce(rq.status IN ('QUEUED', 'CLAIMED', 'RUNNING'), false) AS has_active_request,
  -- 035: files of a FILE object refused by its last run (broken, DOCTYPE, too large, no date)
  (SELECT count(*) FROM etl.object_file f WHERE f.source_object_id = o.source_object_id
     AND f.status IN ('FAILED', 'NO_DATE') AND f.run_id = lr.run_id) AS file_errors
FROM meta.source_object o
JOIN meta.source_system s ON s.source_system_id = o.source_system_id
LEFT JOIN meta.object_config_current cc ON cc.source_object_id = o.source_object_id
LEFT JOIN meta.object_config_version cv ON cv.version_id = cc.version_id
LEFT JOIN meta.object_ods_current oc ON oc.source_object_id = o.source_object_id
LEFT JOIN meta.object_ods_version ov ON ov.ods_version_id = oc.ods_version_id
LEFT JOIN LATERAL (SELECT * FROM etl.object_run r WHERE r.source_object_id = o.source_object_id
                    ORDER BY r.run_id DESC LIMIT 1) lr ON true
LEFT JOIN LATERAL (SELECT * FROM etl.object_ods_run r WHERE r.source_object_id = o.source_object_id
                    ORDER BY r.ods_run_id DESC LIMIT 1) lo ON true
LEFT JOIN LATERAL (SELECT * FROM etl.object_run_request r WHERE r.source_object_id = o.source_object_id
                    ORDER BY r.request_id DESC LIMIT 1) rq ON true;

REVOKE ALL ON FUNCTION etl.object_file_warning(bigint, text, text, text, text) FROM PUBLIC;

-- rule since 005: default grants of the schemas are removed from the new objects and reported
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT 'FUNCTION ' || p.oid::regprocedure::text AS obj, x.grantee
      FROM pg_proc p, aclexplode(p.proacl) x
     WHERE p.oid = 'etl.object_file_warning(bigint, text, text, text, text)'::regprocedure AND x.grantee <> p.proowner
  LOOP
    EXECUTE format('REVOKE ALL ON %s FROM %s', r.obj,
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED % FROM %', r.obj,
                 CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(r.grantee) END;
  END LOOP;
END
$acl$;

GRANT EXECUTE ON FUNCTION etl.object_file_warning(bigint, text, text, text, text) TO dwh_etl;

-- result line on stdout (dwh_sql_package.py looks for it there)
SELECT 'MIGRATION_035_DONE' AS result;
