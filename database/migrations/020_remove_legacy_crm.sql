-- I18 (step 2): removal of the old CRM contour -- package 020
-- (plan docs/DWH_I18_CRM_COMMON_LOGIC_PLAN_APPROVED_20261117.md; decision of the user 17.11.2026: everything of the
-- old CRM is removed, test data, no export; CRM is connected again as an ordinary source).
--
-- Removed:
--   dm   views v_retail_application_current, v_retail_application_source, function refresh_retail_application(bigint),
--        table retail_application_history;
--   stg  12 tables crm_retail_* (and their landing sequences); ods 12 tables crm_retail_*;
--   meta CRM_RETAIL rows of column_contract, mapping_rule, mapping_source_ref and their batch snapshots;
--   etl  jobs of the old contour (EXTRACT_/TRANSFORM_CRM_RETAIL_*, REFRESH_DM_RETAIL_APPLICATION) with their job runs,
--        errors and watermarks; batch runs of the old pipelines (every pipeline except OBJECT_LOAD); the old queue
--        etl.run_request;
--   dq   all rules (14, all CRM_RETAIL_*), runs, results, issues, rule snapshots;
--   AI   the whitelist row dm.v_retail_application_current with its value columns, examples and terms.
-- Kept: source CRM_RETAIL with its connection and SecretStore binding, its source objects 1-12 and structure
--   snapshots (without contracts and mapping rules meta.source_is_legacy() is false: they become ordinary objects),
--   OPERATION_COLLECTOR, object loads (OBJECT_LOAD), marts of the builder, saved reports (a report on the removed
--   object becomes unavailable; its owner deletes it in the interface), all app.* rows, all journals of app/audit.
-- The empty tables of the old mechanism (contracts, mappings, DQ, old queue) stay: views of 015 read them; their
-- screens and code are removed in step 3.
-- Adds the marker meta.legacy_crm_removed() (dwh_sql_package.py finds an applied package by its marker).
-- Every guard failure is a STOP before any change; the whole file runs in ONE transaction. Rows cannot be brought
-- back by a script: the rollback of the data is the copy pre-020 that E2 takes (dwh_backup.py). Run as the owner:
--   psql -1 -v ON_ERROR_STOP=1 -f 020_remove_legacy_crm.sql
DO $pre$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regprocedure('meta.legacy_crm_removed()') IS NOT NULL THEN RAISE EXCEPTION 'STOP: 020 is already applied'; END IF;
  IF to_regclass('meta.dm_mart') IS NULL OR to_regprocedure('meta.mart_builder_guard()') IS NULL THEN
    RAISE EXCEPTION 'STOP: 019 must be applied first';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM meta.source_system WHERE code = 'CRM_RETAIL') THEN
    RAISE EXCEPTION 'STOP: source CRM_RETAIL not found';
  END IF;
END
$pre$;

-- Sets of the old contour, fixed before any change.
CREATE TEMP TABLE i18_old_job ON COMMIT DROP AS
  SELECT job_id FROM etl.job
   WHERE code ~ '^(EXTRACT|TRANSFORM)_CRM_RETAIL_' OR code = 'REFRESH_DM_RETAIL_APPLICATION';
CREATE TEMP TABLE i18_old_batch ON COMMIT DROP AS
  SELECT batch_run_id FROM etl.batch_run WHERE pipeline_code IS DISTINCT FROM 'OBJECT_LOAD';
CREATE TEMP TABLE i18_old_job_run ON COMMIT DROP AS
  SELECT job_run_id FROM etl.job_run
   WHERE job_id IN (SELECT job_id FROM i18_old_job) OR batch_run_id IN (SELECT batch_run_id FROM i18_old_batch);
CREATE TEMP TABLE i18_old_mapping ON COMMIT DROP AS
  SELECT mapping_id FROM meta.mapping_rule WHERE source_system_code = 'CRM_RETAIL';
CREATE TEMP TABLE i18_old_contract ON COMMIT DROP AS
  SELECT contract_id FROM meta.column_contract WHERE source_system_code = 'CRM_RETAIL';
CREATE TEMP TABLE i18_ai_object ON COMMIT DROP AS
  SELECT object_id FROM meta.ai_report_object WHERE schema_name = 'dm' AND object_name = 'v_retail_application_current';

DO $guard$
DECLARE
  v text;
BEGIN
  SET LOCAL search_path = pg_catalog;
  -- Everything that stays must not point at what is removed.
  IF EXISTS (SELECT 1 FROM etl.batch_run b WHERE b.batch_run_id NOT IN (SELECT batch_run_id FROM pg_temp.i18_old_batch)
              AND (b.source_batch_run_id IN (SELECT batch_run_id FROM pg_temp.i18_old_batch)
                   OR b.root_batch_run_id IN (SELECT batch_run_id FROM pg_temp.i18_old_batch)
                   OR b.resume_from_batch_run_id IN (SELECT batch_run_id FROM pg_temp.i18_old_batch)
                   OR b.preflight_batch_run_id IN (SELECT batch_run_id FROM pg_temp.i18_old_batch))) THEN
    RAISE EXCEPTION 'STOP: an object load run refers to a run of the old contour';
  END IF;
  IF EXISTS (SELECT 1 FROM etl.object_run WHERE batch_run_id IN (SELECT batch_run_id FROM pg_temp.i18_old_batch)
                                            OR job_run_id IN (SELECT job_run_id FROM pg_temp.i18_old_job_run))
     OR EXISTS (SELECT 1 FROM etl.object_ods_run WHERE batch_run_id IN (SELECT batch_run_id FROM pg_temp.i18_old_batch)
                                                OR job_run_id IN (SELECT job_run_id FROM pg_temp.i18_old_job_run)) THEN
    RAISE EXCEPTION 'STOP: an object run refers to a run of the old contour';
  END IF;
  IF EXISTS (SELECT 1 FROM etl.job_run r WHERE r.job_run_id NOT IN (SELECT job_run_id FROM pg_temp.i18_old_job_run)
              AND r.reused_from_job_run_id IN (SELECT job_run_id FROM pg_temp.i18_old_job_run)) THEN
    RAISE EXCEPTION 'STOP: a job run that stays reuses a job run of the old contour';
  END IF;
  SELECT string_agg(rule_code, ', ') INTO v FROM dq.rule WHERE rule_code !~ '^CRM_RETAIL_';
  IF v IS NOT NULL THEN RAISE EXCEPTION 'STOP: DQ rules outside the old CRM contour: %', v; END IF;
  IF EXISTS (SELECT 1 FROM meta.object_config_version WHERE source_object_id IN
              (SELECT o.source_object_id FROM meta.source_object o JOIN meta.source_system s
                   ON s.source_system_id = o.source_system_id WHERE s.code = 'CRM_RETAIL')) THEN
    RAISE EXCEPTION 'STOP: CRM_RETAIL objects already have object configurations';
  END IF;
  -- Nothing outside the removed objects may depend on them (views, rules).
  SELECT string_agg(DISTINCT dn.nspname || '.' || dc.relname, ', ') INTO v
    FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid AND d.classid = 'pg_rewrite'::regclass
    JOIN pg_class dc ON dc.oid = r.ev_class JOIN pg_namespace dn ON dn.oid = dc.relnamespace
    JOIN pg_class sc ON sc.oid = d.refobjid JOIN pg_namespace sn ON sn.oid = sc.relnamespace
   WHERE dc.oid <> sc.oid
     AND ((sn.nspname IN ('stg', 'ods') AND sc.relname ~ '^crm_retail_')
          OR (sn.nspname = 'dm' AND sc.relname IN ('retail_application_history', 'v_retail_application_source',
                                                   'v_retail_application_current')))
     AND NOT (dn.nspname = 'dm' AND dc.relname IN ('v_retail_application_source', 'v_retail_application_current'));
  IF v IS NOT NULL THEN RAISE EXCEPTION 'STOP: other objects depend on the old CRM tables: %', v; END IF;
END
$guard$;

-- 1. Database objects of the old contour.
DO $drop$
DECLARE
  r record;
  n integer := 0;
  n_dm integer;
BEGIN
  SET LOCAL search_path = pg_catalog;
  SELECT count(*) INTO n_dm FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
   WHERE s.nspname = 'dm' AND c.relname IN ('v_retail_application_current', 'v_retail_application_source',
                                            'retail_application_history');
  n_dm := n_dm + (SELECT count(*) FROM pg_proc p JOIN pg_namespace s ON s.oid = p.pronamespace
                   WHERE s.nspname = 'dm' AND p.proname = 'refresh_retail_application');
  DROP VIEW IF EXISTS dm.v_retail_application_current;
  DROP VIEW IF EXISTS dm.v_retail_application_source;
  DROP FUNCTION IF EXISTS dm.refresh_retail_application(bigint);
  DROP TABLE IF EXISTS dm.retail_application_history;
  DROP SEQUENCE IF EXISTS dm.retail_application_history_dm_application_version_id_seq;
  FOR r IN SELECT n2.nspname, c.relname, c.relkind FROM pg_class c JOIN pg_namespace n2 ON n2.oid = c.relnamespace
            WHERE n2.nspname IN ('stg', 'ods') AND c.relname ~ '^crm_retail_' AND c.relkind IN ('r', 'p')
            ORDER BY 1, 2
  LOOP
    EXECUTE format('DROP TABLE %I.%I', r.nspname, r.relname);
    n := n + 1;
  END LOOP;
  FOR r IN SELECT n2.nspname, c.relname FROM pg_class c JOIN pg_namespace n2 ON n2.oid = c.relnamespace
            WHERE n2.nspname = 'stg' AND c.relname ~ '^crm_retail_.*__dwh_landing_id_seq$' AND c.relkind = 'S'
  LOOP
    EXECUTE format('DROP SEQUENCE %I.%I', r.nspname, r.relname);
  END LOOP;
  RAISE NOTICE 'I18_020 dropped: dm % objects (2 views, function, table), stg/ods % tables', n_dm, n;
END
$drop$;

-- 2. AI assistant: the whitelist row of the removed view.
DO $ai$
DECLARE
  n_values integer; n_examples integer; n_terms integer; n_objects integer; n_saved integer := 0;
BEGIN
  SET LOCAL search_path = pg_catalog;
  DELETE FROM meta.ai_value_column WHERE object_id IN (SELECT object_id FROM pg_temp.i18_ai_object);
  GET DIAGNOSTICS n_values = ROW_COUNT;
  DELETE FROM meta.ai_example WHERE object_id IN (SELECT object_id FROM pg_temp.i18_ai_object);
  GET DIAGNOSTICS n_examples = ROW_COUNT;
  DELETE FROM meta.ai_glossary WHERE object_id IN (SELECT object_id FROM pg_temp.i18_ai_object);
  GET DIAGNOSTICS n_terms = ROW_COUNT;
  DELETE FROM meta.ai_report_object WHERE object_id IN (SELECT object_id FROM pg_temp.i18_ai_object);
  GET DIAGNOSTICS n_objects = ROW_COUNT;
  IF to_regclass('app.saved_report') IS NOT NULL THEN
    SELECT count(*) INTO n_saved FROM app.saved_report
     WHERE object_ids && ARRAY(SELECT object_id FROM pg_temp.i18_ai_object);
  END IF;
  RAISE NOTICE 'I18_020 AI: objects %, value columns %, examples %, terms %; saved reports left unavailable %',
    n_objects, n_values, n_examples, n_terms, n_saved;
END
$ai$;

-- 3. DQ and the old run queue (all their rows belong to the old contour, checked above).
DO $dq$
DECLARE
  n_issue integer; n_result integer; n_snap integer; n_request integer; n_run integer; n_rule integer;
BEGIN
  SET LOCAL search_path = pg_catalog;
  DELETE FROM dq.issue; GET DIAGNOSTICS n_issue = ROW_COUNT;
  DELETE FROM dq.result; GET DIAGNOSTICS n_result = ROW_COUNT;
  DELETE FROM dq.run_rule_snapshot; GET DIAGNOSTICS n_snap = ROW_COUNT;
  DELETE FROM etl.run_request; GET DIAGNOSTICS n_request = ROW_COUNT;
  DELETE FROM dq.run; GET DIAGNOSTICS n_run = ROW_COUNT;
  DELETE FROM dq.rule; GET DIAGNOSTICS n_rule = ROW_COUNT;
  RAISE NOTICE 'I18_020 DQ: rules %, runs %, results %, issues %, rule snapshots %; old run requests %',
    n_rule, n_run, n_result, n_issue, n_snap, n_request;
END
$dq$;

-- 4. Contracts and mapping rules of CRM_RETAIL (the legacy marker M7) and the batch snapshots of the old runs.
DO $meta$
DECLARE
  n_cs integer; n_ms integer; n_ref integer; n_map integer; n_con integer;
BEGIN
  SET LOCAL search_path = pg_catalog;
  DELETE FROM meta.batch_contract_snapshot WHERE contract_id IN (SELECT contract_id FROM pg_temp.i18_old_contract)
                                              OR batch_run_id IN (SELECT batch_run_id FROM pg_temp.i18_old_batch);
  GET DIAGNOSTICS n_cs = ROW_COUNT;
  DELETE FROM meta.batch_mapping_snapshot WHERE mapping_id IN (SELECT mapping_id FROM pg_temp.i18_old_mapping)
                                             OR batch_run_id IN (SELECT batch_run_id FROM pg_temp.i18_old_batch);
  GET DIAGNOSTICS n_ms = ROW_COUNT;
  DELETE FROM meta.mapping_source_ref WHERE mapping_id IN (SELECT mapping_id FROM pg_temp.i18_old_mapping);
  GET DIAGNOSTICS n_ref = ROW_COUNT;
  DELETE FROM meta.mapping_rule WHERE mapping_id IN (SELECT mapping_id FROM pg_temp.i18_old_mapping);
  GET DIAGNOSTICS n_map = ROW_COUNT;
  DELETE FROM meta.column_contract WHERE contract_id IN (SELECT contract_id FROM pg_temp.i18_old_contract);
  GET DIAGNOSTICS n_con = ROW_COUNT;
  RAISE NOTICE 'I18_020 meta: contracts %, mapping rules %, mapping source refs %, contract snapshots %, mapping snapshots %',
    n_con, n_map, n_ref, n_cs, n_ms;
END
$meta$;

-- 5. Jobs, runs, errors and watermarks of the old contour.
DO $etl$
DECLARE
  n_err integer; n_wm integer; n_jr integer; n_br integer; n_job integer;
BEGIN
  SET LOCAL search_path = pg_catalog;
  DELETE FROM etl.error_log WHERE job_id IN (SELECT job_id FROM pg_temp.i18_old_job)
                               OR batch_run_id IN (SELECT batch_run_id FROM pg_temp.i18_old_batch)
                               OR job_run_id IN (SELECT job_run_id FROM pg_temp.i18_old_job_run);
  GET DIAGNOSTICS n_err = ROW_COUNT;
  DELETE FROM etl.watermark WHERE job_id IN (SELECT job_id FROM pg_temp.i18_old_job)
                               OR last_success_run_id IN (SELECT job_run_id FROM pg_temp.i18_old_job_run);
  GET DIAGNOSTICS n_wm = ROW_COUNT;
  DELETE FROM etl.job_run WHERE job_run_id IN (SELECT job_run_id FROM pg_temp.i18_old_job_run);
  GET DIAGNOSTICS n_jr = ROW_COUNT;
  DELETE FROM etl.batch_run WHERE batch_run_id IN (SELECT batch_run_id FROM pg_temp.i18_old_batch);
  GET DIAGNOSTICS n_br = ROW_COUNT;
  DELETE FROM etl.job WHERE job_id IN (SELECT job_id FROM pg_temp.i18_old_job);
  GET DIAGNOSTICS n_job = ROW_COUNT;
  RAISE NOTICE 'I18_020 etl: jobs %, batch runs %, job runs %, errors %, watermarks %', n_job, n_br, n_jr, n_err, n_wm;
END
$etl$;

-- 6. Result checks inside the transaction.
DO $post$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF meta.source_is_legacy((SELECT source_system_id FROM meta.source_system WHERE code = 'CRM_RETAIL')) THEN
    RAISE EXCEPTION 'STOP: CRM_RETAIL is still a legacy source';
  END IF;
  IF EXISTS (SELECT 1 FROM etl.batch_run WHERE pipeline_code IS DISTINCT FROM 'OBJECT_LOAD') THEN
    RAISE EXCEPTION 'STOP: runs of the old pipelines are left';
  END IF;
END
$post$;

CREATE FUNCTION meta.legacy_crm_removed() RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $fn$ SELECT 'I18 package 020: old CRM contour removed'::text $fn$;
COMMENT ON FUNCTION meta.legacy_crm_removed() IS 'I18 package 020 marker: the old CRM contour is removed';
REVOKE ALL ON FUNCTION meta.legacy_crm_removed() FROM PUBLIC;

SELECT 'MIGRATION_020_DONE' AS result;
