-- I18 (step 4, plan section 9): STG and ODS tables are created and changed from the UI -- package 021
-- (decision of the user 17.11.2026: the whole path from a new source to a DM mart through the interface;
-- DDL proposals applied by a SQL package per table -- M5 of I05, O5 of I06-05 -- are no longer needed).
--
-- Publication of a mapping (LOAD line of 008) or of an ODS configuration (ODS line of 012) brings its target
-- table to the fields of the draft in the same transaction, then publishes it with the functions of 008/012:
--   app.object_table_publish(token, 'LOAD' | 'ODS', object, draft_revision, confirm)
--     - no table: CREATE TABLE from the fields (types from a fixed whitelist, NOT NULL and PRIMARY KEY on the key);
--     - new fields: ADD COLUMN (no confirmation);
--     - columns not in the draft (REMOVED), other type (RETYPED), other key (KEY_CHANGED): the plan lists them with
--       the ODS configurations (of an STG table) and the DM marts (of an ODS table) that use the columns; without
--       p_confirm nothing changes (NEEDS_CONFIRM). A retype that would change a value is refused before ALTER;
--     - a column written by another object stays (KEPT); a retype of it or a key change of a shared table is
--       refused (problems);
--     - open change proposals of the draft are rejected (they are not needed any more).
--   app.object_table_preview(token, 'LOAD' | 'ODS', object): the same plan without a change (DDL text, changes,
--     impact, problems).
-- Runs of the object (advisory locks of 010/012, active requests of 015) block the change; lock_timeout 10 s.
-- The text of the DDL is built by the database from the stored fields (names checked by 008/012, quoted with %I).
-- Only tables of schemas stg and ods owned by the owner of these functions are changed.
-- Functions of 008-020 are not replaced. New functions are object_table_* (no earlier check counts them).
-- Requires 008, 012, 015, 018. Run as the owner of schemas app, meta, stg, ods (dwh_admin):
--   psql -1 -v ON_ERROR_STOP=1 -f 021_object_table_ui_ddl.sql
DO $pre$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regclass('meta.object_ods_version') IS NULL OR to_regclass('etl.object_run_request') IS NULL
     OR to_regclass('meta.dm_mart_source') IS NULL THEN
    RAISE EXCEPTION 'STOP: 012, 015 and 018 must be applied first';
  END IF;
  IF to_regprocedure('app.object_table_publish(text, text, bigint, integer, boolean)') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 021 is already applied';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname IN ('stg', 'ods') AND n.nspowner <> current_user::regrole::oid) THEN
    RAISE EXCEPTION 'STOP: run as the owner of schemas stg and ods (current user %)', current_user;
  END IF;
END
$pre$;

-- ------------------------------------------------------------------ helpers (owner only)

-- PostgreSQL column type (as format_type prints it) of a normalized type of 008/012; NULL = cannot be created.
CREATE FUNCTION meta.object_table_pg_type(p_type text) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog AS $fn$
DECLARE
  m text[] := regexp_match(coalesce(p_type, ''), '^([A-Z]+)(?:\(([0-9]{1,4})(?:,([0-9]{1,4}))?\))?$');
BEGIN
  IF m IS NULL THEN RETURN NULL; END IF;
  IF m[2] IS NOT NULL THEN
    IF m[1] = 'TEXT' AND m[3] IS NULL AND m[2]::integer >= 1 THEN
      RETURN format('character varying(%s)', m[2]::integer);
    END IF;
    IF m[1] = 'NUMERIC' AND m[2]::integer BETWEEN 1 AND 1000 AND coalesce(m[3]::integer, 0) <= m[2]::integer THEN
      RETURN format('numeric(%s,%s)', m[2]::integer, coalesce(m[3]::integer, 0));
    END IF;
    RETURN NULL;
  END IF;
  RETURN CASE m[1]
    WHEN 'TEXT' THEN 'text' WHEN 'INTEGER' THEN 'integer' WHEN 'BIGINT' THEN 'bigint' WHEN 'NUMERIC' THEN 'numeric'
    WHEN 'FLOAT' THEN 'double precision' WHEN 'BOOLEAN' THEN 'boolean' WHEN 'DATE' THEN 'date'
    WHEN 'TIME' THEN 'time without time zone' WHEN 'TIMESTAMP' THEN 'timestamp without time zone'
    WHEN 'TIMESTAMPTZ' THEN 'timestamp with time zone' WHEN 'UUID' THEN 'uuid' WHEN 'JSON' THEN 'jsonb'
    WHEN 'BYTES' THEN 'bytea' END;
END;
$fn$;

-- Draft of an object in a line: version id, revision, target schema and table.
CREATE FUNCTION meta.object_table_draft(p_line text, p_source_object_id bigint)
RETURNS TABLE(version_id bigint, draft_revision integer, target_schema text, target_table text)
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  SELECT v.version_id, v.draft_revision, v.target_schema, v.target_table FROM meta.object_config_version v
   WHERE p_line = 'LOAD' AND v.source_object_id = p_source_object_id AND v.status = 'DRAFT'
  UNION ALL
  SELECT v.ods_version_id, v.draft_revision, 'ods', v.target_table FROM meta.object_ods_version v
   WHERE p_line = 'ODS' AND v.source_object_id = p_source_object_id AND v.status = 'DRAFT'
$fn$;

-- Fields of a version: column, position, type text of 008/012, key position (NULL = not in the key).
CREATE FUNCTION meta.object_table_fields(p_line text, p_version_id bigint)
RETURNS TABLE(column_name text, field_position integer, target_type text, key_position integer)
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  SELECT f.target_column, f.field_position, f.target_type, k.key_position FROM meta.object_config_field f
    LEFT JOIN meta.object_config_key_part k ON k.version_id = f.version_id AND k.target_column = f.target_column
   WHERE p_line = 'LOAD' AND f.version_id = p_version_id
  UNION ALL
  SELECT f.target_column, f.field_position, f.target_type, k.key_position FROM meta.object_ods_field f
    LEFT JOIN meta.object_ods_key_part k ON k.ods_version_id = f.ods_version_id AND k.target_column = f.target_column
   WHERE p_line = 'ODS' AND f.ods_version_id = p_version_id
$fn$;

-- Columns of the table written by the versions in force of other objects (or of the other line of the object).
CREATE FUNCTION meta.object_table_other_users(p_schema text, p_table text, p_line text, p_source_object_id bigint)
RETURNS TABLE(source_object_id bigint, line text, column_name text)
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  SELECT cc.source_object_id, 'LOAD', f.target_column FROM meta.object_config_current cc
    JOIN meta.object_config_version v ON v.version_id = cc.version_id
    JOIN meta.object_config_field f ON f.version_id = v.version_id
   WHERE v.target_schema = p_schema AND v.target_table = p_table
     AND NOT (cc.source_object_id = p_source_object_id AND p_line = 'LOAD')
  UNION ALL
  SELECT oc.source_object_id, 'ODS', f.target_column FROM meta.object_ods_current oc
    JOIN meta.object_ods_version v ON v.ods_version_id = oc.ods_version_id
    JOIN meta.object_ods_field f ON f.ods_version_id = v.ods_version_id
   WHERE p_schema = 'ods' AND v.target_table = p_table
     AND NOT (oc.source_object_id = p_source_object_id AND p_line = 'ODS')
$fn$;

-- What uses the columns of the table: ODS configurations in force reading them from an STG table, DM mart
-- versions (published and drafts of marts not deleted) reading them from an ODS table.
CREATE FUNCTION meta.object_table_impact(p_schema text, p_table text, p_columns text[]) RETURNS jsonb
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  SELECT jsonb_build_object(
    'ods_configs', coalesce((
      SELECT jsonb_agg(jsonb_build_object('source_object_id', x.source_object_id, 'version_no', x.version_no,
                                          'columns', x.cols) ORDER BY x.source_object_id)
        FROM (SELECT oc.source_object_id, v.version_no, array_agg(DISTINCT f.stg_column ORDER BY f.stg_column) AS cols
                FROM meta.object_ods_current oc
                JOIN meta.object_ods_version v ON v.ods_version_id = oc.ods_version_id
                JOIN meta.object_ods_field f ON f.ods_version_id = v.ods_version_id
               WHERE p_schema = 'stg' AND f.stg_column = ANY (p_columns)
                 AND meta.object_stg_table(oc.source_object_id) ->> 'table' = p_table
               GROUP BY oc.source_object_id, v.version_no) x), '[]'::jsonb),
    'marts', coalesce((
      SELECT jsonb_agg(jsonb_build_object('mart_code', x.mart_code, 'version_no', x.version_no, 'status', x.status,
                                          'columns', x.cols) ORDER BY x.mart_code, x.version_no)
        FROM (SELECT m.mart_code, v.version_no, v.status, array_agg(DISTINCT u.col ORDER BY u.col) AS cols
                FROM meta.dm_mart m
                JOIN meta.dm_mart_version v ON v.mart_id = m.mart_id AND v.status IN ('PUBLISHED', 'DRAFT')
                JOIN meta.dm_mart_source s ON s.version_id = v.version_id AND s.table_name = p_table
                JOIN LATERAL (
                  SELECT s.key_column AS col
                  UNION ALL SELECT fs.source_column FROM meta.dm_mart_field_source fs
                    JOIN meta.dm_mart_field fd ON fd.field_id = fs.field_id
                   WHERE fd.version_id = v.version_id AND fs.source_alias = s.alias
                  UNION ALL SELECT fl.column_name FROM meta.dm_mart_filter fl
                   WHERE fl.version_id = v.version_id AND fl.source_alias = s.alias
                  UNION ALL SELECT l.ref_column FROM meta.dm_mart_source l
                   WHERE l.version_id = v.version_id AND l.ref_alias = s.alias) u ON u.col = ANY (p_columns)
               WHERE p_schema = 'ods' AND m.deleted_at IS NULL
               GROUP BY m.mart_code, v.version_no, v.status) x), '[]'::jsonb))
$fn$;

-- What publication of the draft does to its target table (no change).
CREATE FUNCTION meta.object_table_plan(p_line text, p_source_object_id bigint) RETURNS jsonb
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, meta AS $fn$
DECLARE
  d record;
  v_rel oid;
  v_owner_ok boolean;
  v_problems text[] := ARRAY[]::text[];
  v_changes jsonb := '[]'::jsonb;
  v_stmts text[] := ARRAY[]::text[];
  v_cols text[];
  v_old_key text[];
  v_new_key text[];
  v_pk_name text;
  v_bad text;
  v_affected text[] := ARRAY[]::text[];
  v_confirm boolean := false;
  r record;
BEGIN
  IF p_line NOT IN ('LOAD', 'ODS') THEN RAISE EXCEPTION 'Unknown configuration line'; END IF;
  SELECT * INTO d FROM meta.object_table_draft(p_line, p_source_object_id);
  IF NOT FOUND THEN RAISE EXCEPTION 'Draft not found'; END IF;
  IF d.target_schema IS NULL OR d.target_table IS NULL THEN
    RETURN jsonb_build_object('target_schema', d.target_schema, 'target_table', d.target_table, 'table_exists', false,
      'statements', '[]'::jsonb, 'ddl_text', NULL, 'changes', '[]'::jsonb, 'needs_confirm', false,
      'impact', meta.object_table_impact('-', '-', ARRAY[]::text[]), 'problems', jsonb_build_array('Target table not selected'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM meta.object_table_fields(p_line, d.version_id)) THEN
    v_problems := v_problems || 'Configuration has no fields'::text;
  END IF;
  SELECT string_agg(f.column_name || ' (' || f.target_type || ')', ', ' ORDER BY f.field_position) INTO v_bad
    FROM meta.object_table_fields(p_line, d.version_id) f WHERE meta.object_table_pg_type(f.target_type) IS NULL;
  IF v_bad IS NOT NULL THEN v_problems := v_problems || ('No PostgreSQL column type for: ' || v_bad); END IF;
  SELECT string_agg(f.column_name, ', ') INTO v_bad FROM meta.object_table_fields(p_line, d.version_id) f
   WHERE f.column_name IN ('tableoid', 'xmin', 'cmin', 'xmax', 'cmax', 'ctid', 'oid');
  IF v_bad IS NOT NULL THEN v_problems := v_problems || ('Reserved column names: ' || v_bad); END IF;
  SELECT array_agg(f.column_name ORDER BY f.key_position) INTO v_new_key
    FROM meta.object_table_fields(p_line, d.version_id) f WHERE f.key_position IS NOT NULL;
  IF v_new_key IS NULL THEN v_problems := v_problems || 'Business key not selected'::text; END IF;

  SELECT c.oid, c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user) INTO v_rel, v_owner_ok
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = d.target_schema AND c.relname = d.target_table;
  IF v_rel IS NOT NULL AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.oid = v_rel AND c.relkind IN ('r', 'p')) THEN
    v_problems := v_problems || format('%s.%s is not a table', d.target_schema, d.target_table);
  ELSIF v_rel IS NOT NULL AND NOT v_owner_ok THEN
    v_problems := v_problems || format('Table %s.%s is not owned by DWH', d.target_schema, d.target_table);
  END IF;

  IF cardinality(v_problems) = 0 AND v_rel IS NULL THEN
    v_stmts := ARRAY[format(E'CREATE TABLE %I.%I (\n', d.target_schema, d.target_table)
      || (SELECT string_agg(format('  %I %s%s', f.column_name, meta.object_table_pg_type(f.target_type),
                                   CASE WHEN f.key_position IS NOT NULL THEN ' NOT NULL' ELSE '' END),
                            E',\n' ORDER BY f.field_position) FROM meta.object_table_fields(p_line, d.version_id) f)
      || format(E',\n  CONSTRAINT %I PRIMARY KEY (%s)\n)', left('pk_' || d.target_table, 63),
                (SELECT string_agg(format('%I', k), ', ' ORDER BY o) FROM unnest(v_new_key) WITH ORDINALITY u(k, o)))];
    v_changes := jsonb_build_array(jsonb_build_object('change', 'CREATE_TABLE'));
  ELSIF cardinality(v_problems) = 0 THEN
    -- Primary key now and wanted.
    SELECT con.conname, array_agg(a.attname::text ORDER BY u.o) INTO v_pk_name, v_old_key
      FROM pg_constraint con CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY u(attnum, o)
      JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = u.attnum
     WHERE con.conrelid = v_rel AND con.contype = 'p' GROUP BY con.conname;
    FOR r IN
      WITH have AS (SELECT a.attname::text AS name, format_type(a.atttypid, a.atttypmod) AS typ, a.attnotnull,
                           a.atthasdef FROM pg_attribute a WHERE a.attrelid = v_rel AND a.attnum > 0 AND NOT a.attisdropped),
      want AS (SELECT f.column_name AS name, meta.object_table_pg_type(f.target_type) AS typ, f.field_position
                 FROM meta.object_table_fields(p_line, d.version_id) f),
      other AS (SELECT DISTINCT o.column_name FROM meta.object_table_other_users(d.target_schema, d.target_table, p_line,
                                                                                 p_source_object_id) o)
      SELECT coalesce(w.name, h.name) AS name, h.typ AS old_type, w.typ AS new_type, h.attnotnull, h.atthasdef,
             w.field_position, (o.column_name IS NOT NULL) AS used_by_other,
             CASE WHEN h.name IS NULL THEN 'ADDED' WHEN w.name IS NULL THEN 'REMOVED'
                  WHEN w.typ <> h.typ THEN 'RETYPED' END AS change
        FROM want w FULL JOIN have h ON h.name = w.name LEFT JOIN other o ON o.column_name = coalesce(w.name, h.name)
       WHERE h.name IS NULL OR w.name IS NULL OR w.typ <> h.typ
       ORDER BY w.field_position NULLS LAST, coalesce(w.name, h.name)
    LOOP
      IF r.change = 'REMOVED' AND r.used_by_other THEN
        v_changes := v_changes || jsonb_build_object('change', 'KEPT', 'column', r.name, 'old_type', r.old_type);
        CONTINUE;
      END IF;
      IF r.change = 'RETYPED' AND r.used_by_other THEN
        v_problems := v_problems || format('Column %s is written by another object: its type cannot change here', r.name);
        CONTINUE;
      END IF;
      v_changes := v_changes || jsonb_build_object('change', r.change, 'column', r.name, 'old_type', r.old_type,
                                                   'new_type', r.new_type);
      IF r.change = 'ADDED' THEN
        v_stmts := v_stmts || format('ALTER TABLE %I.%I ADD COLUMN %I %s', d.target_schema, d.target_table, r.name, r.new_type);
      ELSIF r.change = 'REMOVED' THEN
        v_stmts := v_stmts || format('ALTER TABLE %I.%I DROP COLUMN %I', d.target_schema, d.target_table, r.name);
        v_affected := v_affected || r.name;
        v_confirm := true;
      ELSE
        v_stmts := v_stmts || format('ALTER TABLE %I.%I ALTER COLUMN %I TYPE %s USING %I::%s', d.target_schema,
                                     d.target_table, r.name, r.new_type, r.name, r.new_type);
        v_affected := v_affected || r.name;
        v_confirm := true;
      END IF;
    END LOOP;
    -- Required columns that stay but are not written by this version (another object writes them: kept).
    SELECT string_agg(a.attname::text, ', ') INTO v_bad FROM pg_attribute a
     WHERE a.attrelid = v_rel AND a.attnum > 0 AND NOT a.attisdropped AND a.attnotnull AND NOT a.atthasdef
       AND a.attname::text <> ALL (coalesce(v_old_key, ARRAY[]::text[]))
       AND a.attname::text IN (SELECT o.column_name FROM meta.object_table_other_users(d.target_schema, d.target_table,
                                                                                         p_line, p_source_object_id) o)
       AND a.attname::text NOT IN (SELECT f.column_name FROM meta.object_table_fields(p_line, d.version_id) f);
    IF v_bad IS NOT NULL THEN
      v_problems := v_problems || ('Required columns written by another object are not in this version: ' || v_bad);
    END IF;
    IF v_old_key IS DISTINCT FROM v_new_key THEN
      IF EXISTS (SELECT 1 FROM meta.object_table_other_users(d.target_schema, d.target_table, p_line, p_source_object_id)) THEN
        v_problems := v_problems || 'The table is written by another object: its key cannot change here'::text;
      ELSE
        v_changes := v_changes || jsonb_build_object('change', 'KEY_CHANGED', 'old_key', to_jsonb(v_old_key),
                                                     'new_key', to_jsonb(v_new_key));
        v_confirm := true;
        IF v_pk_name IS NOT NULL THEN
          -- Dropped before the columns change (a removed column may be a key column).
          v_stmts := format('ALTER TABLE %I.%I DROP CONSTRAINT %I', d.target_schema, d.target_table, v_pk_name) || v_stmts;
        END IF;
        FOR r IN SELECT k FROM unnest(coalesce(v_old_key, ARRAY[]::text[])) k
                  WHERE k <> ALL (v_new_key)
                    AND k IN (SELECT f.column_name FROM meta.object_table_fields(p_line, d.version_id) f) LOOP
          v_stmts := v_stmts || format('ALTER TABLE %I.%I ALTER COLUMN %I DROP NOT NULL', d.target_schema, d.target_table, r.k);
        END LOOP;
        v_stmts := v_stmts || format('ALTER TABLE %I.%I ADD CONSTRAINT %I PRIMARY KEY (%s)', d.target_schema,
          d.target_table, left('pk_' || d.target_table, 63),
          (SELECT string_agg(format('%I', k), ', ' ORDER BY o) FROM unnest(v_new_key) WITH ORDINALITY u(k, o)));
      END IF;
    END IF;
  END IF;
  SELECT coalesce(array_agg(x), ARRAY[]::text[]) INTO v_cols FROM unnest(v_affected) x;
  RETURN jsonb_build_object(
    'target_schema', d.target_schema, 'target_table', d.target_table, 'table_exists', v_rel IS NOT NULL,
    'statements', to_jsonb(v_stmts),
    'ddl_text', CASE WHEN cardinality(v_stmts) > 0 THEN array_to_string(v_stmts, E';\n') || E';\n' END,
    'changes', v_changes, 'needs_confirm', v_confirm,
    'impact', meta.object_table_impact(d.target_schema, d.target_table, v_cols),
    'problems', to_jsonb(v_problems));
END;
$fn$;

-- A retype must not change a value (shorter text, fewer decimals, other text form of a value): the number of rows
-- whose value changes; values that cannot be converted fail the ALTER itself.
CREATE FUNCTION meta.object_table_retype_losses(p_schema text, p_table text, p_column text, p_old text, p_new text)
RETURNS bigint
LANGUAGE plpgsql SET search_path = pg_catalog AS $fn$
DECLARE
  v_old_text boolean := p_old = 'text' OR p_old LIKE 'character%';
  v_new_text boolean := p_new = 'text' OR p_new LIKE 'character%';
  v_count bigint;
BEGIN
  IF p_new LIKE 'numeric(%' THEN
    IF NOT v_old_text AND p_old NOT IN ('integer', 'bigint', 'smallint', 'numeric', 'real', 'double precision')
       AND p_old NOT LIKE 'numeric(%' THEN
      RETURN 0;  -- not a number: the ALTER fails by itself
    END IF;
    EXECUTE format('SELECT count(*) FROM %I.%I WHERE %I IS NOT NULL AND (%I::numeric)::%s <> %I::numeric',
                   p_schema, p_table, p_column, p_column, p_new, p_column) INTO v_count;
  ELSIF NOT v_old_text OR v_new_text THEN
    EXECUTE format('SELECT count(*) FROM %I.%I WHERE %I IS NOT NULL AND (%I::%s)::%s IS DISTINCT FROM %I',
                   p_schema, p_table, p_column, p_column, p_new, p_old, p_column) INTO v_count;
  ELSE
    v_count := 0;  -- text parsed into another type: invalid values fail the ALTER
  END IF;
  RETURN v_count;
END;
$fn$;

-- ------------------------------------------------------------------ UI (dwh_app)

CREATE FUNCTION app.object_table_preview(p_token_hash text, p_line text, p_source_object_id bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
BEGIN
  PERFORM app.integration_admin_session_user(p_token_hash);
  RETURN meta.object_table_plan(p_line, p_source_object_id);
END;
$fn$;

CREATE FUNCTION app.object_table_publish(p_token_hash text, p_line text, p_source_object_id bigint,
  p_draft_revision integer, p_confirm boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, etl, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  d record;
  p jsonb;
  c jsonb;
  s text;
  v_losses bigint;
  v_version_no integer;
  v_rejected integer := 0;
BEGIN
  IF p_line NOT IN ('LOAD', 'ODS') THEN RAISE EXCEPTION 'Unknown configuration line'; END IF;
  IF p_line = 'LOAD' THEN
    PERFORM 1 FROM meta.object_config_version x
     WHERE x.source_object_id = p_source_object_id AND x.status = 'DRAFT' FOR UPDATE;
  ELSE
    PERFORM 1 FROM meta.object_ods_version x
     WHERE x.source_object_id = p_source_object_id AND x.status = 'DRAFT' FOR UPDATE;
  END IF;
  SELECT * INTO d FROM meta.object_table_draft(p_line, p_source_object_id);
  IF NOT FOUND OR d.draft_revision IS DISTINCT FROM p_draft_revision THEN
    RAISE EXCEPTION 'Draft not found or changed by another user';
  END IF;
  -- No run of the object while its tables change (held until the end of this transaction).
  IF NOT pg_try_advisory_xact_lock(hashtext('dwh.object_run'), p_source_object_id::integer)
     OR NOT pg_try_advisory_xact_lock(hashtext('dwh.object_ods_run'), p_source_object_id::integer)
     OR EXISTS (SELECT 1 FROM etl.object_run_request r WHERE r.source_object_id = p_source_object_id
                 AND r.status IN ('CLAIMED', 'RUNNING')) THEN
    RAISE EXCEPTION 'The object is being loaded: wait for the run to finish';
  END IF;
  p := meta.object_table_plan(p_line, p_source_object_id);
  IF jsonb_array_length(p->'problems') > 0 THEN
    RAISE EXCEPTION 'Target table cannot be changed: %',
      (SELECT string_agg(x, '; ') FROM jsonb_array_elements_text(p->'problems') x);
  END IF;
  IF (p->>'needs_confirm')::boolean AND NOT coalesce(p_confirm, false) THEN
    RETURN p || jsonb_build_object('status', 'NEEDS_CONFIRM');
  END IF;
  IF jsonb_array_length(p->'statements') > 0 THEN
    PERFORM set_config('lock_timeout', '10s', true);
    FOR c IN SELECT x FROM jsonb_array_elements(p->'changes') x WHERE x->>'change' = 'RETYPED' LOOP
      v_losses := meta.object_table_retype_losses(d.target_schema, d.target_table, c->>'column', c->>'old_type',
                                                  c->>'new_type');
      IF v_losses > 0 THEN
        RAISE EXCEPTION 'Column %: % rows would change when converted to %', c->>'column', v_losses, c->>'new_type';
      END IF;
    END LOOP;
    FOR s IN SELECT x FROM jsonb_array_elements_text(p->'statements') x LOOP
      EXECUTE s;
    END LOOP;
    PERFORM audit.log_security_event('INTEGRATION_OBJECT_TABLE_CHANGED', 'SOURCE_OBJECT', p_source_object_id::text,
      'UPDATE', 'SUCCESS', jsonb_build_object('actor_user_id', v_actor, 'line', p_line, 'version_id', d.version_id,
      'target', d.target_schema || '.' || d.target_table, 'changes', p->'changes', 'impact', p->'impact',
      'ddl_sha256', encode(sha256(convert_to(p->>'ddl_text', 'UTF8')), 'hex'), 'confirmed', coalesce(p_confirm, false)));
  END IF;
  -- Change proposals of the draft are not needed any more.
  UPDATE meta.target_change_proposal x SET status = 'REJECTED', updated_at = clock_timestamp()
   WHERE x.status = 'PROPOSED'
     AND ((p_line = 'LOAD' AND x.version_id = d.version_id) OR (p_line = 'ODS' AND x.ods_version_id = d.version_id));
  GET DIAGNOSTICS v_rejected = ROW_COUNT;
  IF p_line = 'LOAD' THEN
    v_version_no := app.integration_object_config_publish(p_token_hash, p_source_object_id, p_draft_revision);
  ELSE
    v_version_no := app.integration_ods_config_publish(p_token_hash, p_source_object_id, p_draft_revision);
  END IF;
  RETURN p || jsonb_build_object('status', 'PUBLISHED', 'version_no', v_version_no, 'proposals_rejected', v_rejected);
END;
$fn$;

-- ------------------------------------------------------------------ privileges

REVOKE ALL ON FUNCTION
  meta.object_table_pg_type(text), meta.object_table_draft(text, bigint), meta.object_table_fields(text, bigint),
  meta.object_table_other_users(text, text, text, bigint), meta.object_table_impact(text, text, text[]),
  meta.object_table_plan(text, bigint), meta.object_table_retype_losses(text, text, text, text, text),
  app.object_table_preview(text, text, bigint), app.object_table_publish(text, text, bigint, integer, boolean)
FROM PUBLIC;

-- Default privileges of meta/app (see 005): remove every grant on the new objects except the owner's.
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT 'FUNCTION ' || p.oid::regprocedure::text AS obj, x.grantee
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace, aclexplode(p.proacl) x
     WHERE n.nspname IN ('meta', 'app') AND p.proname LIKE 'object\_table\_%' AND x.grantee <> p.proowner
  LOOP
    EXECUTE format('REVOKE ALL ON %s FROM %s', r.obj,
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED % FROM %', r.obj,
                 CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(r.grantee) END;
  END LOOP;
END
$acl$;

GRANT EXECUTE ON FUNCTION
  app.object_table_preview(text, text, bigint), app.object_table_publish(text, text, bigint, integer, boolean)
TO dwh_app;

SELECT 'MIGRATION_021_DONE' AS result;
