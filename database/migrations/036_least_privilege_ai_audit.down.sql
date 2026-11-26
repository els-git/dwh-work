-- Rollback of package 036: gives back exactly the privileges kept in audit.revoked_grant_036 (each row is first
-- checked against the lists of 036, I14-07 N4: an unknown row -> STOP), drops the audit
-- triggers of the assistant settings. Events already written to audit.security_event stay (the journal is append-only).
-- Run as dwh_admin:  psql -1 -v ON_ERROR_STOP=1 -f 036_least_privilege_ai_audit.down.sql
DO $pre$
BEGIN
  IF to_regclass('audit.revoked_grant_036') IS NULL THEN RAISE EXCEPTION 'STOP: 036 is not applied'; END IF;
END
$pre$;

-- Rows of audit.revoked_grant_036 that package 036 could not have written (I14-07, N4). The rollback of 036 GRANTs
-- by these rows, so a row added by someone else would hand out a privilege. Same list as 036 P1 and P2.
CREATE FUNCTION pg_temp.revoked_036_invalid() RETURNS SETOF text
LANGUAGE sql STABLE SET search_path TO 'pg_catalog' AS $v$
  SELECT format('%s %s %s %s', g.object_kind, g.object_name, g.grantee, g.privilege_type)
    FROM audit.revoked_grant_036 g
   WHERE NOT (
     (g.object_kind = 'TABLE' AND g.grantee = 'dwh_etl'
      AND to_regclass(g.object_name) IS NOT NULL
      AND ((to_regclass(g.object_name) IN (SELECT to_regclass(t) FROM unnest(ARRAY['dq.issue', 'dq.result', 'dq.rule',
              'dq.run', 'dq.run_rule_snapshot', 'etl.batch_run', 'etl.error_log', 'etl.job', 'etl.job_run',
              'etl.run_request', 'etl.watermark', 'meta.batch_contract_snapshot', 'meta.batch_mapping_snapshot',
              'meta.column_contract', 'meta.mapping_rule', 'meta.mapping_source_ref', 'meta.metadata_capture',
              'meta.source_column_snapshot', 'meta.source_object', 'meta.source_object_snapshot',
              'meta.source_system']) t)
            AND g.privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'))
        OR (to_regclass(g.object_name) IN (SELECT to_regclass(t)
              FROM unnest(ARRAY['etl.object_record_change', 'etl.object_ods_record_change']) t)
            AND g.privilege_type IN ('UPDATE', 'DELETE', 'TRUNCATE'))))
     OR (g.object_kind = 'FUNCTION' AND g.privilege_type = 'EXECUTE' AND g.grantee IN ('PUBLIC', 'dwh_app', 'dwh_etl')
         AND to_regprocedure(g.object_name) IS NOT NULL
         AND (SELECT n.nspname || '.' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               WHERE p.oid = to_regprocedure(g.object_name)) IN (
           'app.integration_contract_create', 'app.integration_contract_set_active', 'app.integration_contract_version',
           'app.integration_dq_rule_create', 'app.integration_dq_rule_set_active', 'app.integration_dq_rule_version',
           'app.integration_mapping_create', 'app.integration_mapping_set_active', 'app.integration_mapping_version',
           'app.integration_run_request_create', 'app.integration_run_request_cancel',
           'etl.integration_run_request_claim', 'etl.integration_run_request_fail',
           'etl.integration_run_request_start', 'etl.integration_run_request_success')))
   ORDER BY 1
$v$;

DO $check$
DECLARE v_bad text[] := ARRAY(SELECT pg_temp.revoked_036_invalid());
BEGIN
  IF cardinality(v_bad) > 0 THEN
    RAISE EXCEPTION 'STOP: audit.revoked_grant_036 has % row(s) package 036 did not write: %', cardinality(v_bad),
      array_to_string(v_bad[1:5], '; ');
  END IF;
END
$check$;

DO $back$
DECLARE g record; n int := 0;
BEGIN
  FOR g IN SELECT * FROM audit.revoked_grant_036 ORDER BY 1, 2, 3, 4 LOOP
    IF g.object_kind = 'TABLE' THEN
      EXECUTE format('GRANT %s ON %s TO %s', g.privilege_type, g.object_name,
                     CASE WHEN g.grantee = 'PUBLIC' THEN 'PUBLIC' ELSE quote_ident(g.grantee) END);
    ELSE
      EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %s', g.object_name,
                     CASE WHEN g.grantee = 'PUBLIC' THEN 'PUBLIC' ELSE quote_ident(g.grantee) END);
    END IF;
    n := n + 1;
  END LOOP;
  RAISE NOTICE 'PRIVILEGES_RESTORED %', n;
END
$back$;

DROP TRIGGER IF EXISTS trg_audit_ai_report_object ON meta.ai_report_object;
DROP TRIGGER IF EXISTS trg_audit_ai_value_column ON meta.ai_value_column;
DROP TRIGGER IF EXISTS trg_audit_ai_glossary ON meta.ai_glossary;
DROP TRIGGER IF EXISTS trg_audit_ai_example ON meta.ai_example;
DROP FUNCTION audit.audit_ai_settings();
DROP TABLE audit.revoked_grant_036;

SELECT 'ROLLBACK_036_DONE' AS result;
