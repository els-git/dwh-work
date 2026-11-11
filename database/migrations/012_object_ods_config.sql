-- I06-05 (step 4): ODS configuration of a source object and the ODS step of a run
-- (plan docs/DWH_I06_05_STG_ODS_PLAN_APPROVED_20261106.md, decisions O1-O5).
--
-- O1  A separate version line "ODS" per object (same life cycle as 008: DRAFT -> validation ->
--     PUBLISHED, immutable, definition hash). Editing ODS never creates a new LOAD version, so the
--     INCREMENTAL watermark of the STG load (per LOAD version, 011) is kept. The source of the ODS
--     step is the STG table of the LOAD version in force (meta.object_config_current, schema stg).
-- O2  Each ODS field takes one transformation of a fixed white list; parameters are values only
--     (no SQL text): COPY, CAST, TRIM, UPPER, LOWER, EMPTY_TO_NULL, DEFAULT (constant instead of
--     NULL), CONSTANT, DATE_FROM_TEXT (format from a fixed list), LOAD_RUN_ID, LOADED_AT. The SQL is
--     built by the ETL engine with quoted names and literals. Technical fields are INSERT_ONLY.
-- O3  The ODS step is a run of its own: etl.object_ods_run with its own batch_run (pipeline
--     OBJECT_LOAD, batch code OBJECT_ODS_<id>) and job_run (job OBJECT_ODS_<id>, type TRANSFORM),
--     linked to the STG run it followed (NULL when started alone). The STG result already committed
--     is never changed by the ODS step.
-- O4  History "before/after" of the ODS step: etl.object_ods_record_change (append-only).
-- O5  A new ODS table is a change proposal: meta.target_change_proposal gets ods_version_id
--     (a proposal belongs to exactly one LOAD or ODS version); nothing here executes DDL.
--
-- Existing tables keep their rows; existing functions of 008-011 are not replaced.
-- app.* wrappers check the DWH session and write audit.log_security_event (as in 008).
-- Privileges: tables only for the owner (default grants of meta/app/etl removed and reported),
-- INSERT on etl.object_ods_record_change for dwh_etl; EXECUTE on app wrappers for dwh_app,
-- on the two etl functions for dwh_etl.
-- Requires 001-011. Run as the owner of schemas app, meta, etl:
--   psql -1 -v ON_ERROR_STOP=1 -f 012_object_ods_config.sql

CREATE TABLE meta.object_ods_version (
  ods_version_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_object_id bigint NOT NULL REFERENCES meta.source_object(source_object_id) ON DELETE RESTRICT,
  version_no integer NOT NULL CHECK (version_no >= 1),
  status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'PUBLISHED')),
  based_on_ods_version_id bigint REFERENCES meta.object_ods_version(ods_version_id) ON DELETE RESTRICT,
  target_table text CHECK (target_table ~ '^[a-z_][a-z0-9_]{0,62}$'),
  draft_revision integer NOT NULL DEFAULT 1 CHECK (draft_revision >= 1),
  definition_hash text CHECK (definition_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  created_by bigint NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by bigint NOT NULL,
  published_at timestamptz,
  published_by bigint,
  UNIQUE (source_object_id, version_no),
  CONSTRAINT object_ods_published_complete CHECK (
    status = 'DRAFT'
    OR (published_at IS NOT NULL AND published_by IS NOT NULL AND definition_hash IS NOT NULL
        AND target_table IS NOT NULL))
);
CREATE UNIQUE INDEX object_ods_one_draft ON meta.object_ods_version(source_object_id) WHERE status = 'DRAFT';

CREATE TABLE meta.object_ods_field (
  ods_version_id bigint NOT NULL REFERENCES meta.object_ods_version(ods_version_id) ON DELETE RESTRICT,
  target_column text NOT NULL CHECK (target_column ~ '^[a-z_][a-z0-9_]{0,62}$'),
  field_position integer NOT NULL CHECK (field_position >= 1),
  transform text NOT NULL CHECK (transform IN ('COPY', 'CAST', 'TRIM', 'UPPER', 'LOWER', 'EMPTY_TO_NULL', 'DEFAULT',
                                                'CONSTANT', 'DATE_FROM_TEXT', 'LOAD_RUN_ID', 'LOADED_AT')),
  stg_column text CHECK (stg_column ~ '^[a-z_][a-z0-9_]{0,62}$'),
  stg_type text CHECK (stg_type ~ '^[A-Z]+(\([0-9]{1,4}(,[0-9]{1,4})?\))?$'),
  target_type text NOT NULL CHECK (target_type ~ '^[A-Z]+(\([0-9]{1,4}(,[0-9]{1,4})?\))?$'),
  conversion text NOT NULL DEFAULT 'NONE' CHECK (conversion IN ('NONE', 'SAFE', 'CONFIRMED')),
  param text CHECK (char_length(param) BETWEEN 1 AND 200),
  write_policy text NOT NULL DEFAULT 'AUTHORITATIVE' CHECK (write_policy IN ('AUTHORITATIVE', 'INSERT_ONLY')),
  allow_explicit_null boolean NOT NULL DEFAULT false,
  PRIMARY KEY (ods_version_id, target_column),
  UNIQUE (ods_version_id, field_position),
  CONSTRAINT object_ods_field_stg_column CHECK (
    (transform IN ('CONSTANT', 'LOAD_RUN_ID', 'LOADED_AT')) = (stg_column IS NULL)
    AND (stg_column IS NULL) = (stg_type IS NULL)),
  CONSTRAINT object_ods_field_param CHECK (
    (transform IN ('DEFAULT', 'CONSTANT', 'DATE_FROM_TEXT')) = (param IS NOT NULL)),
  CONSTRAINT object_ods_field_date_format CHECK (
    transform <> 'DATE_FROM_TEXT' OR param IN ('YYYY-MM-DD', 'DD.MM.YYYY', 'YYYYMMDD')),
  CONSTRAINT object_ods_field_technical CHECK (
    transform NOT IN ('LOAD_RUN_ID', 'LOADED_AT') OR write_policy = 'INSERT_ONLY')
);

CREATE TABLE meta.object_ods_key_part (
  ods_version_id bigint NOT NULL,
  key_position integer NOT NULL CHECK (key_position >= 1),
  target_column text NOT NULL,
  PRIMARY KEY (ods_version_id, key_position),
  UNIQUE (ods_version_id, target_column),
  FOREIGN KEY (ods_version_id, target_column) REFERENCES meta.object_ods_field(ods_version_id, target_column)
    ON DELETE RESTRICT
);

CREATE TABLE meta.object_ods_validation (
  validation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ods_version_id bigint NOT NULL REFERENCES meta.object_ods_version(ods_version_id) ON DELETE RESTRICT,
  draft_revision integer NOT NULL,
  definition_hash text NOT NULL CHECK (definition_hash ~ '^[0-9a-f]{64}$'),
  ok boolean NOT NULL,
  issues jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(issues) = 'array'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  created_by bigint NOT NULL
);

CREATE TABLE meta.object_ods_current (
  source_object_id bigint PRIMARY KEY REFERENCES meta.source_object(source_object_id) ON DELETE RESTRICT,
  ods_version_id bigint NOT NULL UNIQUE REFERENCES meta.object_ods_version(ods_version_id) ON DELETE RESTRICT,
  status text NOT NULL CHECK (status IN ('READY', 'DRIFT')),
  status_details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(status_details) = 'object'),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by bigint NOT NULL
);

-- O5: a change proposal belongs to exactly one LOAD (version_id) or ODS (ods_version_id) version.
ALTER TABLE meta.target_change_proposal
  ALTER COLUMN version_id DROP NOT NULL,
  ADD COLUMN ods_version_id bigint REFERENCES meta.object_ods_version(ods_version_id) ON DELETE RESTRICT,
  ADD CONSTRAINT target_change_proposal_one_version CHECK (num_nonnulls(version_id, ods_version_id) = 1),
  ADD CONSTRAINT target_change_proposal_ods_schema CHECK (ods_version_id IS NULL OR target_schema = 'ods');

-- A published ODS version, its fields and key never change; drafts may.
CREATE FUNCTION meta.object_ods_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, meta AS $fn$
DECLARE
  v_status text;
BEGIN
  IF TG_TABLE_NAME = 'object_ods_version' THEN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'ODS configuration versions cannot be deleted'; END IF;
    IF OLD.status = 'PUBLISHED' THEN RAISE EXCEPTION 'Published ODS configuration is immutable'; END IF;
    IF NEW.source_object_id IS DISTINCT FROM OLD.source_object_id OR NEW.version_no IS DISTINCT FROM OLD.version_no
       OR NEW.created_at IS DISTINCT FROM OLD.created_at OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
      RAISE EXCEPTION 'ODS configuration version identity immutable';
    END IF;
    RETURN NEW;
  END IF;
  SELECT status INTO v_status FROM meta.object_ods_version
   WHERE ods_version_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.ods_version_id ELSE NEW.ods_version_id END;
  IF v_status IS DISTINCT FROM 'DRAFT' THEN RAISE EXCEPTION 'Published ODS configuration is immutable'; END IF;
  IF TG_OP = 'UPDATE' AND OLD.ods_version_id IS DISTINCT FROM NEW.ods_version_id THEN
    RAISE EXCEPTION 'ODS configuration rows cannot move between versions';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$fn$;
CREATE TRIGGER object_ods_version_guard BEFORE UPDATE OR DELETE ON meta.object_ods_version
FOR EACH ROW EXECUTE FUNCTION meta.object_ods_guard();
CREATE TRIGGER object_ods_field_guard BEFORE INSERT OR UPDATE OR DELETE ON meta.object_ods_field
FOR EACH ROW EXECUTE FUNCTION meta.object_ods_guard();
CREATE TRIGGER object_ods_key_part_guard BEFORE INSERT OR UPDATE OR DELETE ON meta.object_ods_key_part
FOR EACH ROW EXECUTE FUNCTION meta.object_ods_guard();
CREATE TRIGGER object_ods_validation_append_only BEFORE UPDATE OR DELETE ON meta.object_ods_validation
FOR EACH ROW EXECUTE FUNCTION meta.object_config_reject_change();
CREATE TRIGGER object_ods_version_truncate BEFORE TRUNCATE ON meta.object_ods_version
FOR EACH STATEMENT EXECUTE FUNCTION meta.object_config_reject_change();
CREATE TRIGGER object_ods_field_truncate BEFORE TRUNCATE ON meta.object_ods_field
FOR EACH STATEMENT EXECUTE FUNCTION meta.object_config_reject_change();
CREATE TRIGGER object_ods_key_part_truncate BEFORE TRUNCATE ON meta.object_ods_key_part
FOR EACH STATEMENT EXECUTE FUNCTION meta.object_config_reject_change();
CREATE TRIGGER object_ods_validation_truncate BEFORE TRUNCATE ON meta.object_ods_validation
FOR EACH STATEMENT EXECUTE FUNCTION meta.object_config_reject_change();
CREATE TRIGGER object_ods_current_truncate BEFORE TRUNCATE ON meta.object_ods_current
FOR EACH STATEMENT EXECUTE FUNCTION meta.object_config_reject_change();

-- Canonical definition of an ODS version (target table, fields in order, key in order) and its hash.
CREATE FUNCTION meta.object_ods_definition(p_ods_version_id bigint) RETURNS jsonb
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  SELECT jsonb_build_object(
    'target_schema', 'ods', 'target_table', v.target_table,
    'fields', coalesce((SELECT jsonb_agg(jsonb_build_object(
                'target_column', f.target_column, 'transform', f.transform, 'stg_column', f.stg_column,
                'stg_type', f.stg_type, 'target_type', f.target_type, 'conversion', f.conversion, 'param', f.param,
                'write_policy', f.write_policy, 'allow_explicit_null', f.allow_explicit_null)
              ORDER BY f.field_position) FROM meta.object_ods_field f WHERE f.ods_version_id = v.ods_version_id),
              '[]'::jsonb),
    'key', coalesce((SELECT jsonb_agg(k.target_column ORDER BY k.key_position)
              FROM meta.object_ods_key_part k WHERE k.ods_version_id = v.ods_version_id), '[]'::jsonb))
    FROM meta.object_ods_version v WHERE v.ods_version_id = p_ods_version_id;
$fn$;

CREATE FUNCTION meta.object_ods_hash(p_ods_version_id bigint) RETURNS text
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  SELECT encode(sha256(convert_to(meta.object_ods_definition(p_ods_version_id)::text, 'UTF8')), 'hex');
$fn$;

-- STG table of the object: target of the LOAD version in force, schema stg (NULL otherwise).
CREATE FUNCTION meta.object_stg_table(p_source_object_id bigint) RETURNS jsonb
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  SELECT jsonb_build_object('schema', v.target_schema, 'table', v.target_table, 'version_id', v.version_id,
                            'version_no', v.version_no, 'status', c.status)
    FROM meta.object_config_current c JOIN meta.object_config_version v ON v.version_id = c.version_id
   WHERE c.source_object_id = p_source_object_id AND v.target_schema = 'stg';
$fn$;

-- ODS configuration of an object as the API sees it (proposals: app.integration_object_config_get).
CREATE FUNCTION app.integration_ods_config_get(p_token_hash text, p_source_object_id bigint)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
DECLARE
  o meta.source_object;
  c meta.object_ods_current;
  p meta.object_ods_version;
  d meta.object_ods_version;
  lv meta.object_ods_validation;
BEGIN
  PERFORM app.integration_admin_session_user(p_token_hash);
  SELECT * INTO o FROM meta.source_object x WHERE x.source_object_id = p_source_object_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source object not found'; END IF;
  SELECT * INTO c FROM meta.object_ods_current x WHERE x.source_object_id = p_source_object_id;
  IF FOUND THEN SELECT * INTO p FROM meta.object_ods_version x WHERE x.ods_version_id = c.ods_version_id; END IF;
  SELECT * INTO d FROM meta.object_ods_version x WHERE x.source_object_id = p_source_object_id AND x.status = 'DRAFT';
  IF d.ods_version_id IS NOT NULL THEN
    SELECT * INTO lv FROM meta.object_ods_validation x WHERE x.ods_version_id = d.ods_version_id
     ORDER BY x.validation_id DESC LIMIT 1;
  END IF;
  RETURN jsonb_build_object(
    'legacy', meta.source_is_legacy(o.source_system_id),
    'stg', meta.object_stg_table(p_source_object_id),
    'current', CASE WHEN c.ods_version_id IS NULL THEN NULL ELSE jsonb_build_object(
       'ods_version_id', p.ods_version_id, 'version_no', p.version_no, 'status', c.status,
       'status_details', c.status_details, 'published_at', p.published_at, 'definition_hash', p.definition_hash,
       'definition', meta.object_ods_definition(p.ods_version_id)) END,
    'draft', CASE WHEN d.ods_version_id IS NULL THEN NULL ELSE jsonb_build_object(
       'ods_version_id', d.ods_version_id, 'version_no', d.version_no, 'draft_revision', d.draft_revision,
       'definition_hash', d.definition_hash, 'updated_at', d.updated_at,
       'definition', meta.object_ods_definition(d.ods_version_id),
       'last_validation', CASE WHEN lv.validation_id IS NULL THEN NULL ELSE jsonb_build_object(
          'ok', lv.ok, 'issues', lv.issues, 'draft_revision', lv.draft_revision,
          'definition_hash', lv.definition_hash, 'created_at', lv.created_at) END) END);
END;
$fn$;

-- Create (p_expected_revision NULL) or replace the ODS draft of an object.
-- p_config: {"target_table": "t", "fields": [{"target_column": .., "transform": "COPY", "stg_column": ..,
--   "stg_type": "TEXT", "target_type": "TEXT", "conversion": "NONE", "param": null,
--   "write_policy": "AUTHORITATIVE", "allow_explicit_null": false}, ...], "key": ["target_column", ...]}
CREATE FUNCTION app.integration_ods_config_save_draft(
  p_token_hash text, p_source_object_id bigint, p_expected_revision integer, p_config jsonb)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  o meta.source_object;
  d meta.object_ods_version;
  v_current bigint;
  f jsonb;
  k jsonb;
  i integer := 0;
BEGIN
  SELECT * INTO o FROM meta.source_object x WHERE x.source_object_id = p_source_object_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source object not found'; END IF;
  IF NOT o.is_active THEN RAISE EXCEPTION 'Source object is not active'; END IF;
  IF meta.source_is_legacy(o.source_system_id) THEN
    RAISE EXCEPTION 'Legacy CRM sources keep their existing ODS transformation';
  END IF;
  IF jsonb_typeof(p_config) IS DISTINCT FROM 'object' OR jsonb_typeof(p_config -> 'fields') IS DISTINCT FROM 'array'
     OR jsonb_typeof(coalesce(p_config -> 'key', '[]'::jsonb)) <> 'array' THEN
    RAISE EXCEPTION 'Invalid ODS configuration document';
  END IF;
  SELECT * INTO d FROM meta.object_ods_version x
   WHERE x.source_object_id = p_source_object_id AND x.status = 'DRAFT' FOR UPDATE;
  IF d.ods_version_id IS NULL THEN
    IF p_expected_revision IS NOT NULL THEN RAISE EXCEPTION 'Draft not found or changed by another user'; END IF;
    SELECT ods_version_id INTO v_current FROM meta.object_ods_current WHERE source_object_id = p_source_object_id;
    INSERT INTO meta.object_ods_version(source_object_id, version_no, based_on_ods_version_id, created_by, updated_by)
    VALUES (p_source_object_id,
            coalesce((SELECT max(version_no) FROM meta.object_ods_version WHERE source_object_id = p_source_object_id), 0) + 1,
            v_current, v_actor, v_actor)
    RETURNING * INTO d;
  ELSIF p_expected_revision IS DISTINCT FROM d.draft_revision THEN
    RAISE EXCEPTION 'Draft not found or changed by another user';
  ELSE
    DELETE FROM meta.object_ods_key_part WHERE ods_version_id = d.ods_version_id;
    DELETE FROM meta.object_ods_field WHERE ods_version_id = d.ods_version_id;
  END IF;
  FOR f IN SELECT * FROM jsonb_array_elements(p_config -> 'fields') LOOP
    i := i + 1;
    INSERT INTO meta.object_ods_field(ods_version_id, target_column, field_position, transform, stg_column, stg_type,
        target_type, conversion, param, write_policy, allow_explicit_null)
    VALUES (d.ods_version_id, f ->> 'target_column', i, f ->> 'transform', nullif(f ->> 'stg_column', ''),
        nullif(f ->> 'stg_type', ''), f ->> 'target_type', coalesce(f ->> 'conversion', 'NONE'), f ->> 'param',
        coalesce(f ->> 'write_policy', 'AUTHORITATIVE'), coalesce((f ->> 'allow_explicit_null')::boolean, false));
  END LOOP;
  i := 0;
  FOR k IN SELECT * FROM jsonb_array_elements(coalesce(p_config -> 'key', '[]'::jsonb)) LOOP
    i := i + 1;
    INSERT INTO meta.object_ods_key_part(ods_version_id, key_position, target_column)
    VALUES (d.ods_version_id, i, k #>> '{}');
  END LOOP;
  UPDATE meta.object_ods_version
     SET target_table = nullif(p_config ->> 'target_table', ''),
         draft_revision = CASE WHEN p_expected_revision IS NULL THEN draft_revision ELSE draft_revision + 1 END,
         updated_at = clock_timestamp(), updated_by = v_actor
   WHERE ods_version_id = d.ods_version_id RETURNING * INTO d;
  UPDATE meta.object_ods_version SET definition_hash = meta.object_ods_hash(d.ods_version_id)
   WHERE ods_version_id = d.ods_version_id;
  PERFORM audit.log_security_event('INTEGRATION_ODS_CONFIG_SAVED', 'SOURCE_OBJECT', p_source_object_id::text,
    CASE WHEN p_expected_revision IS NULL THEN 'CREATE' ELSE 'UPDATE' END, 'SUCCESS',
    jsonb_build_object('ods_version_id', d.ods_version_id, 'version_no', d.version_no,
                       'draft_revision', d.draft_revision, 'actor_user_id', v_actor));
  RETURN d.draft_revision;
END;
$fn$;

-- Result of the API validation (white list, types) for the current ODS draft revision.
CREATE FUNCTION app.integration_ods_config_record_validation(
  p_token_hash text, p_source_object_id bigint, p_draft_revision integer, p_ok boolean, p_issues jsonb)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  d meta.object_ods_version;
  v_id bigint;
BEGIN
  IF p_ok IS NULL OR jsonb_typeof(coalesce(p_issues, '[]'::jsonb)) <> 'array' THEN
    RAISE EXCEPTION 'Invalid validation result';
  END IF;
  SELECT * INTO d FROM meta.object_ods_version x
   WHERE x.source_object_id = p_source_object_id AND x.status = 'DRAFT' FOR UPDATE;
  IF NOT FOUND OR d.draft_revision IS DISTINCT FROM p_draft_revision THEN
    RAISE EXCEPTION 'Draft not found or changed by another user';
  END IF;
  INSERT INTO meta.object_ods_validation(ods_version_id, draft_revision, definition_hash, ok, issues, created_by)
  VALUES (d.ods_version_id, d.draft_revision, d.definition_hash, p_ok, coalesce(p_issues, '[]'::jsonb), v_actor)
  RETURNING validation_id INTO v_id;
  PERFORM audit.log_security_event('INTEGRATION_ODS_CONFIG_VALIDATED', 'SOURCE_OBJECT', p_source_object_id::text,
    'VALIDATE', CASE WHEN p_ok THEN 'SUCCESS' ELSE 'FAILURE' END,
    jsonb_build_object('ods_version_id', d.ods_version_id, 'draft_revision', d.draft_revision,
                       'actor_user_id', v_actor));
  RETURN v_id;
END;
$fn$;

-- Publish the validated ODS draft: it becomes the immutable ODS version in force, READY.
CREATE FUNCTION app.integration_ods_config_publish(
  p_token_hash text, p_source_object_id bigint, p_draft_revision integer)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  d meta.object_ods_version;
  lv meta.object_ods_validation;
  v_stg jsonb := meta.object_stg_table(p_source_object_id);
  v_missing text;
  v_conflict text;
BEGIN
  SELECT * INTO d FROM meta.object_ods_version x
   WHERE x.source_object_id = p_source_object_id AND x.status = 'DRAFT' FOR UPDATE;
  IF NOT FOUND OR d.draft_revision IS DISTINCT FROM p_draft_revision THEN
    RAISE EXCEPTION 'Draft not found or changed by another user';
  END IF;
  IF d.definition_hash IS DISTINCT FROM meta.object_ods_hash(d.ods_version_id) THEN
    RAISE EXCEPTION 'Draft hash mismatch';
  END IF;
  SELECT * INTO lv FROM meta.object_ods_validation x WHERE x.ods_version_id = d.ods_version_id
   ORDER BY x.validation_id DESC LIMIT 1;
  IF NOT FOUND OR NOT lv.ok OR lv.draft_revision <> d.draft_revision OR lv.definition_hash <> d.definition_hash THEN
    RAISE EXCEPTION 'Publish requires a successful validation of this draft revision';
  END IF;
  IF v_stg IS NULL THEN
    RAISE EXCEPTION 'The object has no published STG configuration (LOAD version with target schema stg)';
  END IF;
  IF d.target_table IS NULL THEN RAISE EXCEPTION 'Target table not selected'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace
                  WHERE n.nspname = 'ods' AND t.relname = d.target_table AND t.relkind IN ('r', 'p')) THEN
    RAISE EXCEPTION 'Target table ods.% does not exist; apply its change proposal first', d.target_table;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM meta.object_ods_field WHERE ods_version_id = d.ods_version_id) THEN
    RAISE EXCEPTION 'Configuration has no fields';
  END IF;
  SELECT string_agg(f.target_column, ', ' ORDER BY f.field_position) INTO v_missing
    FROM meta.object_ods_field f
   WHERE f.ods_version_id = d.ods_version_id
     AND NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_class t ON t.oid = a.attrelid
                       JOIN pg_namespace n ON n.oid = t.relnamespace
                      WHERE n.nspname = 'ods' AND t.relname = d.target_table
                        AND a.attname = f.target_column AND a.attnum > 0 AND NOT a.attisdropped);
  IF v_missing IS NOT NULL THEN RAISE EXCEPTION 'Target columns do not exist: %', v_missing; END IF;
  SELECT string_agg(f.stg_column, ', ' ORDER BY f.field_position) INTO v_missing
    FROM meta.object_ods_field f
   WHERE f.ods_version_id = d.ods_version_id AND f.stg_column IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_class t ON t.oid = a.attrelid
                       JOIN pg_namespace n ON n.oid = t.relnamespace
                      WHERE n.nspname = 'stg' AND t.relname = v_stg ->> 'table'
                        AND a.attname = f.stg_column AND a.attnum > 0 AND NOT a.attisdropped);
  IF v_missing IS NOT NULL THEN RAISE EXCEPTION 'STG columns do not exist: %', v_missing; END IF;
  IF NOT EXISTS (SELECT 1 FROM meta.object_ods_key_part WHERE ods_version_id = d.ods_version_id) THEN
    RAISE EXCEPTION 'Business key not selected';
  END IF;
  IF EXISTS (SELECT 1 FROM meta.object_ods_key_part k JOIN meta.object_ods_field f
               ON f.ods_version_id = k.ods_version_id AND f.target_column = k.target_column
              WHERE k.ods_version_id = d.ods_version_id
                AND (f.stg_column IS NULL OR f.allow_explicit_null OR f.transform IN ('DEFAULT', 'EMPTY_TO_NULL'))) THEN
    RAISE EXCEPTION 'Key fields must come from STG columns and may not be NULL or replaced';
  END IF;
  -- No column of the ODS table may be AUTHORITATIVE for two objects (ODS lines and LOAD versions into ods).
  SELECT string_agg(DISTINCT f.target_column, ', ') INTO v_conflict
    FROM meta.object_ods_field f
   WHERE f.ods_version_id = d.ods_version_id AND f.write_policy = 'AUTHORITATIVE'
     AND (EXISTS (SELECT 1 FROM meta.object_ods_current oc JOIN meta.object_ods_version ov ON ov.ods_version_id = oc.ods_version_id
                   JOIN meta.object_ods_field of2 ON of2.ods_version_id = ov.ods_version_id
                  WHERE oc.source_object_id <> p_source_object_id AND ov.target_table = d.target_table
                    AND of2.target_column = f.target_column AND of2.write_policy = 'AUTHORITATIVE')
          OR EXISTS (SELECT 1 FROM meta.object_config_current cc JOIN meta.object_config_version cv ON cv.version_id = cc.version_id
                       JOIN meta.object_config_field cf ON cf.version_id = cv.version_id
                      WHERE cv.target_schema = 'ods' AND cv.target_table = d.target_table
                        AND cf.target_column = f.target_column AND cf.write_policy = 'AUTHORITATIVE'));
  IF v_conflict IS NOT NULL THEN
    RAISE EXCEPTION 'Another object is already AUTHORITATIVE for: %', v_conflict;
  END IF;
  UPDATE meta.object_ods_version
     SET status = 'PUBLISHED', published_at = clock_timestamp(), published_by = v_actor,
         updated_at = clock_timestamp(), updated_by = v_actor
   WHERE ods_version_id = d.ods_version_id;
  INSERT INTO meta.object_ods_current(source_object_id, ods_version_id, status, updated_by)
  VALUES (p_source_object_id, d.ods_version_id, 'READY', v_actor)
  ON CONFLICT (source_object_id) DO UPDATE
     SET ods_version_id = EXCLUDED.ods_version_id, status = 'READY', status_details = '{}'::jsonb,
         updated_at = clock_timestamp(), updated_by = v_actor;
  PERFORM audit.log_security_event('INTEGRATION_ODS_CONFIG_PUBLISHED', 'SOURCE_OBJECT', p_source_object_id::text,
    'PUBLISH', 'SUCCESS', jsonb_build_object('ods_version_id', d.ods_version_id, 'version_no', d.version_no,
    'definition_hash', d.definition_hash, 'actor_user_id', v_actor));
  RETURN d.version_no;
END;
$fn$;

-- Store a DDL proposal for a new ODS table or columns (never executed here; applied by a package).
CREATE FUNCTION app.integration_ods_proposal_create(
  p_token_hash text, p_source_object_id bigint, p_change_kind text, p_target_table text, p_ddl text)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  d meta.object_ods_version;
  v_id bigint;
BEGIN
  SELECT * INTO d FROM meta.object_ods_version x
   WHERE x.source_object_id = p_source_object_id AND x.status = 'DRAFT';
  IF NOT FOUND THEN RAISE EXCEPTION 'Save an ODS draft first'; END IF;
  IF p_change_kind = 'CREATE_TABLE' AND EXISTS (SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace
       WHERE n.nspname = 'ods' AND t.relname = p_target_table) THEN
    RAISE EXCEPTION 'Target table already exists';
  END IF;
  INSERT INTO meta.target_change_proposal(source_object_id, version_id, ods_version_id, change_kind, target_schema,
      target_table, ddl, created_by)
  VALUES (p_source_object_id, NULL, d.ods_version_id, p_change_kind, 'ods', p_target_table, p_ddl, v_actor)
  RETURNING proposal_id INTO v_id;
  PERFORM audit.log_security_event('INTEGRATION_TARGET_PROPOSAL_CREATED', 'SOURCE_OBJECT', p_source_object_id::text,
    'CREATE', 'SUCCESS', jsonb_build_object('proposal_id', v_id, 'change_kind', p_change_kind,
    'target', 'ods.' || p_target_table, 'ods_version_id', d.ods_version_id, 'actor_user_id', v_actor));
  RETURN v_id;
END;
$fn$;

-- ------------------------------------------------------------------ ODS step of a run (O3, O4)

CREATE TABLE etl.object_ods_run (
  ods_run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_object_id bigint NOT NULL REFERENCES meta.source_object(source_object_id) ON DELETE RESTRICT,
  ods_version_id bigint NOT NULL REFERENCES meta.object_ods_version(ods_version_id) ON DELETE RESTRICT,
  definition_hash text NOT NULL CHECK (definition_hash ~ '^[0-9a-f]{64}$'),
  stg_run_id bigint REFERENCES etl.object_run(run_id) ON DELETE RESTRICT,
  stg_table text NOT NULL CHECK (stg_table ~ '^[a-z_][a-z0-9_]{0,62}$'),
  batch_run_id bigint NOT NULL UNIQUE REFERENCES etl.batch_run(batch_run_id) ON DELETE RESTRICT,
  job_run_id bigint NOT NULL UNIQUE REFERENCES etl.job_run(job_run_id) ON DELETE RESTRICT,
  status text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
  rows_read bigint NOT NULL DEFAULT 0 CHECK (rows_read >= 0),
  rows_inserted bigint NOT NULL DEFAULT 0 CHECK (rows_inserted >= 0),
  rows_updated bigint NOT NULL DEFAULT 0 CHECK (rows_updated >= 0),
  rows_unchanged bigint NOT NULL DEFAULT 0 CHECK (rows_unchanged >= 0),
  error_code text CHECK (error_code ~ '^[A-Z][A-Z0-9_]{0,59}$'),
  started_by text NOT NULL,
  started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  finished_at timestamptz,
  CONSTRAINT object_ods_run_result CHECK (
    (status = 'RUNNING' AND finished_at IS NULL AND error_code IS NULL)
    OR (status = 'SUCCESS' AND finished_at IS NOT NULL AND error_code IS NULL)
    OR (status = 'FAILED' AND finished_at IS NOT NULL AND error_code IS NOT NULL))
);
CREATE INDEX object_ods_run_object ON etl.object_ods_run(source_object_id, ods_run_id);

CREATE TABLE etl.object_ods_record_change (
  change_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ods_run_id bigint NOT NULL REFERENCES etl.object_ods_run(ods_run_id) ON DELETE RESTRICT,
  change_kind text NOT NULL CHECK (change_kind IN ('INSERT', 'UPDATE')),
  record_key jsonb NOT NULL CHECK (jsonb_typeof(record_key) = 'object'),
  changes jsonb NOT NULL CHECK (jsonb_typeof(changes) = 'object'),
  changed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX object_ods_record_change_run ON etl.object_ods_record_change(ods_run_id);
CREATE INDEX object_ods_record_change_key ON etl.object_ods_record_change USING gin (record_key);

-- History rows only for a RUNNING ODS run; never changed or removed.
CREATE FUNCTION etl.object_ods_record_change_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl AS $fn$
BEGIN
  IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION '% of ODS record history prohibited', TG_OP; END IF;
  IF NOT EXISTS (SELECT 1 FROM etl.object_ods_run r WHERE r.ods_run_id = NEW.ods_run_id AND r.status = 'RUNNING') THEN
    RAISE EXCEPTION 'Record history requires a running ODS run';
  END IF;
  RETURN NEW;
END;
$fn$;
CREATE TRIGGER object_ods_record_change_insert BEFORE INSERT ON etl.object_ods_record_change
FOR EACH ROW EXECUTE FUNCTION etl.object_ods_record_change_guard();
CREATE TRIGGER object_ods_record_change_immutable BEFORE UPDATE OR DELETE ON etl.object_ods_record_change
FOR EACH ROW EXECUTE FUNCTION etl.object_ods_record_change_guard();
CREATE TRIGGER object_ods_record_change_truncate BEFORE TRUNCATE ON etl.object_ods_record_change
FOR EACH STATEMENT EXECUTE FUNCTION etl.object_ods_record_change_guard();

-- Close an ODS run (shared by finish and by ABANDONED handling in begin).
CREATE FUNCTION etl.object_ods_run_close(p_ods_run_id bigint, p_ok boolean, p_rows_read bigint,
  p_rows_inserted bigint, p_rows_updated bigint, p_rows_unchanged bigint, p_error_code text, p_error_message text,
  p_record_key jsonb, p_column_name text)
RETURNS text
LANGUAGE plpgsql SET search_path = pg_catalog, etl AS $fn$
DECLARE
  r etl.object_ods_run;
  v_job bigint;
  v_status text := CASE WHEN p_ok THEN 'SUCCESS' ELSE 'FAILED' END;
  v_message text := CASE WHEN p_ok THEN 'ODS load completed' ELSE left(coalesce(p_error_message, p_error_code), 2000) END;
BEGIN
  UPDATE etl.object_ods_run
     SET status = v_status, rows_read = coalesce(p_rows_read, 0), rows_inserted = coalesce(p_rows_inserted, 0),
         rows_updated = coalesce(p_rows_updated, 0), rows_unchanged = coalesce(p_rows_unchanged, 0),
         error_code = CASE WHEN p_ok THEN NULL ELSE p_error_code END, finished_at = clock_timestamp()
   WHERE ods_run_id = p_ods_run_id AND status = 'RUNNING'
  RETURNING * INTO r;
  IF NOT FOUND THEN RAISE EXCEPTION 'ODS run not found or already finished'; END IF;
  UPDATE etl.job_run
     SET status = v_status, finished_at = clock_timestamp(), rows_read = r.rows_read, rows_inserted = r.rows_inserted,
         rows_updated = r.rows_updated, error_count = CASE WHEN p_ok THEN 0 ELSE 1 END, message = v_message
   WHERE job_run_id = r.job_run_id
  RETURNING job_id INTO v_job;
  UPDATE etl.batch_run SET status = v_status, finished_at = clock_timestamp(), message = v_message
   WHERE batch_run_id = r.batch_run_id;
  IF NOT p_ok THEN
    INSERT INTO etl.error_log(batch_run_id, job_run_id, job_id, source_object_id, error_stage, error_code,
        error_message, record_key, column_name)
    VALUES (r.batch_run_id, r.job_run_id, v_job, r.source_object_id, 'ODS_LOAD', p_error_code, v_message,
        p_record_key, left(p_column_name, 200));
  END IF;
  RETURN v_status;
END;
$fn$;

-- Start the ODS step of an object (dwh_etl). p_stg_run_id: the successful STG run it follows, or NULL.
CREATE FUNCTION etl.object_ods_run_begin(p_source_object_id bigint, p_stg_run_id bigint)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl, meta, audit AS $fn$
DECLARE
  o meta.source_object;
  c meta.object_ods_current;
  v meta.object_ods_version;
  v_stg jsonb := meta.object_stg_table(p_source_object_id);
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
    RAISE EXCEPTION 'Legacy CRM sources are transformed by their own ETL';
  END IF;
  SELECT * INTO c FROM meta.object_ods_current x WHERE x.source_object_id = p_source_object_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Object has no published ODS configuration'; END IF;
  IF c.status <> 'READY' THEN RAISE EXCEPTION 'ODS configuration is %, resolve it before a run', c.status; END IF;
  SELECT * INTO v FROM meta.object_ods_version x WHERE x.ods_version_id = c.ods_version_id;
  IF v_stg IS NULL THEN RAISE EXCEPTION 'The object has no published STG configuration'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace
                  WHERE n.nspname = 'stg' AND t.relname = v_stg ->> 'table' AND t.relkind IN ('r', 'p'))
     OR NOT EXISTS (SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace
                     WHERE n.nspname = 'ods' AND t.relname = v.target_table AND t.relkind IN ('r', 'p')) THEN
    RAISE EXCEPTION 'STG or ODS table does not exist';
  END IF;
  IF p_stg_run_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM etl.object_run r
        WHERE r.run_id = p_stg_run_id AND r.source_object_id = p_source_object_id AND r.status = 'SUCCESS') THEN
    RAISE EXCEPTION 'STG run is not a successful run of this object';
  END IF;
  IF NOT pg_try_advisory_lock(hashtext('dwh.object_ods_run'), p_source_object_id::integer) THEN
    RAISE EXCEPTION 'Another ODS run of this object is in progress';
  END IF;
  PERFORM set_config('dwh.security_source', 'ETL', true);
  FOR stale IN SELECT r.ods_run_id FROM etl.object_ods_run r
                WHERE r.source_object_id = p_source_object_id AND r.status = 'RUNNING' ORDER BY r.ods_run_id LOOP
    PERFORM etl.object_ods_run_close(stale.ods_run_id, false, 0, 0, 0, 0, 'ABANDONED',
      'Previous ODS run did not finish (session ended)', NULL, NULL);
  END LOOP;
  SELECT j.job_id INTO v_job FROM etl.job j WHERE j.code = 'OBJECT_ODS_' || p_source_object_id;
  IF NOT FOUND THEN
    INSERT INTO etl.job(code, name, job_type, source_system_id, source_object_id, target_schema, target_object,
        description)
    VALUES ('OBJECT_ODS_' || p_source_object_id, left('ODS load: ' || o.source_schema || '.' || o.source_object, 200),
        'TRANSFORM', o.source_system_id, p_source_object_id, 'ods', v.target_table,
        'I06-05: published ODS configuration')
    RETURNING job_id INTO v_job;
  ELSE
    UPDATE etl.job SET target_object = v.target_table, updated_at = now()
     WHERE job_id = v_job AND target_object IS DISTINCT FROM v.target_table;
  END IF;
  INSERT INTO etl.batch_run(batch_code, business_date, trigger_type, status, started_at, message, created_by,
      pipeline_code, run_mode, attempt_no)
  VALUES ('OBJECT_ODS_' || p_source_object_id, current_date, 'MANUAL', 'RUNNING', clock_timestamp(),
      left('ODS load ' || o.source_schema || '.' || o.source_object || ' -> ods.' || v.target_table
           || ' v' || v.version_no, 2000),
      left(session_user::text, 128), 'OBJECT_LOAD', 'FULL', 1)
  RETURNING batch_run_id INTO v_batch;
  INSERT INTO etl.job_run(batch_run_id, job_id, status, started_at)
  VALUES (v_batch, v_job, 'RUNNING', clock_timestamp()) RETURNING job_run_id INTO v_job_run;
  INSERT INTO etl.object_ods_run(source_object_id, ods_version_id, definition_hash, stg_run_id, stg_table,
      batch_run_id, job_run_id, started_by)
  VALUES (p_source_object_id, v.ods_version_id, v.definition_hash, p_stg_run_id, v_stg ->> 'table', v_batch,
      v_job_run, session_user::text)
  RETURNING ods_run_id INTO v_run;
  PERFORM audit.log_security_event('ETL_OBJECT_ODS_RUN_STARTED', 'SOURCE_OBJECT', p_source_object_id::text, 'RUN',
    'SUCCESS', jsonb_build_object('ods_run_id', v_run, 'batch_run_id', v_batch, 'ods_version_id', v.ods_version_id,
                                  'stg_run_id', p_stg_run_id, 'actor_role', session_user::text));
  RETURN jsonb_build_object(
    'ods_run_id', v_run, 'batch_run_id', v_batch, 'job_run_id', v_job_run,
    'ods_version_id', v.ods_version_id, 'version_no', v.version_no, 'definition_hash', v.definition_hash,
    'definition', meta.object_ods_definition(v.ods_version_id),
    'stg', jsonb_build_object('schema', 'stg', 'table', v_stg ->> 'table'));
END;
$fn$;

CREATE FUNCTION etl.object_ods_run_finish(p_ods_run_id bigint, p_ok boolean, p_rows_read bigint,
  p_rows_inserted bigint, p_rows_updated bigint, p_rows_unchanged bigint, p_error_code text, p_error_message text,
  p_record_key jsonb, p_column_name text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl, audit AS $fn$
DECLARE
  r etl.object_ods_run;
  v_status text;
BEGIN
  IF p_ok IS NULL OR (NOT p_ok AND (p_error_code IS NULL OR p_error_code !~ '^[A-Z][A-Z0-9_]{0,59}$'))
     OR (p_ok AND p_error_code IS NOT NULL)
     OR (p_record_key IS NOT NULL AND jsonb_typeof(p_record_key) <> 'object') THEN
    RAISE EXCEPTION 'Invalid run result';
  END IF;
  SELECT * INTO r FROM etl.object_ods_run x WHERE x.ods_run_id = p_ods_run_id FOR UPDATE;
  IF NOT FOUND OR r.status <> 'RUNNING' THEN RAISE EXCEPTION 'ODS run not found or already finished'; END IF;
  IF r.started_by IS DISTINCT FROM session_user::text THEN RAISE EXCEPTION 'ODS run belongs to another role'; END IF;
  PERFORM set_config('dwh.security_source', 'ETL', true);
  v_status := etl.object_ods_run_close(p_ods_run_id, p_ok, p_rows_read, p_rows_inserted, p_rows_updated,
    p_rows_unchanged, p_error_code, p_error_message, p_record_key, p_column_name);
  PERFORM pg_advisory_unlock(hashtext('dwh.object_ods_run'), r.source_object_id::integer);
  PERFORM audit.log_security_event('ETL_OBJECT_ODS_RUN_FINISHED', 'SOURCE_OBJECT', r.source_object_id::text, 'RUN',
    CASE WHEN p_ok THEN 'SUCCESS' ELSE 'FAILURE' END,
    jsonb_build_object('ods_run_id', p_ods_run_id, 'status', v_status, 'error_code', p_error_code,
                       'rows_read', p_rows_read, 'rows_inserted', p_rows_inserted, 'rows_updated', p_rows_updated));
  RETURN v_status;
END;
$fn$;

-- ------------------------------------------------------------------ privileges

REVOKE ALL ON meta.object_ods_version, meta.object_ods_field, meta.object_ods_key_part, meta.object_ods_validation,
  meta.object_ods_current, etl.object_ods_run, etl.object_ods_record_change FROM PUBLIC;
REVOKE ALL ON FUNCTION
  meta.object_ods_guard(), meta.object_ods_definition(bigint), meta.object_ods_hash(bigint),
  meta.object_stg_table(bigint),
  app.integration_ods_config_get(text, bigint),
  app.integration_ods_config_save_draft(text, bigint, integer, jsonb),
  app.integration_ods_config_record_validation(text, bigint, integer, boolean, jsonb),
  app.integration_ods_config_publish(text, bigint, integer),
  app.integration_ods_proposal_create(text, bigint, text, text, text),
  etl.object_ods_record_change_guard(),
  etl.object_ods_run_close(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text),
  etl.object_ods_run_begin(bigint, bigint),
  etl.object_ods_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text)
FROM PUBLIC;

-- Default privileges of meta/app/etl (rule since 005): remove every grant on the new objects except
-- the owner's and report it, then grant only what is intended.
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT DISTINCT CASE c.relkind WHEN 'S' THEN 'SEQUENCE ' ELSE 'TABLE ' END || c.oid::regclass::text AS obj, x.grantee
      FROM pg_class c, aclexplode(c.relacl) x
     WHERE (c.oid IN ('meta.object_ods_version'::regclass, 'meta.object_ods_field'::regclass,
                      'meta.object_ods_key_part'::regclass, 'meta.object_ods_validation'::regclass,
                      'meta.object_ods_current'::regclass, 'etl.object_ods_run'::regclass,
                      'etl.object_ods_record_change'::regclass)
            OR c.oid IN (SELECT d.objid FROM pg_depend d
                          WHERE d.refobjid IN ('meta.object_ods_version'::regclass, 'meta.object_ods_validation'::regclass,
                                               'etl.object_ods_run'::regclass, 'etl.object_ods_record_change'::regclass)
                            AND d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')))
       AND x.grantee <> c.relowner
    UNION
    SELECT 'FUNCTION ' || p.oid::regprocedure::text, x.grantee
      FROM pg_proc p, aclexplode(p.proacl) x
     WHERE p.oid IN ('meta.object_ods_guard()'::regprocedure, 'meta.object_ods_definition(bigint)'::regprocedure,
                     'meta.object_ods_hash(bigint)'::regprocedure, 'meta.object_stg_table(bigint)'::regprocedure,
                     'app.integration_ods_config_get(text, bigint)'::regprocedure,
                     'app.integration_ods_config_save_draft(text, bigint, integer, jsonb)'::regprocedure,
                     'app.integration_ods_config_record_validation(text, bigint, integer, boolean, jsonb)'::regprocedure,
                     'app.integration_ods_config_publish(text, bigint, integer)'::regprocedure,
                     'app.integration_ods_proposal_create(text, bigint, text, text, text)'::regprocedure,
                     'etl.object_ods_record_change_guard()'::regprocedure,
                     'etl.object_ods_run_close(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text)'::regprocedure,
                     'etl.object_ods_run_begin(bigint, bigint)'::regprocedure,
                     'etl.object_ods_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text)'::regprocedure)
       AND x.grantee <> p.proowner
  LOOP
    EXECUTE format('REVOKE ALL ON %s FROM %s', r.obj,
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED % FROM %', r.obj,
                 CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(r.grantee) END;
  END LOOP;
END
$acl$;

GRANT EXECUTE ON FUNCTION
  app.integration_ods_config_get(text, bigint),
  app.integration_ods_config_save_draft(text, bigint, integer, jsonb),
  app.integration_ods_config_record_validation(text, bigint, integer, boolean, jsonb),
  app.integration_ods_config_publish(text, bigint, integer),
  app.integration_ods_proposal_create(text, bigint, text, text, text)
TO dwh_app;
GRANT EXECUTE ON FUNCTION
  etl.object_ods_run_begin(bigint, bigint),
  etl.object_ods_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text)
TO dwh_etl;
GRANT INSERT ON etl.object_ods_record_change TO dwh_etl;
