-- E0 fixture for package 020 (sandbox only, never the working dwh): the old CRM contour as the I18 inventory found
-- it on the server (docs/DWH_I18_CRM_COMMON_LOGIC_PLAN_APPROVED_20261117.md, 5.1; report i18_step1b: columns R7,
-- foreign keys R1), with a few rows, plus object loads of OPERATION_COLLECTOR that must stay.
-- Applied on top of the E0 database of 019 (016 -> 017 -> 018 -> 019 with their fixtures). Run as a superuser.
SET ROLE dwh_admin;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS dq;

CREATE TABLE meta.source_system (source_system_id bigint PRIMARY KEY, code varchar NOT NULL UNIQUE, name varchar,
  source_type varchar, is_active boolean NOT NULL DEFAULT true);
CREATE TABLE meta.source_object (source_object_id bigint PRIMARY KEY,
  source_system_id bigint REFERENCES meta.source_system(source_system_id), source_schema varchar, source_object varchar,
  watermark_column varchar, is_active boolean NOT NULL DEFAULT true);
CREATE TABLE meta.source_connection (source_system_id bigint PRIMARY KEY
  REFERENCES meta.source_system(source_system_id) ON DELETE RESTRICT, host text);
CREATE TABLE meta.column_contract (contract_id bigint PRIMARY KEY, source_system_code varchar, source_object varchar,
  target_column varchar, is_active boolean, contract_root_id bigint REFERENCES meta.column_contract(contract_id),
  supersedes_contract_id bigint REFERENCES meta.column_contract(contract_id));
CREATE TABLE meta.mapping_rule (mapping_id bigint PRIMARY KEY, mapping_key varchar, source_system_code varchar,
  target_object varchar, is_active boolean, supersedes_mapping_id bigint REFERENCES meta.mapping_rule(mapping_id));
CREATE TABLE meta.mapping_source_ref (mapping_source_ref_id bigint PRIMARY KEY,
  mapping_id bigint REFERENCES meta.mapping_rule(mapping_id));
CREATE TABLE etl.job (job_id bigint PRIMARY KEY, code varchar NOT NULL UNIQUE, job_type varchar,
  source_system_id bigint REFERENCES meta.source_system(source_system_id),
  source_object_id bigint REFERENCES meta.source_object(source_object_id), target_schema varchar, target_object varchar,
  is_active boolean NOT NULL DEFAULT true);
CREATE TABLE etl.batch_run (batch_run_id bigint PRIMARY KEY, batch_code varchar, status varchar, pipeline_code varchar,
  source_batch_run_id bigint REFERENCES etl.batch_run(batch_run_id),
  root_batch_run_id bigint REFERENCES etl.batch_run(batch_run_id),
  resume_from_batch_run_id bigint REFERENCES etl.batch_run(batch_run_id),
  preflight_batch_run_id bigint REFERENCES etl.batch_run(batch_run_id));
CREATE TABLE etl.job_run (job_run_id bigint PRIMARY KEY, batch_run_id bigint REFERENCES etl.batch_run(batch_run_id),
  job_id bigint REFERENCES etl.job(job_id), status varchar,
  reused_from_job_run_id bigint REFERENCES etl.job_run(job_run_id));
CREATE TABLE etl.error_log (error_id bigint PRIMARY KEY, batch_run_id bigint REFERENCES etl.batch_run(batch_run_id),
  job_run_id bigint REFERENCES etl.job_run(job_run_id), job_id bigint REFERENCES etl.job(job_id),
  source_object_id bigint REFERENCES meta.source_object(source_object_id), error_code varchar);
CREATE TABLE etl.watermark (job_id bigint REFERENCES etl.job(job_id),
  source_object_id bigint REFERENCES meta.source_object(source_object_id), last_success_value text,
  last_success_run_id bigint REFERENCES etl.job_run(job_run_id));
CREATE TABLE meta.batch_contract_snapshot (batch_contract_snapshot_id bigint PRIMARY KEY,
  batch_run_id bigint REFERENCES etl.batch_run(batch_run_id), contract_id bigint REFERENCES meta.column_contract(contract_id));
CREATE TABLE meta.batch_mapping_snapshot (batch_mapping_snapshot_id bigint PRIMARY KEY,
  batch_run_id bigint REFERENCES etl.batch_run(batch_run_id), mapping_id bigint REFERENCES meta.mapping_rule(mapping_id));
CREATE TABLE dq.rule (rule_id bigint PRIMARY KEY, rule_code varchar, is_active boolean,
  rule_root_id bigint REFERENCES dq.rule(rule_id), supersedes_rule_id bigint REFERENCES dq.rule(rule_id));
CREATE TABLE dq.run (dq_run_id bigint PRIMARY KEY, source_batch_run_id bigint, status varchar);
CREATE TABLE dq.result (dq_result_id bigint PRIMARY KEY, dq_run_id bigint REFERENCES dq.run(dq_run_id),
  rule_id bigint REFERENCES dq.rule(rule_id), status varchar);
CREATE TABLE dq.issue (dq_issue_id bigint PRIMARY KEY, dq_run_id bigint REFERENCES dq.run(dq_run_id),
  dq_result_id bigint REFERENCES dq.result(dq_result_id), rule_id bigint REFERENCES dq.rule(rule_id));
CREATE TABLE dq.run_rule_snapshot (run_rule_snapshot_id bigint PRIMARY KEY, dq_run_id bigint REFERENCES dq.run(dq_run_id),
  rule_id bigint REFERENCES dq.rule(rule_id));
CREATE VIEW dq.v_current_rule AS SELECT rule_id, rule_code FROM dq.rule WHERE is_active;
CREATE TABLE etl.run_request (run_request_id bigint PRIMARY KEY, task_code varchar,
  source_batch_run_id bigint REFERENCES etl.batch_run(batch_run_id),
  result_batch_run_id bigint REFERENCES etl.batch_run(batch_run_id), result_dq_run_id bigint REFERENCES dq.run(dq_run_id));
CREATE TABLE meta.object_config_version (version_id bigint PRIMARY KEY,
  source_object_id bigint REFERENCES meta.source_object(source_object_id) ON DELETE RESTRICT);
CREATE TABLE etl.object_run (run_id bigint PRIMARY KEY, batch_run_id bigint REFERENCES etl.batch_run(batch_run_id) ON DELETE RESTRICT,
  job_run_id bigint REFERENCES etl.job_run(job_run_id) ON DELETE RESTRICT,
  source_object_id bigint REFERENCES meta.source_object(source_object_id) ON DELETE RESTRICT);
CREATE TABLE etl.object_ods_run (ods_run_id bigint PRIMARY KEY,
  batch_run_id bigint REFERENCES etl.batch_run(batch_run_id) ON DELETE RESTRICT,
  job_run_id bigint REFERENCES etl.job_run(job_run_id) ON DELETE RESTRICT,
  source_object_id bigint REFERENCES meta.source_object(source_object_id) ON DELETE RESTRICT);
-- As in 008 (decision M7): a source is legacy while it has contracts or mapping rules.
CREATE FUNCTION meta.source_is_legacy(p_source_system_id bigint) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  SELECT EXISTS (SELECT 1 FROM meta.source_system s
                  WHERE s.source_system_id = p_source_system_id
                    AND (EXISTS (SELECT 1 FROM meta.column_contract cc WHERE cc.source_system_code = s.code)
                         OR EXISTS (SELECT 1 FROM meta.mapping_rule mr WHERE mr.source_system_code = s.code)));
$fn$;
-- Views that read the old tables and must keep working (like etl.v_object_load_status of 015).
CREATE VIEW etl.v_i18_legacy_reader AS
  SELECT o.source_object_id, meta.source_is_legacy(o.source_system_id) AS is_legacy,
         (SELECT count(*) FROM meta.column_contract) AS contracts FROM meta.source_object o;

-- Objects of the old contour in stg/ods/dm (shape is not important; names and dependencies are).
DO $t$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['application', 'applicant', 'credit_info', 'workflow_history', 'product', 'stage', 'status',
                           'currency', 'sales_channel', 'branch', 'user', 'integration_log'] LOOP
    EXECUTE format('CREATE TABLE stg.%I (_dwh_landing_id bigserial PRIMARY KEY, _dwh_batch_run_id bigint, source_data jsonb)',
                   'crm_retail_' || t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['application', 'applicant', 'credit_info', 'workflow_history', 'product', 'stage', 'status',
                           'currency', 'sales_channel', 'branch', 'user', 'integration_event'] LOOP
    EXECUTE format('CREATE TABLE ods.%I (source_id bigint PRIMARY KEY, source_data jsonb)', 'crm_retail_' || t);
  END LOOP;
END
$t$;
INSERT INTO ods.crm_retail_application VALUES (101, '{}'), (102, '{}');
-- The E0 fixture of 016 has its own stubs of these dm objects: replaced by the shape below.
DROP VIEW IF EXISTS dm.v_retail_application_current, dm.v_retail_application_source;
DROP TABLE IF EXISTS dm.retail_application_history;
CREATE TABLE dm.retail_application_history (dm_application_version_id bigserial PRIMARY KEY, application_id bigint,
  is_current boolean);
INSERT INTO dm.retail_application_history (application_id, is_current) VALUES (101, true), (102, true);
CREATE VIEW dm.v_retail_application_source AS
  SELECT a.source_id AS application_id FROM ods.crm_retail_application a
    LEFT JOIN ods.crm_retail_status s ON s.source_id = a.source_id;
CREATE VIEW dm.v_retail_application_current AS SELECT * FROM dm.retail_application_history WHERE is_current;
CREATE FUNCTION dm.refresh_retail_application(p_batch bigint) RETURNS bigint
LANGUAGE sql SECURITY DEFINER AS $fn$ SELECT count(*) FROM dm.v_retail_application_source $fn$;
-- What stays: the ODS of OPERATION_COLLECTOR.
CREATE TABLE IF NOT EXISTS ods.operation_collector_asan_pension_requests (id bigint PRIMARY KEY, name text);
INSERT INTO ods.operation_collector_asan_pension_requests VALUES (1, 'a') ON CONFLICT DO NOTHING;

-- Rows.
INSERT INTO meta.source_system VALUES (1, 'CRM_RETAIL', 'CRM', 'POSTGRESQL', true), (5, 'OPERATION_COLLECTOR', 'OC', 'POSTGRESQL', true);
INSERT INTO meta.source_connection VALUES (1, 'crm-host'), (5, 'oc-host');
INSERT INTO meta.source_object (source_object_id, source_system_id, source_schema, source_object)
  SELECT g, 1, 'app_retail', 'object_' || g FROM generate_series(1, 12) g;
INSERT INTO meta.source_object VALUES (16, 5, 'public', 'asan_pension_requests', NULL, true);
INSERT INTO meta.column_contract VALUES (1, 'CRM_RETAIL', 'application', 'id', true, NULL, NULL),
  (2, 'CRM_RETAIL', 'application', 'id', true, 1, 1);
INSERT INTO meta.mapping_rule VALUES (1, 'm1', 'CRM_RETAIL', 'crm_retail_application', true, NULL),
  (2, 'm1', 'CRM_RETAIL', 'crm_retail_application', true, 1);
INSERT INTO meta.mapping_source_ref VALUES (1, 1), (2, 2);
INSERT INTO etl.job VALUES (1, 'EXTRACT_CRM_RETAIL_APPLICATION', 'EXTRACT', 1, 1, 'stg', 'crm_retail_application', true),
  (13, 'TRANSFORM_CRM_RETAIL_APPLICATION', 'TRANSFORM', 1, 1, 'ods', 'crm_retail_application', true),
  (25, 'REFRESH_DM_RETAIL_APPLICATION', 'MART', NULL, NULL, 'dm', 'retail_application_history', true),
  (26, 'OBJECT_LOAD_16', 'EXTRACT', 5, 16, 'stg', 'operation_collector_asan_pension_requests', true),
  (27, 'OBJECT_ODS_16', 'TRANSFORM', 5, 16, 'ods', 'operation_collector_asan_pension_requests', true);
INSERT INTO etl.batch_run (batch_run_id, batch_code, status, pipeline_code) VALUES
  (1, 'SNAPSHOT_1', 'SUCCESS', NULL), (6, 'ODS_6', 'SUCCESS', 'ODS_TRANSFORM'), (11, 'VAL_11', 'SUCCESS', 'STG_VALIDATION'),
  (15, 'DM_15', 'SUCCESS', 'DM_REFRESH'), (22, 'OBJECT_16', 'SUCCESS', 'OBJECT_LOAD'), (23, 'OBJECT_ODS_16', 'SUCCESS', 'OBJECT_LOAD');
UPDATE etl.batch_run SET source_batch_run_id = 1 WHERE batch_run_id IN (6, 11);
UPDATE etl.batch_run SET root_batch_run_id = 6 WHERE batch_run_id = 15;
INSERT INTO etl.job_run VALUES (1, 1, 1, 'SUCCESS', NULL), (2, 6, 13, 'SUCCESS', NULL), (3, 15, 25, 'SUCCESS', NULL),
  (4, 6, 13, 'SUCCESS', 2), (10, 22, 26, 'SUCCESS', NULL), (11, 23, 27, 'SUCCESS', NULL);
INSERT INTO etl.error_log VALUES (1, 6, 2, 13, 1, 'TYPE'), (2, 22, 10, 26, 16, 'SOURCE_NO_RESPONSE');
INSERT INTO etl.watermark VALUES (1, 1, '2026-10-01', 1), (26, 16, '2026-11-01', 10);
INSERT INTO meta.batch_contract_snapshot VALUES (1, 11, 1), (2, 11, 2);
INSERT INTO meta.batch_mapping_snapshot VALUES (1, 6, 1), (2, 6, 2);
INSERT INTO dq.rule VALUES (1, 'CRM_RETAIL_APPLICATION_COUNT', true, 1, NULL), (2, 'CRM_RETAIL_APPLICATION_STAGE', true, 2, NULL);
INSERT INTO dq.run VALUES (1, 6, 'PASSED');
INSERT INTO dq.result VALUES (1, 1, 1, 'PASS'), (2, 1, 2, 'WARN');
INSERT INTO dq.issue VALUES (1, 1, 2, 2);
INSERT INTO dq.run_rule_snapshot VALUES (1, 1, 1), (2, 1, 2);
INSERT INTO etl.run_request VALUES (1, 'TRANSFORM_ODS', 1, 6, NULL), (2, 'RUN_DQ', 6, NULL, 1);
INSERT INTO etl.object_run VALUES (1, 22, 10, 16);
INSERT INTO etl.object_ods_run VALUES (1, 23, 11, 16);
INSERT INTO meta.object_config_version VALUES (1, 16);
-- AI whitelist: the old view (removed) and a mart (stays).
INSERT INTO meta.ai_report_object (schema_name, object_name, title, required_permission_code, is_active, updated_by_user_id)
  SELECT 'dm', 'v_retail_application_current', 'Розничные заявки', 'REPORT_VIEW', true, 1
   WHERE NOT EXISTS (SELECT 1 FROM meta.ai_report_object WHERE object_name = 'v_retail_application_current');
INSERT INTO meta.ai_report_object (schema_name, object_name, title, required_permission_code, is_active, updated_by_user_id)
  VALUES ('dm', 'pension_request_i18', 'Пенсии', 'REPORT_VIEW', true, 1);
INSERT INTO meta.ai_value_column (object_id, column_name, max_values, is_active, updated_by_user_id)
  SELECT object_id, 'status_name', 20, true, 1 FROM meta.ai_report_object WHERE object_name = 'v_retail_application_current';
INSERT INTO meta.ai_glossary (term, meaning, object_id, is_active, updated_by_user_id)
  SELECT 'заявка', 'кредитная заявка', object_id, true, 1 FROM meta.ai_report_object WHERE object_name = 'v_retail_application_current';
INSERT INTO meta.ai_glossary (term, meaning, object_id, is_active, updated_by_user_id)
  VALUES ('общий термин', 'без объекта', NULL, true, 1);
GRANT USAGE ON SCHEMA dq TO dwh_app;
GRANT SELECT ON meta.source_system, meta.source_object, meta.column_contract, meta.mapping_rule, etl.job, etl.batch_run,
  dq.v_current_rule TO dwh_app;
RESET ROLE;
