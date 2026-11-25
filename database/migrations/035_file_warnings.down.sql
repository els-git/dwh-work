-- Rollback of 035 (as dwh_admin, one transaction): the function is dropped, etl.v_object_load_status returns to
-- the text of 015 (without file_errors) with the same grant. Warnings already written stay in etl.error_log.
--   psql -v ON_ERROR_STOP=1 -1 -f 035_file_warnings.down.sql

DO $pre$
BEGIN
  IF to_regprocedure('etl.object_file_warning(bigint, text, text, text, text)') IS NULL THEN
    RAISE EXCEPTION 'STOP: 035 is not applied';
  END IF;
END
$pre$;

DROP FUNCTION etl.object_file_warning(bigint, text, text, text, text);
DROP VIEW etl.v_object_load_status;
CREATE VIEW etl.v_object_load_status AS
SELECT o.source_object_id, s.code AS source_system_code, o.source_schema, o.source_object, o.is_active,
  (EXISTS (SELECT 1 FROM meta.column_contract cc WHERE cc.source_system_code = s.code)
   OR EXISTS (SELECT 1 FROM meta.mapping_rule mr WHERE mr.source_system_code = s.code)) AS is_legacy,
  cv.version_no AS load_version_no, cc.status AS load_status, cv.load_strategy,
  cv.target_schema AS stg_schema, cv.target_table AS stg_table,
  ov.version_no AS ods_version_no, oc.status AS ods_status, ov.target_table AS ods_table,
  lr.run_id AS stg_run_id, lr.batch_run_id AS stg_batch_run_id, lr.status AS stg_run_status,
  lr.started_at AS stg_started_at, lr.finished_at AS stg_finished_at, lr.rows_read AS stg_rows_read,
  lr.rows_inserted AS stg_rows_inserted, lr.rows_updated AS stg_rows_updated, lr.error_code AS stg_error_code,
  (SELECT left(e.error_message, 2000) FROM etl.error_log e WHERE e.batch_run_id = lr.batch_run_id
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
  coalesce(rq.status IN ('QUEUED', 'CLAIMED', 'RUNNING'), false) AS has_active_request
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
REVOKE ALL ON etl.v_object_load_status FROM PUBLIC;
GRANT SELECT ON etl.v_object_load_status TO dwh_app;

SELECT 'ROLLBACK_035_DONE' AS result;
