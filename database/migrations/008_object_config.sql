-- I05 (step 3): versioned field mapping configuration of a source object.
--
-- Decisions of the I05 plan (approved 04.11.2026):
--   M1  one version line per OBJECT (meta.source_object); objects publish independently;
--   M2  configuration only (versions, target, fields, key, validations, change proposals);
--       record history / presence tables come with I06;
--   M4  target = existing table of schema stg or ods; a new table or column is a
--       change PROPOSAL (M5): DDL text stored here, applied only as a separate
--       database package (E0 -> E1 -> E2); nothing here executes DDL;
--   M7  sources of the legacy CRM contour (rows in meta.column_contract or
--       meta.mapping_rule) get no schema snapshot from the UI.
--
-- meta.object_config_version  DRAFT -> PUBLISHED (immutable afterwards); at most one
--   DRAFT per object; draft_revision guards concurrent edits; definition_hash is
--   computed here from the canonical definition (target, fields, key).
-- meta.object_config_field    target column <- source column, normalized source/target
--   type, conversion (NONE / SAFE / CONFIRMED), write policy (AUTHORITATIVE /
--   INSERT_ONLY / NO_WRITE), explicit NULL allowed or not.
-- meta.object_config_key_part ordered business key (target columns of the field map).
-- meta.object_config_validation  validation results made by the API (type checks).
-- meta.object_config_current  published version in force per object, READY or DRIFT.
-- meta.target_change_proposal DDL proposals (never executed from here).
--
-- Publish re-checks in the database: the draft revision and hash match a successful
-- validation, the target table and every target column exist, the key is not empty
-- and not NO_WRITE, and no other object's published version is AUTHORITATIVE for the
-- same physical column (I02-02, A08).
-- All app.* wrappers check the DWH session and write audit.log_security_event.
-- Tables: no grants beyond the owner (default privileges of meta are removed and
-- reported); dwh_app may EXECUTE the wrappers only.
-- Requires 001-007. Run as the owner of schemas app, meta:
--   psql -1 -v ON_ERROR_STOP=1 -f 008_object_config.sql

CREATE TABLE meta.object_config_version (
  version_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_object_id bigint NOT NULL REFERENCES meta.source_object(source_object_id) ON DELETE RESTRICT,
  version_no integer NOT NULL CHECK (version_no >= 1),
  status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'PUBLISHED')),
  based_on_version_id bigint REFERENCES meta.object_config_version(version_id) ON DELETE RESTRICT,
  target_schema text CHECK (target_schema IN ('stg', 'ods')),
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
  CONSTRAINT object_config_published_complete CHECK (
    status = 'DRAFT'
    OR (published_at IS NOT NULL AND published_by IS NOT NULL AND definition_hash IS NOT NULL
        AND target_schema IS NOT NULL AND target_table IS NOT NULL))
);
CREATE UNIQUE INDEX object_config_one_draft ON meta.object_config_version(source_object_id) WHERE status = 'DRAFT';

CREATE TABLE meta.object_config_field (
  version_id bigint NOT NULL REFERENCES meta.object_config_version(version_id) ON DELETE RESTRICT,
  target_column text NOT NULL CHECK (target_column ~ '^[a-z_][a-z0-9_]{0,62}$'),
  field_position integer NOT NULL CHECK (field_position >= 1),
  source_column text CHECK (char_length(source_column) BETWEEN 1 AND 200),
  source_type text NOT NULL CHECK (source_type ~ '^[A-Z]+(\([0-9]{1,4}(,[0-9]{1,4})?\))?$'),
  target_type text NOT NULL CHECK (target_type ~ '^[A-Z]+(\([0-9]{1,4}(,[0-9]{1,4})?\))?$'),
  conversion text NOT NULL CHECK (conversion IN ('NONE', 'SAFE', 'CONFIRMED')),
  write_policy text NOT NULL CHECK (write_policy IN ('AUTHORITATIVE', 'INSERT_ONLY', 'NO_WRITE')),
  allow_explicit_null boolean NOT NULL DEFAULT false,
  PRIMARY KEY (version_id, target_column),
  UNIQUE (version_id, field_position),
  CONSTRAINT object_config_field_source CHECK (source_column IS NOT NULL OR write_policy = 'NO_WRITE')
);

CREATE TABLE meta.object_config_key_part (
  version_id bigint NOT NULL,
  key_position integer NOT NULL CHECK (key_position >= 1),
  target_column text NOT NULL,
  PRIMARY KEY (version_id, key_position),
  UNIQUE (version_id, target_column),
  FOREIGN KEY (version_id, target_column) REFERENCES meta.object_config_field(version_id, target_column) ON DELETE RESTRICT
);

CREATE TABLE meta.object_config_validation (
  validation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  version_id bigint NOT NULL REFERENCES meta.object_config_version(version_id) ON DELETE RESTRICT,
  draft_revision integer NOT NULL,
  definition_hash text NOT NULL CHECK (definition_hash ~ '^[0-9a-f]{64}$'),
  ok boolean NOT NULL,
  issues jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(issues) = 'array'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  created_by bigint NOT NULL
);

CREATE TABLE meta.object_config_current (
  source_object_id bigint PRIMARY KEY REFERENCES meta.source_object(source_object_id) ON DELETE RESTRICT,
  version_id bigint NOT NULL UNIQUE REFERENCES meta.object_config_version(version_id) ON DELETE RESTRICT,
  status text NOT NULL CHECK (status IN ('READY', 'DRIFT')),
  status_details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(status_details) = 'object'),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by bigint NOT NULL
);

CREATE TABLE meta.target_change_proposal (
  proposal_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_object_id bigint NOT NULL REFERENCES meta.source_object(source_object_id) ON DELETE RESTRICT,
  version_id bigint NOT NULL REFERENCES meta.object_config_version(version_id) ON DELETE RESTRICT,
  change_kind text NOT NULL CHECK (change_kind IN ('CREATE_TABLE', 'ADD_COLUMNS')),
  target_schema text NOT NULL CHECK (target_schema IN ('stg', 'ods')),
  target_table text NOT NULL CHECK (target_table ~ '^[a-z_][a-z0-9_]{0,62}$'),
  ddl text NOT NULL CHECK (char_length(ddl) BETWEEN 1 AND 100000),
  status text NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'APPLIED', 'REJECTED')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  created_by bigint NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- A published version and its fields and key never change; drafts may.
CREATE FUNCTION meta.object_config_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, meta AS $fn$
DECLARE
  v_status text;
BEGIN
  IF TG_TABLE_NAME = 'object_config_version' THEN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Configuration versions cannot be deleted'; END IF;
    IF OLD.status = 'PUBLISHED' THEN RAISE EXCEPTION 'Published configuration is immutable'; END IF;
    IF NEW.source_object_id IS DISTINCT FROM OLD.source_object_id OR NEW.version_no IS DISTINCT FROM OLD.version_no
       OR NEW.created_at IS DISTINCT FROM OLD.created_at OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
      RAISE EXCEPTION 'Configuration version identity immutable';
    END IF;
    RETURN NEW;
  END IF;
  SELECT status INTO v_status FROM meta.object_config_version
   WHERE version_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
  IF v_status IS DISTINCT FROM 'DRAFT' THEN RAISE EXCEPTION 'Published configuration is immutable'; END IF;
  IF TG_OP = 'UPDATE' AND OLD.version_id IS DISTINCT FROM NEW.version_id THEN
    RAISE EXCEPTION 'Configuration rows cannot move between versions';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$fn$;
CREATE TRIGGER object_config_version_guard BEFORE UPDATE OR DELETE ON meta.object_config_version
FOR EACH ROW EXECUTE FUNCTION meta.object_config_guard();
CREATE TRIGGER object_config_field_guard BEFORE INSERT OR UPDATE OR DELETE ON meta.object_config_field
FOR EACH ROW EXECUTE FUNCTION meta.object_config_guard();
CREATE TRIGGER object_config_key_part_guard BEFORE INSERT OR UPDATE OR DELETE ON meta.object_config_key_part
FOR EACH ROW EXECUTE FUNCTION meta.object_config_guard();

CREATE FUNCTION meta.object_config_reject_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $fn$
BEGIN RAISE EXCEPTION '% of % prohibited', TG_OP, TG_TABLE_NAME; END;
$fn$;
CREATE TRIGGER object_config_validation_append_only BEFORE UPDATE OR DELETE ON meta.object_config_validation
FOR EACH ROW EXECUTE FUNCTION meta.object_config_reject_change();
CREATE TRIGGER object_config_version_truncate BEFORE TRUNCATE ON meta.object_config_version
FOR EACH STATEMENT EXECUTE FUNCTION meta.object_config_reject_change();
CREATE TRIGGER object_config_field_truncate BEFORE TRUNCATE ON meta.object_config_field
FOR EACH STATEMENT EXECUTE FUNCTION meta.object_config_reject_change();
CREATE TRIGGER object_config_key_part_truncate BEFORE TRUNCATE ON meta.object_config_key_part
FOR EACH STATEMENT EXECUTE FUNCTION meta.object_config_reject_change();
CREATE TRIGGER object_config_validation_truncate BEFORE TRUNCATE ON meta.object_config_validation
FOR EACH STATEMENT EXECUTE FUNCTION meta.object_config_reject_change();
CREATE TRIGGER object_config_current_truncate BEFORE TRUNCATE ON meta.object_config_current
FOR EACH STATEMENT EXECUTE FUNCTION meta.object_config_reject_change();
CREATE TRIGGER target_change_proposal_truncate BEFORE TRUNCATE ON meta.target_change_proposal
FOR EACH STATEMENT EXECUTE FUNCTION meta.object_config_reject_change();

-- Canonical definition of a version (target, fields in order, key in order) and its hash.
CREATE FUNCTION meta.object_config_definition(p_version_id bigint) RETURNS jsonb
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  SELECT jsonb_build_object(
    'target_schema', v.target_schema, 'target_table', v.target_table,
    'fields', coalesce((SELECT jsonb_agg(jsonb_build_object(
                'target_column', f.target_column, 'source_column', f.source_column,
                'source_type', f.source_type, 'target_type', f.target_type, 'conversion', f.conversion,
                'write_policy', f.write_policy, 'allow_explicit_null', f.allow_explicit_null)
              ORDER BY f.field_position) FROM meta.object_config_field f WHERE f.version_id = v.version_id), '[]'::jsonb),
    'key', coalesce((SELECT jsonb_agg(k.target_column ORDER BY k.key_position)
              FROM meta.object_config_key_part k WHERE k.version_id = v.version_id), '[]'::jsonb))
    FROM meta.object_config_version v WHERE v.version_id = p_version_id;
$fn$;

CREATE FUNCTION meta.object_config_hash(p_version_id bigint) RETURNS text
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  SELECT encode(sha256(convert_to(meta.object_config_definition(p_version_id)::text, 'UTF8')), 'hex');
$fn$;

-- True when the source belongs to the legacy CRM contour (decision M7).
CREATE FUNCTION meta.source_is_legacy(p_source_system_id bigint) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  SELECT EXISTS (SELECT 1 FROM meta.source_system s
                  WHERE s.source_system_id = p_source_system_id
                    AND (EXISTS (SELECT 1 FROM meta.column_contract cc WHERE cc.source_system_code = s.code)
                         OR EXISTS (SELECT 1 FROM meta.mapping_rule mr WHERE mr.source_system_code = s.code)));
$fn$;

-- Configuration of an object as the API sees it.
CREATE FUNCTION app.integration_object_config_get(p_token_hash text, p_source_object_id bigint)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
DECLARE
  o meta.source_object;
  c meta.object_config_current;
  d meta.object_config_version;
  p meta.object_config_version;
  lv meta.object_config_validation;
BEGIN
  PERFORM app.integration_admin_session_user(p_token_hash);
  SELECT * INTO o FROM meta.source_object x WHERE x.source_object_id = p_source_object_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source object not found'; END IF;
  SELECT * INTO c FROM meta.object_config_current x WHERE x.source_object_id = p_source_object_id;
  IF FOUND THEN SELECT * INTO p FROM meta.object_config_version x WHERE x.version_id = c.version_id; END IF;
  SELECT * INTO d FROM meta.object_config_version x WHERE x.source_object_id = p_source_object_id AND x.status = 'DRAFT';
  IF d.version_id IS NOT NULL THEN
    SELECT * INTO lv FROM meta.object_config_validation x WHERE x.version_id = d.version_id
     ORDER BY x.validation_id DESC LIMIT 1;
  END IF;
  RETURN jsonb_build_object(
    'object', jsonb_build_object('source_object_id', o.source_object_id, 'source_system_id', o.source_system_id,
       'source_schema', o.source_schema, 'source_object', o.source_object, 'object_type', o.object_type,
       'is_active', o.is_active, 'legacy', meta.source_is_legacy(o.source_system_id)),
    'current', CASE WHEN c.version_id IS NULL THEN NULL ELSE jsonb_build_object(
       'version_id', p.version_id, 'version_no', p.version_no, 'status', c.status, 'status_details', c.status_details,
       'published_at', p.published_at, 'definition_hash', p.definition_hash,
       'definition', meta.object_config_definition(p.version_id)) END,
    'draft', CASE WHEN d.version_id IS NULL THEN NULL ELSE jsonb_build_object(
       'version_id', d.version_id, 'version_no', d.version_no, 'draft_revision', d.draft_revision,
       'definition_hash', d.definition_hash, 'updated_at', d.updated_at,
       'definition', meta.object_config_definition(d.version_id),
       'last_validation', CASE WHEN lv.validation_id IS NULL THEN NULL ELSE jsonb_build_object(
          'ok', lv.ok, 'issues', lv.issues, 'draft_revision', lv.draft_revision,
          'definition_hash', lv.definition_hash, 'created_at', lv.created_at) END) END,
    'proposals', coalesce((SELECT jsonb_agg(jsonb_build_object(
       'proposal_id', tp.proposal_id, 'change_kind', tp.change_kind, 'target_schema', tp.target_schema,
       'target_table', tp.target_table, 'status', tp.status, 'created_at', tp.created_at, 'ddl', tp.ddl)
       ORDER BY tp.proposal_id DESC) FROM meta.target_change_proposal tp
       WHERE tp.source_object_id = p_source_object_id), '[]'::jsonb));
END;
$fn$;

-- Create (p_expected_revision NULL) or replace the draft of an object.
-- p_config: {"target_schema": "stg", "target_table": "t", "fields": [{"target_column": .., "source_column": ..,
--   "source_type": "TEXT", "target_type": "TEXT", "conversion": "NONE", "write_policy": "AUTHORITATIVE",
--   "allow_explicit_null": false}, ...], "key": ["target_column", ...]}
CREATE FUNCTION app.integration_object_config_save_draft(
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

-- Result of the API validation (type compatibility etc.) for the current draft revision.
CREATE FUNCTION app.integration_object_config_record_validation(
  p_token_hash text, p_source_object_id bigint, p_draft_revision integer, p_ok boolean, p_issues jsonb)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  d meta.object_config_version;
  v_id bigint;
BEGIN
  IF p_ok IS NULL OR jsonb_typeof(coalesce(p_issues, '[]'::jsonb)) <> 'array' THEN
    RAISE EXCEPTION 'Invalid validation result';
  END IF;
  SELECT * INTO d FROM meta.object_config_version x
   WHERE x.source_object_id = p_source_object_id AND x.status = 'DRAFT' FOR UPDATE;
  IF NOT FOUND OR d.draft_revision IS DISTINCT FROM p_draft_revision THEN
    RAISE EXCEPTION 'Draft not found or changed by another user';
  END IF;
  INSERT INTO meta.object_config_validation(version_id, draft_revision, definition_hash, ok, issues, created_by)
  VALUES (d.version_id, d.draft_revision, d.definition_hash, p_ok, coalesce(p_issues, '[]'::jsonb), v_actor)
  RETURNING validation_id INTO v_id;
  PERFORM audit.log_security_event('INTEGRATION_OBJECT_CONFIG_VALIDATED', 'SOURCE_OBJECT', p_source_object_id::text,
    'VALIDATE', CASE WHEN p_ok THEN 'SUCCESS' ELSE 'FAILURE' END,
    jsonb_build_object('version_id', d.version_id, 'draft_revision', d.draft_revision, 'actor_user_id', v_actor));
  RETURN v_id;
END;
$fn$;

-- Publish the validated draft: it becomes the immutable version in force, status READY.
CREATE FUNCTION app.integration_object_config_publish(
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

-- Schema drift found by the API (new snapshot vs the version in force).
CREATE FUNCTION app.integration_object_config_set_drift(
  p_token_hash text, p_source_object_id bigint, p_version_id bigint, p_has_drift boolean, p_details jsonb)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  v_status text := CASE WHEN p_has_drift THEN 'DRIFT' ELSE 'READY' END;
BEGIN
  IF p_has_drift IS NULL OR jsonb_typeof(coalesce(p_details, '{}'::jsonb)) <> 'object' THEN
    RAISE EXCEPTION 'Invalid drift result';
  END IF;
  UPDATE meta.object_config_current
     SET status = v_status, status_details = coalesce(p_details, '{}'::jsonb),
         updated_at = clock_timestamp(), updated_by = v_actor
   WHERE source_object_id = p_source_object_id AND version_id = p_version_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Published version not in force or changed'; END IF;
  PERFORM audit.log_security_event('INTEGRATION_OBJECT_CONFIG_DRIFT', 'SOURCE_OBJECT', p_source_object_id::text,
    'CHECK', CASE WHEN p_has_drift THEN 'FAILURE' ELSE 'SUCCESS' END,
    jsonb_build_object('version_id', p_version_id, 'status', v_status, 'actor_user_id', v_actor));
  RETURN v_status;
END;
$fn$;

-- Store a DDL proposal for a new target table or columns (never executed here).
CREATE FUNCTION app.integration_target_proposal_create(
  p_token_hash text, p_source_object_id bigint, p_change_kind text, p_target_schema text, p_target_table text,
  p_ddl text)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  d meta.object_config_version;
  v_id bigint;
BEGIN
  SELECT * INTO d FROM meta.object_config_version x
   WHERE x.source_object_id = p_source_object_id AND x.status = 'DRAFT';
  IF NOT FOUND THEN RAISE EXCEPTION 'Save a draft configuration first'; END IF;
  IF p_change_kind = 'CREATE_TABLE' AND EXISTS (SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace
       WHERE n.nspname = p_target_schema AND t.relname = p_target_table) THEN
    RAISE EXCEPTION 'Target table already exists';
  END IF;
  INSERT INTO meta.target_change_proposal(source_object_id, version_id, change_kind, target_schema, target_table, ddl,
      created_by)
  VALUES (p_source_object_id, d.version_id, p_change_kind, p_target_schema, p_target_table, p_ddl, v_actor)
  RETURNING proposal_id INTO v_id;
  PERFORM audit.log_security_event('INTEGRATION_TARGET_PROPOSAL_CREATED', 'SOURCE_OBJECT', p_source_object_id::text,
    'CREATE', 'SUCCESS', jsonb_build_object('proposal_id', v_id, 'change_kind', p_change_kind,
    'target', p_target_schema || '.' || p_target_table, 'actor_user_id', v_actor));
  RETURN v_id;
END;
$fn$;

-- Reject a proposal (APPLIED is set by the database package that applies it).
CREATE FUNCTION app.integration_target_proposal_reject(p_token_hash text, p_proposal_id bigint)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  v_object bigint;
BEGIN
  UPDATE meta.target_change_proposal SET status = 'REJECTED', updated_at = clock_timestamp()
   WHERE proposal_id = p_proposal_id AND status = 'PROPOSED'
  RETURNING source_object_id INTO v_object;
  IF NOT FOUND THEN RAISE EXCEPTION 'Proposal not found or not open'; END IF;
  PERFORM audit.log_security_event('INTEGRATION_TARGET_PROPOSAL_REJECTED', 'SOURCE_OBJECT', v_object::text,
    'UPDATE', 'SUCCESS', jsonb_build_object('proposal_id', p_proposal_id, 'actor_user_id', v_actor));
  RETURN true;
END;
$fn$;

-- M7: no schema snapshot from the UI for legacy CRM sources (006 function + one check).
CREATE OR REPLACE FUNCTION app.integration_source_metadata_capture(
  p_token_hash text, p_source_system_id bigint, p_source_database text, p_source_version text, p_objects jsonb)
RETURNS TABLE (metadata_capture_id bigint, objects_captured integer, columns_captured integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  v_capture bigint;
  v_snapshot bigint;
  v_objects integer := 0;
  v_columns integer := 0;
  o jsonb;
  col jsonb;
  so meta.source_object;
BEGIN
  IF meta.source_is_legacy(p_source_system_id) THEN
    RAISE EXCEPTION 'Legacy CRM sources take schema snapshots with their own ETL (capture_source_metadata)';
  END IF;
  IF jsonb_typeof(p_objects) IS DISTINCT FROM 'array' OR jsonb_array_length(p_objects) = 0 THEN
    RAISE EXCEPTION 'Snapshot must contain objects';
  END IF;
  IF EXISTS (SELECT 1 FROM meta.source_object x WHERE x.source_system_id = p_source_system_id AND x.is_active
               AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p_objects) e
                                WHERE (e ->> 'source_object_id')::bigint = x.source_object_id))
     OR (SELECT count(DISTINCT e ->> 'source_object_id') FROM jsonb_array_elements(p_objects) e)
        <> jsonb_array_length(p_objects) THEN
    RAISE EXCEPTION 'Snapshot must contain every active object of the source exactly once';
  END IF;
  INSERT INTO meta.metadata_capture AS mc (source_system_id, status, started_at, finished_at, source_database,
      source_version, created_by, message)
  VALUES (p_source_system_id, 'RUNNING', clock_timestamp(), NULL, left(p_source_database, 200),
      left(p_source_version, 200), 'dwh_user:' || v_actor, 'Source schema snapshot from integration administration')
  RETURNING mc.metadata_capture_id INTO v_capture;
  FOR o IN SELECT * FROM jsonb_array_elements(p_objects) LOOP
    SELECT * INTO so FROM meta.source_object x
     WHERE x.source_object_id = (o ->> 'source_object_id')::bigint
       AND x.source_system_id = p_source_system_id AND x.is_active;
    IF NOT FOUND THEN RAISE EXCEPTION 'Object is not an active object of this source'; END IF;
    IF jsonb_typeof(o -> 'columns') IS DISTINCT FROM 'array' OR jsonb_array_length(o -> 'columns') = 0 THEN
      RAISE EXCEPTION 'Source object has no columns: %.%', so.source_schema, so.source_object;
    END IF;
    INSERT INTO meta.source_object_snapshot(metadata_capture_id, source_object_id, source_schema, source_object,
        relkind, object_type)
    VALUES (v_capture, so.source_object_id, so.source_schema, so.source_object, o ->> 'relkind', o ->> 'object_type')
    RETURNING object_snapshot_id INTO v_snapshot;
    FOR col IN SELECT * FROM jsonb_array_elements(o -> 'columns') LOOP
      INSERT INTO meta.source_column_snapshot(object_snapshot_id, ordinal_position, column_name, formatted_type,
          udt_name, is_nullable, is_primary_key, is_unique_key, is_identity, is_generated,
          default_expression, column_comment)
      VALUES (v_snapshot, (col ->> 'ordinal_position')::integer, col ->> 'column_name', col ->> 'formatted_type',
          col ->> 'udt_name', (col ->> 'is_nullable')::boolean, coalesce((col ->> 'is_primary_key')::boolean, false),
          coalesce((col ->> 'is_unique_key')::boolean, false), coalesce((col ->> 'is_identity')::boolean, false),
          coalesce((col ->> 'is_generated')::boolean, false), col ->> 'default_expression', col ->> 'column_comment');
      v_columns := v_columns + 1;
    END LOOP;
    v_objects := v_objects + 1;
  END LOOP;
  UPDATE meta.metadata_capture mc
     SET status = 'SUCCESS', finished_at = clock_timestamp(),
         objects_captured = v_objects, columns_captured = v_columns
   WHERE mc.metadata_capture_id = v_capture;
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_METADATA_CAPTURED', 'SOURCE_SYSTEM', p_source_system_id::text,
    'CREATE', 'SUCCESS', jsonb_build_object('metadata_capture_id', v_capture, 'objects', v_objects,
    'columns', v_columns, 'actor_user_id', v_actor));
  RETURN QUERY SELECT v_capture, v_objects, v_columns;
END;
$fn$;

REVOKE ALL ON meta.object_config_version, meta.object_config_field, meta.object_config_key_part,
  meta.object_config_validation, meta.object_config_current, meta.target_change_proposal FROM PUBLIC;
REVOKE ALL ON FUNCTION
  meta.object_config_guard(), meta.object_config_reject_change(),
  meta.object_config_definition(bigint), meta.object_config_hash(bigint), meta.source_is_legacy(bigint),
  app.integration_object_config_get(text, bigint),
  app.integration_object_config_save_draft(text, bigint, integer, jsonb),
  app.integration_object_config_record_validation(text, bigint, integer, boolean, jsonb),
  app.integration_object_config_publish(text, bigint, integer),
  app.integration_object_config_set_drift(text, bigint, bigint, boolean, jsonb),
  app.integration_target_proposal_create(text, bigint, text, text, text, text),
  app.integration_target_proposal_reject(text, bigint)
FROM PUBLIC;

-- Default privileges of meta/app (see 005): remove every grant on the new objects
-- except the owner's and report it, then grant only what is intended.
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT 'TABLE ' || c.oid::regclass::text AS obj, x.grantee
      FROM pg_class c, aclexplode(c.relacl) x
     WHERE c.oid IN ('meta.object_config_version'::regclass, 'meta.object_config_field'::regclass,
                     'meta.object_config_key_part'::regclass, 'meta.object_config_validation'::regclass,
                     'meta.object_config_current'::regclass, 'meta.target_change_proposal'::regclass)
       AND x.grantee <> c.relowner
    UNION
    SELECT 'SEQUENCE ' || c.oid::regclass::text, x.grantee
      FROM pg_class c JOIN pg_depend dep ON dep.objid = c.oid AND dep.deptype IN ('a', 'i'), aclexplode(c.relacl) x
     WHERE c.relkind = 'S'
       AND dep.refobjid IN ('meta.object_config_version'::regclass, 'meta.object_config_validation'::regclass,
                            'meta.target_change_proposal'::regclass)
       AND x.grantee <> c.relowner
    UNION
    SELECT 'FUNCTION ' || p.oid::regprocedure::text, x.grantee
      FROM pg_proc p, aclexplode(p.proacl) x
     WHERE p.oid IN ('meta.object_config_guard()'::regprocedure, 'meta.object_config_reject_change()'::regprocedure,
                     'meta.object_config_definition(bigint)'::regprocedure, 'meta.object_config_hash(bigint)'::regprocedure,
                     'meta.source_is_legacy(bigint)'::regprocedure,
                     'app.integration_object_config_get(text, bigint)'::regprocedure,
                     'app.integration_object_config_save_draft(text, bigint, integer, jsonb)'::regprocedure,
                     'app.integration_object_config_record_validation(text, bigint, integer, boolean, jsonb)'::regprocedure,
                     'app.integration_object_config_publish(text, bigint, integer)'::regprocedure,
                     'app.integration_object_config_set_drift(text, bigint, bigint, boolean, jsonb)'::regprocedure,
                     'app.integration_target_proposal_create(text, bigint, text, text, text, text)'::regprocedure,
                     'app.integration_target_proposal_reject(text, bigint)'::regprocedure)
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
  app.integration_object_config_get(text, bigint),
  app.integration_object_config_save_draft(text, bigint, integer, jsonb),
  app.integration_object_config_record_validation(text, bigint, integer, boolean, jsonb),
  app.integration_object_config_publish(text, bigint, integer),
  app.integration_object_config_set_drift(text, bigint, bigint, boolean, jsonb),
  app.integration_target_proposal_create(text, bigint, text, text, text, text),
  app.integration_target_proposal_reject(text, bigint)
TO dwh_app;
