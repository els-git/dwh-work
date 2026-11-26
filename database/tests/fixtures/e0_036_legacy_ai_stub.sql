-- E0 fixture for package 036 (sandbox only, after e0_035): objects that exist in dwh but not in the E0 chain.
--   * the 4 assistant settings tables of package 016 (same columns);
--   * the old CRM tables that dwh_etl may write in dwh (dq.*, etl.run_request, etl.watermark, meta.* snapshots),
--     with the same write rights of dwh_etl as the inventory of 26.11.2026 shows;
--   * stubs of the 15 functions of the old CRM contour, EXECUTE for dwh_app / dwh_etl as in dwh.
-- Run as postgres. Not a part of any package.
CREATE TABLE meta.ai_report_object (
  object_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  schema_name text NOT NULL CHECK (schema_name = 'dm'),
  object_name text NOT NULL CHECK (object_name ~ '^[a-z_][a-z0-9_]{0,62}$'),
  title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 200),
  description text CHECK (char_length(description) <= 4000),
  required_permission_code text NOT NULL CHECK (required_permission_code ~ '^[A-Z][A-Z0-9_]*$'),
  is_active boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint,
  CONSTRAINT ai_report_object_name UNIQUE (schema_name, object_name)
);

-- U2: reference columns whose distinct values the model may see.
CREATE TABLE meta.ai_value_column (
  value_column_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  object_id bigint NOT NULL REFERENCES meta.ai_report_object(object_id) ON DELETE RESTRICT,
  column_name text NOT NULL CHECK (column_name ~ '^[a-z_][a-z0-9_]{0,62}$'),
  max_values integer NOT NULL DEFAULT 50 CHECK (max_values BETWEEN 1 AND 500),
  is_active boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint,
  CONSTRAINT ai_value_column_name UNIQUE (object_id, column_name)
);

-- Terms of the business: explained to the model (general, or bound to one object).
CREATE TABLE meta.ai_glossary (
  term_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  term text NOT NULL CHECK (char_length(btrim(term)) BETWEEN 1 AND 200),
  meaning text NOT NULL CHECK (char_length(btrim(meaning)) BETWEEN 1 AND 4000),
  object_id bigint REFERENCES meta.ai_report_object(object_id) ON DELETE RESTRICT,
  is_active boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint
);

-- Example questions with checked SQL (few-shot for the model), bound to one object.
CREATE TABLE meta.ai_example (
  example_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  object_id bigint NOT NULL REFERENCES meta.ai_report_object(object_id) ON DELETE RESTRICT,
  question text NOT NULL CHECK (char_length(btrim(question)) BETWEEN 1 AND 1000),
  sql_text text NOT NULL CHECK (char_length(btrim(sql_text)) BETWEEN 1 AND 20000),
  is_active boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint
);

-- ------------------------------------------------------------------ request log (app)
ALTER TABLE meta.ai_report_object OWNER TO dwh_admin;
ALTER TABLE meta.ai_value_column OWNER TO dwh_admin;
ALTER TABLE meta.ai_glossary OWNER TO dwh_admin;
ALTER TABLE meta.ai_example OWNER TO dwh_admin;

CREATE SCHEMA IF NOT EXISTS dq AUTHORIZATION dwh_admin;
GRANT USAGE ON SCHEMA dq TO dwh_etl;
DO $t$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['dq.issue', 'dq.result', 'dq.rule', 'dq.run', 'dq.run_rule_snapshot', 'etl.run_request',
                           'etl.watermark', 'meta.batch_contract_snapshot', 'meta.batch_mapping_snapshot',
                           'meta.mapping_source_ref'] LOOP
    IF to_regclass(t) IS NULL THEN
      EXECUTE format('CREATE TABLE %s (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, note text)', t);
      EXECUTE format('ALTER TABLE %s OWNER TO dwh_admin', t);
    END IF;
  END LOOP;
  FOREACH t IN ARRAY ARRAY['dq.issue', 'dq.result', 'dq.rule', 'dq.run', 'dq.run_rule_snapshot', 'etl.batch_run',
    'etl.error_log', 'etl.job', 'etl.job_run', 'etl.run_request', 'etl.watermark', 'meta.batch_contract_snapshot',
    'meta.batch_mapping_snapshot', 'meta.column_contract', 'meta.mapping_rule', 'meta.mapping_source_ref',
    'meta.metadata_capture', 'meta.source_column_snapshot', 'meta.source_object', 'meta.source_object_snapshot',
    'meta.source_system'] LOOP
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO dwh_etl', t);
  END LOOP;
  EXECUTE 'GRANT UPDATE, DELETE ON etl.object_record_change, etl.object_ods_record_change TO dwh_etl';
END
$t$;

DO $f$
DECLARE n text;
BEGIN
  FOREACH n IN ARRAY ARRAY['integration_contract_create', 'integration_contract_set_active', 'integration_contract_version',
    'integration_dq_rule_create', 'integration_dq_rule_set_active', 'integration_dq_rule_version',
    'integration_mapping_create', 'integration_mapping_set_active', 'integration_mapping_version',
    'integration_run_request_create', 'integration_run_request_cancel'] LOOP
    IF to_regprocedure(format('app.%s(text)', n)) IS NULL THEN
      EXECUTE format('CREATE FUNCTION app.%I(p_token_hash text) RETURNS integer LANGUAGE sql SECURITY DEFINER '
                     'SET search_path = pg_catalog AS $b$ SELECT 1 $b$', n);
      EXECUTE format('ALTER FUNCTION app.%I(text) OWNER TO dwh_admin', n);
      EXECUTE format('REVOKE ALL ON FUNCTION app.%I(text) FROM PUBLIC', n);
      EXECUTE format('GRANT EXECUTE ON FUNCTION app.%I(text) TO dwh_app', n);
    END IF;
  END LOOP;
  FOREACH n IN ARRAY ARRAY['integration_run_request_claim', 'integration_run_request_fail',
                           'integration_run_request_start', 'integration_run_request_success'] LOOP
    IF to_regprocedure(format('etl.%s(text)', n)) IS NULL THEN
      EXECUTE format('CREATE FUNCTION etl.%I(p_worker text) RETURNS integer LANGUAGE sql SECURITY DEFINER '
                     'SET search_path = pg_catalog AS $b$ SELECT 1 $b$', n);
      EXECUTE format('ALTER FUNCTION etl.%I(text) OWNER TO dwh_admin', n);
      EXECUTE format('GRANT EXECUTE ON FUNCTION etl.%I(text) TO dwh_etl', n);   -- PUBLIC keeps its default EXECUTE
    END IF;
  END LOOP;
END
$f$;
