-- I13-03 (package 036): least privilege of the working roles and audit of the assistant settings.
-- Plan: docs/DWH_I13_RELIABILITY_PLAN_APPROVED_20261126.md, section 7.1 (P1-P3; P4 not needed - inventory 2b:
-- users, roles, modules and rights are audited by triggers audit.audit_*; P5 not done - decision R6 = A).
--   P1  dwh_etl loses INSERT/UPDATE/DELETE/TRUNCATE on 21 tables its code does not write (old CRM ETL rights);
--       on etl.object_record_change / etl.object_ods_record_change it keeps INSERT only.
--   P2  EXECUTE of the 15 functions of the removed old CRM contour is taken from dwh_app, dwh_etl and PUBLIC
--       (the functions stay; the rollback gives the rights back).
--   P3  trigger audit.audit_ai_settings on meta.ai_report_object / ai_value_column / ai_glossary / ai_example writes
--       AI_SETTINGS_CHANGED for every new or changed row (who, which row, what it opens; no texts, no SQL).
-- Every privilege taken is kept in audit.revoked_grant_036 (owner only) so that the rollback restores it exactly.
-- Run as the owner of the schemas (dwh_admin):  psql -1 -v ON_ERROR_STOP=1 -f 036_least_privilege_ai_audit.sql
DO $pre$
DECLARE t text;
BEGIN
  IF to_regprocedure('etl.object_file_warning(bigint, text, text, text, text)') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 035 is not applied';
  END IF;
  IF to_regprocedure('audit.log_security_event(text,text,text,text,text,jsonb)') IS NULL THEN
    RAISE EXCEPTION 'STOP: audit.log_security_event not found';
  END IF;
  IF to_regprocedure('audit.audit_ai_settings()') IS NOT NULL OR to_regclass('audit.revoked_grant_036') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 036 is already applied';
  END IF;
  FOREACH t IN ARRAY ARRAY['meta.ai_report_object', 'meta.ai_value_column', 'meta.ai_glossary', 'meta.ai_example'] LOOP
    IF to_regclass(t) IS NULL THEN RAISE EXCEPTION 'STOP: % not found', t; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass(t) AND attname = 'updated_by_user_id'
                     AND NOT attisdropped) THEN
      RAISE EXCEPTION 'STOP: %.updated_by_user_id not found', t;
    END IF;
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dwh_etl') OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dwh_app') THEN
    RAISE EXCEPTION 'STOP: role dwh_etl or dwh_app not found';
  END IF;
END
$pre$;

CREATE TABLE audit.revoked_grant_036 (
  object_kind text NOT NULL CHECK (object_kind IN ('TABLE', 'FUNCTION')),
  object_name text NOT NULL,          -- regclass / regprocedure text
  grantee text NOT NULL,              -- role name or PUBLIC
  privilege_type text NOT NULL,
  PRIMARY KEY (object_kind, object_name, grantee, privilege_type)
);
REVOKE ALL ON audit.revoked_grant_036 FROM PUBLIC;

-- P1: tables. The list comes from the inventory of 26.11.2026; a table that does not exist is skipped.
DO $p1$
DECLARE
  t text; r record; n int := 0;
  v_tables text[] := ARRAY['dq.issue', 'dq.result', 'dq.rule', 'dq.run', 'dq.run_rule_snapshot',
    'etl.batch_run', 'etl.error_log', 'etl.job', 'etl.job_run', 'etl.run_request', 'etl.watermark',
    'meta.batch_contract_snapshot', 'meta.batch_mapping_snapshot', 'meta.column_contract', 'meta.mapping_rule',
    'meta.mapping_source_ref', 'meta.metadata_capture', 'meta.source_column_snapshot', 'meta.source_object',
    'meta.source_object_snapshot', 'meta.source_system'];
BEGIN
  FOREACH t IN ARRAY v_tables LOOP
    CONTINUE WHEN to_regclass(t) IS NULL;
    FOR r IN SELECT a.privilege_type FROM pg_class c, aclexplode(c.relacl) a
              WHERE c.oid = to_regclass(t) AND a.grantee = 'dwh_etl'::regrole
                AND a.privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE') LOOP
      INSERT INTO audit.revoked_grant_036 VALUES ('TABLE', to_regclass(t)::text, 'dwh_etl', r.privilege_type);
      n := n + 1;
    END LOOP;
    EXECUTE format('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON %s FROM dwh_etl', to_regclass(t));
  END LOOP;
  FOREACH t IN ARRAY ARRAY['etl.object_record_change', 'etl.object_ods_record_change'] LOOP
    CONTINUE WHEN to_regclass(t) IS NULL;
    FOR r IN SELECT a.privilege_type FROM pg_class c, aclexplode(c.relacl) a
              WHERE c.oid = to_regclass(t) AND a.grantee = 'dwh_etl'::regrole
                AND a.privilege_type IN ('UPDATE', 'DELETE', 'TRUNCATE') LOOP
      INSERT INTO audit.revoked_grant_036 VALUES ('TABLE', to_regclass(t)::text, 'dwh_etl', r.privilege_type);
      n := n + 1;
    END LOOP;
    EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON %s FROM dwh_etl', to_regclass(t));
  END LOOP;
  RAISE NOTICE 'TABLE_PRIVILEGES_REVOKED %', n;
END
$p1$;

-- P2: functions of the removed old CRM contour (every overload of each name).
DO $p2$
DECLARE
  f record; a record; n int := 0;
BEGIN
  FOR f IN SELECT p.oid, p.oid::regprocedure::text AS sig, p.proowner, p.proacl
             FROM pg_proc p JOIN pg_namespace s ON s.oid = p.pronamespace
            WHERE (s.nspname = 'app' AND p.proname IN ('integration_contract_create', 'integration_contract_set_active',
                     'integration_contract_version', 'integration_dq_rule_create', 'integration_dq_rule_set_active',
                     'integration_dq_rule_version', 'integration_mapping_create', 'integration_mapping_set_active',
                     'integration_mapping_version', 'integration_run_request_create', 'integration_run_request_cancel'))
               OR (s.nspname = 'etl' AND p.proname IN ('integration_run_request_claim', 'integration_run_request_fail',
                     'integration_run_request_start', 'integration_run_request_success')) LOOP
    FOR a IN SELECT CASE WHEN x.grantee = 0 THEN 'PUBLIC' ELSE x.grantee::regrole::text END AS grantee
               FROM aclexplode(coalesce(f.proacl, acldefault('f', f.proowner))) x
              WHERE x.privilege_type = 'EXECUTE'
                AND (x.grantee = 0 OR x.grantee IN ('dwh_app'::regrole, 'dwh_etl'::regrole)) LOOP
      INSERT INTO audit.revoked_grant_036 VALUES ('FUNCTION', f.sig, a.grantee, 'EXECUTE');
      n := n + 1;
    END LOOP;
    EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC, dwh_app, dwh_etl', f.sig);
  END LOOP;
  RAISE NOTICE 'FUNCTION_PRIVILEGES_REVOKED %', n;
END
$p2$;

-- P3: audit of the assistant settings.
CREATE FUNCTION audit.audit_ai_settings() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog' AS $fn$
DECLARE
  v_row jsonb := to_jsonb(NEW);
  v_id text;
  v_detail jsonb;
BEGIN
  v_id := CASE TG_TABLE_NAME
            WHEN 'ai_report_object' THEN v_row->>'object_id'
            WHEN 'ai_value_column' THEN v_row->>'value_column_id'
            WHEN 'ai_glossary' THEN v_row->>'term_id'
            ELSE v_row->>'example_id' END;
  -- what the row opens to the assistant; texts of terms, examples and SQL are never copied
  v_detail := jsonb_strip_nulls(jsonb_build_object(
    'table', TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
    'row_id', v_id,
    'actor_user_id', v_row->'updated_by_user_id',
    'object', CASE WHEN TG_TABLE_NAME = 'ai_report_object' THEN (v_row->>'schema_name') || '.' || (v_row->>'object_name') END,
    'object_id', CASE WHEN TG_TABLE_NAME <> 'ai_report_object' THEN v_row->'object_id' END,
    'required_permission_code', v_row->'required_permission_code',
    'column_name', v_row->'column_name',
    'is_active', v_row->'is_active',
    'changed_fields', CASE WHEN TG_OP = 'UPDATE' THEN (
        SELECT jsonb_agg(k ORDER BY k) FROM jsonb_object_keys(v_row) k
         WHERE k NOT IN ('updated_at', 'updated_by_user_id') AND v_row->k IS DISTINCT FROM to_jsonb(OLD)->k) END));
  PERFORM audit.log_security_event('AI_SETTINGS_CHANGED', 'AI_SETTING', TG_TABLE_NAME || ':' || v_id,
    CASE TG_OP WHEN 'INSERT' THEN 'CREATE' ELSE 'UPDATE' END, 'SUCCESS', v_detail);
  RETURN NULL;
END
$fn$;
REVOKE ALL ON FUNCTION audit.audit_ai_settings() FROM PUBLIC;

CREATE TRIGGER trg_audit_ai_report_object AFTER INSERT OR UPDATE ON meta.ai_report_object
  FOR EACH ROW EXECUTE FUNCTION audit.audit_ai_settings();
CREATE TRIGGER trg_audit_ai_value_column AFTER INSERT OR UPDATE ON meta.ai_value_column
  FOR EACH ROW EXECUTE FUNCTION audit.audit_ai_settings();
CREATE TRIGGER trg_audit_ai_glossary AFTER INSERT OR UPDATE ON meta.ai_glossary
  FOR EACH ROW EXECUTE FUNCTION audit.audit_ai_settings();
CREATE TRIGGER trg_audit_ai_example AFTER INSERT OR UPDATE ON meta.ai_example
  FOR EACH ROW EXECUTE FUNCTION audit.audit_ai_settings();

SELECT 'MIGRATION_036_DONE' AS result;
