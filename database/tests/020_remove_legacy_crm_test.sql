-- I18 test of 020 (after the package): CRM_RETAIL is an ordinary source, what stays is intact, the old mechanism
-- tables are empty but readable. One transaction, ROLLED BACK. For E0 and E1, never the working dwh.
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 020_remove_legacy_crm_test.sql
-- Expected last line: ALL_TESTS_PASS (labels L01-L08: 8 checks).
BEGIN;

CREATE FUNCTION pg_temp.expect(label text, ok boolean) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  IF ok IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', label; END IF;
  RAISE NOTICE 'PASS %', label;
END
$fn$;

SELECT pg_temp.expect('L01 CRM_RETAIL is not a legacy source',
  NOT meta.source_is_legacy((SELECT source_system_id FROM meta.source_system WHERE code = 'CRM_RETAIL')));
SELECT pg_temp.expect('L02 CRM_RETAIL keeps its connection and objects',
  EXISTS (SELECT 1 FROM meta.source_connection c JOIN meta.source_system s ON s.source_system_id = c.source_system_id
           WHERE s.code = 'CRM_RETAIL')
  AND (SELECT count(*) FROM meta.source_object o JOIN meta.source_system s ON s.source_system_id = o.source_system_id
        WHERE s.code = 'CRM_RETAIL') >= 1);
SELECT pg_temp.expect('L03 object loads stay: runs, job runs, object runs, jobs OBJECT_*',
  EXISTS (SELECT 1 FROM etl.batch_run WHERE pipeline_code = 'OBJECT_LOAD')
  AND NOT EXISTS (SELECT 1 FROM etl.object_run r WHERE NOT EXISTS (SELECT 1 FROM etl.job_run j WHERE j.job_run_id = r.job_run_id))
  AND EXISTS (SELECT 1 FROM etl.job WHERE code LIKE 'OBJECT\_%'));
SELECT pg_temp.expect('L04 ODS of OPERATION_COLLECTOR stays',
  to_regclass('ods.operation_collector_asan_pension_requests') IS NOT NULL);
SELECT pg_temp.expect('L05 old queue and DQ are empty',
  NOT EXISTS (SELECT 1 FROM etl.run_request) AND NOT EXISTS (SELECT 1 FROM dq.rule) AND NOT EXISTS (SELECT 1 FROM dq.run)
  AND NOT EXISTS (SELECT 1 FROM dq.result) AND NOT EXISTS (SELECT 1 FROM dq.issue));
SELECT pg_temp.expect('L06 no errors, watermarks or job runs of removed jobs',
  NOT EXISTS (SELECT 1 FROM etl.error_log e WHERE NOT EXISTS (SELECT 1 FROM etl.job j WHERE j.job_id = e.job_id))
  AND NOT EXISTS (SELECT 1 FROM etl.watermark w WHERE NOT EXISTS (SELECT 1 FROM etl.job j WHERE j.job_id = w.job_id))
  AND NOT EXISTS (SELECT 1 FROM etl.job_run r WHERE NOT EXISTS (SELECT 1 FROM etl.job j WHERE j.job_id = r.job_id)));
SELECT pg_temp.expect('L07 AI: other whitelist rows and terms without an object stay',
  NOT EXISTS (SELECT 1 FROM meta.ai_value_column v WHERE NOT EXISTS (SELECT 1 FROM meta.ai_report_object o WHERE o.object_id = v.object_id))
  AND NOT EXISTS (SELECT 1 FROM meta.ai_glossary g WHERE g.object_id IS NOT NULL
                   AND NOT EXISTS (SELECT 1 FROM meta.ai_report_object o WHERE o.object_id = g.object_id)));
-- Decision M7 is unchanged: a source with contracts or mapping rules is still legacy (definition of 008).
SELECT pg_temp.expect('L08 the legacy marker M7 still reads contracts and mapping rules',
  (SELECT prosrc FROM pg_proc WHERE oid = 'meta.source_is_legacy(bigint)'::regprocedure) ~ 'column_contract'
  AND (SELECT prosrc FROM pg_proc WHERE oid = 'meta.source_is_legacy(bigint)'::regprocedure) ~ 'mapping_rule');

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
