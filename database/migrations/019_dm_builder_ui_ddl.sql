-- I19 (step 7): DM marts fully managed from the UI -- package 019
-- (plan docs/DWH_I19_DM_BUILDER_PLAN_APPROVED_20261116.md, section 13; decision of the user 17.11.2026:
-- creating, changing and deleting a mart needs no SQL package; D9 changed, D13 added).
--
-- Publication builds aside and swaps (no moment without data for readers):
--   app.mart_builder_publish(token, version, confirm)
--     - the mart table already has the fields of the version  -> published at once, a refresh is queued;
--     - otherwise: removed / retyped columns are compared with the current table and the saved reports
--       (017), AI examples and reference columns (016) that use them are listed; without p_confirm nothing
--       changes (NEEDS_CONFIRM). With it: dm.<code>__next is created from the fields (text built by the
--       database, whitelisted types only), a PUBLISH request is queued, the version is BUILDING;
--   the dispatcher fills dm.<code>__next (dwh_etl), then etl.dm_mart_run_finish swaps in one transaction:
--     DROP dm.<code>, RENAME dm.<code>__next -> dm.<code>, publish the version. On failure dm.<code>__next is
--     dropped, the published version and its table stay, the draft is FAILED with the reason.
--   app.mart_builder_delete(token, mart, confirm): lists the saved reports / examples using the mart, then
--     drops its tables, deactivates the mart and its AI rows (code stays reserved, history is kept).
--   app.mart_builder_preview(token, version): DDL text, changes and the impact before publication.
-- Only tables of marts of meta.dm_mart are created or dropped (codes are checked against dm at creation).
-- New functions are named mart_builder_* (check 018 counts dm_*); etl.dm_mart_run_request_claim,
-- etl.dm_mart_run_begin, etl.dm_mart_run_finish and etl.v_dm_mart_status are replaced (same signatures,
-- grants kept; the rollback restores the 018 text). Requires 017 and 018. Run as the owner (dwh_admin):
--   psql -1 -v ON_ERROR_STOP=1 -f 019_dm_builder_ui_ddl.sql
DO $pre$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regclass('meta.dm_mart') IS NULL THEN RAISE EXCEPTION 'STOP: 018 must be applied first'; END IF;
  IF to_regclass('app.saved_report') IS NULL THEN RAISE EXCEPTION 'STOP: 017 must be applied first'; END IF;
  IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'meta.dm_mart'::regclass AND attname = 'deleted_at'
              AND NOT attisdropped) THEN
    RAISE EXCEPTION 'STOP: 019 is already applied';
  END IF;
END
$pre$;

ALTER TABLE meta.dm_mart ADD COLUMN deleted_at timestamptz, ADD COLUMN deleted_by_user_id bigint;
ALTER TABLE meta.dm_mart_version
  ADD COLUMN build_status text NOT NULL DEFAULT 'NONE' CHECK (build_status IN ('NONE', 'BUILDING', 'FAILED')),
  ADD COLUMN build_message text CHECK (char_length(build_message) <= 4000),
  ADD COLUMN build_request_id bigint;
ALTER TABLE etl.dm_mart_run_request ADD COLUMN version_id bigint REFERENCES meta.dm_mart_version(version_id) ON DELETE RESTRICT;
ALTER TABLE etl.dm_mart_run_request DROP CONSTRAINT dm_mart_run_request_trigger_kind_check;
ALTER TABLE etl.dm_mart_run_request ADD CONSTRAINT dm_mart_run_request_trigger_kind_check
  CHECK (trigger_kind IN ('MANUAL', 'AFTER_ODS', 'PUBLISH'));
ALTER TABLE etl.dm_mart_run_request DROP CONSTRAINT dm_mart_run_request_trigger;
ALTER TABLE etl.dm_mart_run_request ADD CONSTRAINT dm_mart_run_request_trigger CHECK (
  (trigger_kind IN ('MANUAL', 'PUBLISH')) = (requested_by_user_id IS NOT NULL)
  AND (trigger_kind = 'AFTER_ODS') = (trigger_table IS NOT NULL)
  AND (trigger_kind = 'PUBLISH') = (version_id IS NOT NULL));

-- ------------------------------------------------------------------ helpers (owner only)

-- A deleted mart cannot be changed or get new versions (its code stays reserved).
CREATE FUNCTION meta.mart_builder_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, meta AS $fn$
BEGIN
  IF TG_TABLE_NAME = 'dm_mart' THEN
    IF OLD.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'Mart is deleted'; END IF;
    RETURN NEW;
  END IF;
  IF EXISTS (SELECT 1 FROM meta.dm_mart m WHERE m.mart_id = NEW.mart_id AND m.deleted_at IS NOT NULL) THEN
    RAISE EXCEPTION 'Mart is deleted';
  END IF;
  RETURN NEW;
END;
$fn$;
CREATE TRIGGER dm_mart_deleted_guard BEFORE UPDATE ON meta.dm_mart
  FOR EACH ROW EXECUTE FUNCTION meta.mart_builder_guard();
CREATE TRIGGER dm_mart_version_deleted_guard BEFORE INSERT ON meta.dm_mart_version
  FOR EACH ROW EXECUTE FUNCTION meta.mart_builder_guard();

-- The definition of a version being built cannot change (sources, fields, filters).
CREATE FUNCTION meta.mart_builder_building_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, meta AS $fn$
DECLARE
  v_version bigint;
BEGIN
  IF TG_TABLE_NAME = 'dm_mart_field_source' THEN
    SELECT f.version_id INTO v_version FROM meta.dm_mart_field f
     WHERE f.field_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.field_id ELSE NEW.field_id END;
  ELSE
    v_version := CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
  END IF;
  IF EXISTS (SELECT 1 FROM meta.dm_mart_version v WHERE v.version_id = v_version AND v.build_status = 'BUILDING') THEN
    RAISE EXCEPTION 'The version is being built and cannot be changed';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$fn$;
CREATE TRIGGER dm_mart_source_building_guard BEFORE INSERT OR DELETE ON meta.dm_mart_source
  FOR EACH ROW EXECUTE FUNCTION meta.mart_builder_building_guard();
CREATE TRIGGER dm_mart_field_building_guard BEFORE INSERT OR DELETE ON meta.dm_mart_field
  FOR EACH ROW EXECUTE FUNCTION meta.mart_builder_building_guard();
CREATE TRIGGER dm_mart_filter_building_guard BEFORE INSERT OR DELETE ON meta.dm_mart_filter
  FOR EACH ROW EXECUTE FUNCTION meta.mart_builder_building_guard();

-- CREATE TABLE dm.<table> for the fields of a version (types from the whitelist of 018).
CREATE FUNCTION meta.mart_builder_ddl(p_version_id bigint, p_table text) RETURNS text
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  SELECT format(E'CREATE TABLE dm.%I (\n', p_table)
      || (SELECT string_agg(format('  %I %s%s', f.field_name, f.data_type, CASE WHEN f.is_key THEN ' NOT NULL' ELSE '' END),
                            E',\n' ORDER BY f.position) FROM meta.dm_mart_field f WHERE f.version_id = p_version_id)
      || E',\n  dm_loaded_at timestamptz NOT NULL,\n'
      || format('  CONSTRAINT %I PRIMARY KEY (%I)', p_table || '_pk',
                (SELECT f.field_name FROM meta.dm_mart_field f WHERE f.version_id = p_version_id AND f.is_key))
      || E'\n);\n'
$fn$;

-- Columns of the current dm.<code> that the version removes or retypes.
CREATE FUNCTION meta.mart_builder_changes(p_version_id bigint)
RETURNS TABLE(column_name text, change text, old_type text, new_type text)
LANGUAGE sql STABLE SET search_path = pg_catalog, meta AS $fn$
  WITH v AS (SELECT m.mart_code FROM meta.dm_mart_version x JOIN meta.dm_mart m ON m.mart_id = x.mart_id
              WHERE x.version_id = p_version_id),
  have AS (SELECT a.attname::text AS name, format_type(a.atttypid, a.atttypmod) AS typ
             FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'dm' AND c.relname = (SELECT mart_code FROM v) AND c.relkind = 'r'
              AND a.attnum > 0 AND NOT a.attisdropped AND a.attname <> 'dm_loaded_at'),
  want AS (SELECT f.field_name AS name, meta.dm_type_canonical(f.data_type) AS typ FROM meta.dm_mart_field f
            WHERE f.version_id = p_version_id)
  SELECT h.name, CASE WHEN w.name IS NULL THEN 'REMOVED' ELSE 'RETYPED' END, h.typ, w.typ
    FROM have h LEFT JOIN want w ON w.name = h.name
   WHERE w.name IS NULL OR w.typ <> h.typ
   ORDER BY h.name
$fn$;

-- What uses the mart (p_columns NULL = the whole mart, otherwise these columns): saved reports of 017
-- (object_ids of the AI whitelist row; columns found in their SQL text), active AI examples and reference
-- columns of 016.
CREATE FUNCTION meta.mart_builder_impact(p_mart_id bigint, p_columns text[]) RETURNS jsonb
LANGUAGE sql STABLE SET search_path = pg_catalog, meta, app AS $fn$
  WITH o AS (SELECT a.object_id FROM meta.ai_report_object a JOIN meta.dm_mart m ON m.mart_code = a.object_name
              WHERE a.schema_name = 'dm' AND m.mart_id = p_mart_id)
  SELECT jsonb_build_object(
    'reports', coalesce((SELECT jsonb_agg(jsonb_build_object('report_id', r.report_id, 'title', r.title,
                   'folder_name', f.folder_name, 'visibility', f.visibility, 'owner', r.owner_username) ORDER BY r.report_id)
               FROM app.saved_report r JOIN app.report_folder f ON f.folder_id = r.folder_id
              WHERE r.is_active AND r.object_ids && ARRAY(SELECT object_id FROM o)
                AND (p_columns IS NULL OR EXISTS (SELECT 1 FROM unnest(p_columns) c WHERE r.sql_text ~* ('\m' || c || '\M')))),
             '[]'::jsonb),
    'examples', coalesce((SELECT jsonb_agg(jsonb_build_object('example_id', e.example_id, 'question', e.question)
                   ORDER BY e.example_id)
               FROM meta.ai_example e WHERE e.is_active AND e.object_id IN (SELECT object_id FROM o)
                AND (p_columns IS NULL OR EXISTS (SELECT 1 FROM unnest(p_columns) c WHERE e.sql_text ~* ('\m' || c || '\M')))),
             '[]'::jsonb),
    'value_columns', coalesce((SELECT jsonb_agg(jsonb_build_object('value_column_id', v.value_column_id,
                   'column_name', v.column_name) ORDER BY v.column_name)
               FROM meta.ai_value_column v WHERE v.is_active AND v.object_id IN (SELECT object_id FROM o)
                AND (p_columns IS NULL OR v.column_name = ANY (p_columns))),
             '[]'::jsonb))
$fn$;

CREATE FUNCTION meta.mart_builder_impact_empty(p jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $fn$
  SELECT jsonb_array_length(p->'reports') = 0 AND jsonb_array_length(p->'examples') = 0
     AND jsonb_array_length(p->'value_columns') = 0
$fn$;

-- Publication of a version whose table is in place (same rules as app.dm_mart_version_publish of 018).
CREATE FUNCTION meta.mart_builder_publish_now(p_version_id bigint, p_actor bigint) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, meta AS $fn$
DECLARE
  v meta.dm_mart_version;
BEGIN
  SELECT * INTO v FROM meta.dm_mart_version x WHERE x.version_id = p_version_id FOR UPDATE;
  UPDATE meta.dm_mart_version x SET status = 'SUPERSEDED' WHERE x.mart_id = v.mart_id AND x.status = 'PUBLISHED';
  UPDATE meta.dm_mart_version x SET status = 'PUBLISHED', published_at = clock_timestamp(), published_by_user_id = p_actor,
         build_status = 'NONE', build_message = NULL,
         ddl_status = CASE WHEN x.ddl_status = 'PROPOSED' THEN 'REJECTED' ELSE x.ddl_status END
   WHERE x.version_id = p_version_id;
  PERFORM meta.dm_mart_sync_ai(v.mart_id, p_actor);
END;
$fn$;

-- What publication of a version would do (no change).
CREATE FUNCTION meta.mart_builder_plan(p_version_id bigint) RETURNS jsonb
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, meta AS $fn$
DECLARE
  v meta.dm_mart_version;
  m meta.dm_mart;
  v_cols text[];
  v_changes jsonb;
BEGIN
  SELECT * INTO v FROM meta.dm_mart_version x WHERE x.version_id = p_version_id;
  SELECT * INTO m FROM meta.dm_mart x WHERE x.mart_id = v.mart_id;
  SELECT coalesce(jsonb_agg(to_jsonb(c)), '[]'::jsonb), coalesce(array_agg(c.column_name), ARRAY[]::text[])
    INTO v_changes, v_cols FROM meta.mart_builder_changes(p_version_id) c;
  RETURN jsonb_build_object(
    'needs_build', NOT meta.dm_mart_table_matches(p_version_id),
    'table_exists', to_regclass(format('dm.%I', m.mart_code)) IS NOT NULL,
    -- No key field yet (an empty draft): no DDL text, the problems say what is missing.
    'ddl_text', CASE WHEN EXISTS (SELECT 1 FROM meta.dm_mart_field f WHERE f.version_id = p_version_id AND f.is_key)
                     THEN meta.mart_builder_ddl(p_version_id, m.mart_code) END,
    'changes', v_changes,
    'impact', CASE WHEN cardinality(v_cols) = 0 THEN jsonb_build_object('reports', '[]'::jsonb, 'examples', '[]'::jsonb,
                   'value_columns', '[]'::jsonb) ELSE meta.mart_builder_impact(m.mart_id, v_cols) END,
    'problems', to_jsonb(meta.dm_mart_version_problems(p_version_id)));
END;
$fn$;

-- ------------------------------------------------------------------ UI (dwh_app)

CREATE FUNCTION app.mart_builder_preview(p_token_hash text, p_version_id bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
BEGIN
  PERFORM app.integration_admin_session_user(p_token_hash);
  IF NOT EXISTS (SELECT 1 FROM meta.dm_mart_version x WHERE x.version_id = p_version_id) THEN
    RAISE EXCEPTION 'Mart version not found';
  END IF;
  RETURN meta.mart_builder_plan(p_version_id);
END;
$fn$;

CREATE FUNCTION app.mart_builder_publish(p_token_hash text, p_version_id bigint, p_confirm boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, etl, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  v meta.dm_mart_version;
  m meta.dm_mart;
  p jsonb;
  v_next text;
  v_ddl text;
  v_request bigint;
BEGIN
  SELECT * INTO v FROM meta.dm_mart_version x WHERE x.version_id = p_version_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Mart version not found'; END IF;
  SELECT * INTO m FROM meta.dm_mart x WHERE x.mart_id = v.mart_id FOR UPDATE;
  IF m.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'Mart is deleted'; END IF;
  IF v.status <> 'DRAFT' THEN RAISE EXCEPTION 'Only a DRAFT version can be published'; END IF;
  IF v.build_status = 'BUILDING' THEN RAISE EXCEPTION 'The version is already being built'; END IF;
  IF EXISTS (SELECT 1 FROM etl.dm_mart_run_request r WHERE r.mart_id = m.mart_id AND r.status IN ('CLAIMED', 'RUNNING')) THEN
    RAISE EXCEPTION 'Mart already has an active run request: wait for it to finish';
  END IF;
  p := meta.mart_builder_plan(p_version_id);
  IF jsonb_array_length(p->'problems') > 0 THEN
    RAISE EXCEPTION 'Version is not valid: %', (SELECT string_agg(x, '; ') FROM jsonb_array_elements_text(p->'problems') x);
  END IF;
  IF NOT (p->>'needs_build')::boolean THEN
    PERFORM meta.mart_builder_publish_now(p_version_id, v_actor);
    INSERT INTO etl.dm_mart_run_request (mart_id, trigger_kind, requested_by_user_id, requested_by)
    VALUES (m.mart_id, 'MANUAL', v_actor, meta.dm_actor_username(v_actor))
    ON CONFLICT (mart_id) WHERE status = 'QUEUED' DO NOTHING
    RETURNING request_id INTO v_request;
    PERFORM audit.log_security_event('DM_MART_VERSION_PUBLISHED', 'DM_MART', m.mart_id::text, 'PUBLISH', 'SUCCESS',
      jsonb_build_object('actor_user_id', v_actor, 'version_id', p_version_id, 'version_no', v.version_no, 'build', false));
    RETURN p || jsonb_build_object('status', 'PUBLISHED', 'request_id', v_request);
  END IF;
  IF NOT meta.mart_builder_impact_empty(p->'impact') AND NOT coalesce(p_confirm, false) THEN
    RETURN p || jsonb_build_object('status', 'NEEDS_CONFIRM');
  END IF;
  -- Queued refreshes of the old structure are replaced by the build.
  UPDATE etl.dm_mart_run_request r SET status = 'CANCELLED', finished_at = clock_timestamp(), result_code = 'CANCELLED',
         message = 'Replaced by the publication of a new version'
   WHERE r.mart_id = m.mart_id AND r.status = 'QUEUED';
  v_next := m.mart_code || '__next';
  EXECUTE format('DROP TABLE IF EXISTS dm.%I', v_next);
  v_ddl := meta.mart_builder_ddl(p_version_id, v_next);
  EXECUTE v_ddl;
  -- Reference columns of the AI assistant on removed columns would break its prompts.
  UPDATE meta.ai_value_column c SET is_active = false, updated_at = clock_timestamp(), updated_by_user_id = v_actor
   WHERE c.value_column_id IN (SELECT (x->>'value_column_id')::bigint FROM jsonb_array_elements(p->'impact'->'value_columns') x);
  INSERT INTO etl.dm_mart_run_request (mart_id, trigger_kind, requested_by_user_id, requested_by, version_id)
  VALUES (m.mart_id, 'PUBLISH', v_actor, meta.dm_actor_username(v_actor), p_version_id)
  RETURNING request_id INTO v_request;
  UPDATE meta.dm_mart_version x SET build_status = 'BUILDING', build_message = NULL, build_request_id = v_request,
         ddl_text = meta.mart_builder_ddl(p_version_id, m.mart_code), updated_at = clock_timestamp(),
         ddl_sha256 = encode(sha256(convert_to(meta.mart_builder_ddl(p_version_id, m.mart_code), 'UTF8')), 'hex'),
         ddl_status = 'PROPOSED'
   WHERE x.version_id = p_version_id;
  PERFORM audit.log_security_event('DM_MART_BUILD_STARTED', 'DM_MART', m.mart_id::text, 'PUBLISH', 'SUCCESS',
    jsonb_build_object('actor_user_id', v_actor, 'version_id', p_version_id, 'request_id', v_request,
                       'changes', p->'changes', 'impact', p->'impact', 'confirmed', coalesce(p_confirm, false)));
  RETURN p || jsonb_build_object('status', 'BUILDING', 'request_id', v_request);
END;
$fn$;

CREATE FUNCTION app.mart_builder_delete(p_token_hash text, p_mart_id bigint, p_confirm boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, etl, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  m meta.dm_mart;
  p jsonb;
BEGIN
  SELECT * INTO m FROM meta.dm_mart x WHERE x.mart_id = p_mart_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Mart not found'; END IF;
  IF m.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'Mart is deleted'; END IF;
  IF EXISTS (SELECT 1 FROM etl.dm_mart_run_request r WHERE r.mart_id = m.mart_id AND r.status IN ('CLAIMED', 'RUNNING')) THEN
    RAISE EXCEPTION 'Mart already has an active run request: wait for it to finish';
  END IF;
  p := meta.mart_builder_impact(m.mart_id, NULL);
  IF NOT meta.mart_builder_impact_empty(p) AND NOT coalesce(p_confirm, false) THEN
    RETURN jsonb_build_object('status', 'NEEDS_CONFIRM', 'impact', p);
  END IF;
  UPDATE etl.dm_mart_run_request r SET status = 'CANCELLED', finished_at = clock_timestamp(), result_code = 'CANCELLED',
         message = 'Mart deleted'
   WHERE r.mart_id = m.mart_id AND r.status = 'QUEUED';
  EXECUTE format('DROP TABLE IF EXISTS dm.%I', m.mart_code || '__next');
  EXECUTE format('DROP TABLE IF EXISTS dm.%I', m.mart_code);
  UPDATE meta.ai_value_column c SET is_active = false, updated_at = clock_timestamp(), updated_by_user_id = v_actor
   WHERE c.value_column_id IN (SELECT (x->>'value_column_id')::bigint FROM jsonb_array_elements(p->'value_columns') x);
  UPDATE meta.ai_example e SET is_active = false, updated_at = clock_timestamp(), updated_by_user_id = v_actor
   WHERE e.example_id IN (SELECT (x->>'example_id')::bigint FROM jsonb_array_elements(p->'examples') x);
  UPDATE meta.dm_mart_version x SET build_status = 'NONE', build_message = NULL
   WHERE x.mart_id = m.mart_id AND x.build_status <> 'NONE';
  UPDATE meta.dm_mart x SET is_active = false, deleted_at = clock_timestamp(), deleted_by_user_id = v_actor,
         updated_at = clock_timestamp(), updated_by_user_id = v_actor
   WHERE x.mart_id = m.mart_id;
  PERFORM meta.dm_mart_sync_ai(m.mart_id, v_actor);
  PERFORM audit.log_security_event('DM_MART_DELETED', 'DM_MART', m.mart_id::text, 'DELETE', 'SUCCESS',
    jsonb_build_object('actor_user_id', v_actor, 'mart_code', m.mart_code, 'impact', p, 'confirmed', coalesce(p_confirm, false)));
  RETURN jsonb_build_object('status', 'DELETED', 'impact', p);
END;
$fn$;

-- ------------------------------------------------------------------ engine (replaced 018 functions)

-- As in 018, plus: a PUBLISH request runs its draft version (being built); a lost PUBLISH request drops the
-- table being built and marks the draft FAILED.
CREATE OR REPLACE FUNCTION etl.dm_mart_run_request_claim(p_worker_name text)
RETURNS TABLE(request_id bigint, mart_id bigint, version_id bigint, trigger_kind text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl, meta AS $fn$
DECLARE
  v_worker text := btrim(coalesce(p_worker_name, ''));
  v_key integer := hashtext('dwh.dm_mart_run_request');
  a record;
  c etl.dm_mart_run_request;
  v_version bigint;
BEGIN
  IF v_worker = '' OR char_length(v_worker) > 255 THEN RAISE EXCEPTION 'Worker name is required'; END IF;
  FOR a IN SELECT x.request_id, x.status, x.claimed_by, x.trigger_kind, x.version_id, x.mart_id FROM etl.dm_mart_run_request x
            WHERE x.status IN ('CLAIMED', 'RUNNING') ORDER BY x.request_id LOOP
    CONTINUE WHEN EXISTS (SELECT 1 FROM pg_locks l WHERE l.locktype = 'advisory' AND l.pid = pg_backend_pid()
                           AND l.classid = v_key::oid AND l.objid = a.request_id::integer::oid AND l.objsubid = 2);
    IF pg_try_advisory_lock(v_key, a.request_id::integer) THEN
      UPDATE etl.dm_mart_run x SET status = 'FAILED', finished_at = clock_timestamp(), error_code = 'DISPATCHER_LOST',
             error_message = 'Dispatcher session ended during the run'
       WHERE x.request_id = a.request_id AND x.status = 'RUNNING';
      UPDATE etl.dm_mart_run_request x
         SET status = 'FAILED', finished_at = clock_timestamp(), result_code = 'DISPATCHER_LOST',
             run_id = (SELECT r.run_id FROM etl.dm_mart_run r WHERE r.request_id = a.request_id),
             message = left('Recovered abandoned ' || a.status || ' request: dispatcher session ended; previous worker='
                            || coalesce(a.claimed_by, '<unknown>') || '; recovered by=' || v_worker, 4000)
       WHERE x.request_id = a.request_id AND x.status IN ('CLAIMED', 'RUNNING');
      IF a.trigger_kind = 'PUBLISH' THEN
        PERFORM meta.mart_builder_build_failed(a.version_id, 'Dispatcher session ended during the build');
      END IF;
      PERFORM pg_advisory_unlock(v_key, a.request_id::integer);
    END IF;
  END LOOP;
  SELECT * INTO c FROM etl.dm_mart_run_request x
   WHERE x.status = 'QUEUED'
     AND NOT EXISTS (SELECT 1 FROM etl.dm_mart_run_request y WHERE y.mart_id = x.mart_id AND y.status IN ('CLAIMED', 'RUNNING'))
   ORDER BY x.requested_at, x.request_id FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN; END IF;
  IF c.trigger_kind = 'PUBLISH' THEN
    SELECT v.version_id INTO v_version FROM meta.dm_mart_version v JOIN meta.dm_mart m ON m.mart_id = v.mart_id
     WHERE v.version_id = c.version_id AND v.status = 'DRAFT' AND v.build_status = 'BUILDING' AND m.deleted_at IS NULL;
  ELSE
    SELECT v.version_id INTO v_version FROM meta.dm_mart_version v JOIN meta.dm_mart m ON m.mart_id = v.mart_id
     WHERE v.mart_id = c.mart_id AND v.status = 'PUBLISHED' AND m.is_active;
  END IF;
  IF v_version IS NULL THEN
    UPDATE etl.dm_mart_run_request x SET status = 'FAILED', finished_at = clock_timestamp(), result_code = 'MART_NOT_RUNNABLE',
           message = 'Mart is inactive or has no published version'
     WHERE x.request_id = c.request_id;
    RETURN;
  END IF;
  IF NOT pg_try_advisory_lock(v_key, c.request_id::integer) THEN
    RAISE EXCEPTION 'Queued run request lock is unexpectedly held';
  END IF;
  UPDATE etl.dm_mart_run_request x SET status = 'CLAIMED', claimed_by = v_worker, claimed_at = clock_timestamp()
   WHERE x.request_id = c.request_id AND x.status = 'QUEUED';
  RETURN QUERY SELECT c.request_id, c.mart_id, v_version, c.trigger_kind;
END;
$fn$;

CREATE OR REPLACE FUNCTION etl.dm_mart_run_begin(p_request_id bigint, p_worker_name text, p_version_id bigint)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl, meta AS $fn$
DECLARE
  r etl.dm_mart_run_request;
  v_run bigint;
BEGIN
  SELECT * INTO r FROM etl.dm_mart_run_request x WHERE x.request_id = p_request_id FOR UPDATE;
  IF NOT FOUND OR r.status <> 'CLAIMED' OR r.claimed_by IS DISTINCT FROM btrim(coalesce(p_worker_name, '')) THEN
    RAISE EXCEPTION 'Run request cannot be started';
  END IF;
  IF r.trigger_kind = 'PUBLISH' THEN
    IF p_version_id IS DISTINCT FROM r.version_id OR NOT EXISTS (SELECT 1 FROM meta.dm_mart_version v
         WHERE v.version_id = p_version_id AND v.status = 'DRAFT' AND v.build_status = 'BUILDING') THEN
      RAISE EXCEPTION 'Version is not the version being built';
    END IF;
  ELSIF NOT EXISTS (SELECT 1 FROM meta.dm_mart_version v WHERE v.version_id = p_version_id AND v.mart_id = r.mart_id
                     AND v.status = 'PUBLISHED') THEN
    RAISE EXCEPTION 'Version is not the published version of the mart';
  END IF;
  UPDATE etl.dm_mart_run_request x SET status = 'RUNNING', started_at = clock_timestamp() WHERE x.request_id = p_request_id;
  INSERT INTO etl.dm_mart_run (mart_id, version_id, request_id) VALUES (r.mart_id, p_version_id, p_request_id)
  RETURNING run_id INTO v_run;
  RETURN v_run;
END;
$fn$;

-- Failed build: the table being built is dropped, the draft shows the reason; the published version stays.
CREATE FUNCTION meta.mart_builder_build_failed(p_version_id bigint, p_message text) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, meta AS $fn$
DECLARE
  v_code text;
BEGIN
  SELECT m.mart_code INTO v_code FROM meta.dm_mart_version v JOIN meta.dm_mart m ON m.mart_id = v.mart_id
   WHERE v.version_id = p_version_id;
  IF v_code IS NOT NULL THEN
    EXECUTE format('DROP TABLE IF EXISTS dm.%I', v_code || '__next');
  END IF;
  UPDATE meta.dm_mart_version x SET build_status = 'FAILED', build_message = left(coalesce(p_message, 'Build failed'), 4000),
         ddl_status = CASE WHEN x.ddl_status = 'PROPOSED' THEN 'REJECTED' ELSE x.ddl_status END
   WHERE x.version_id = p_version_id AND x.build_status = 'BUILDING';
END;
$fn$;

-- As in 018, plus: a PUBLISH run that filled dm.<code>__next swaps it in and publishes its version in the
-- same transaction; a failed one drops dm.<code>__next.
CREATE OR REPLACE FUNCTION etl.dm_mart_run_finish(p_run_id bigint, p_ok boolean, p_rows_deleted bigint, p_rows_inserted bigint,
  p_error_code text, p_error_message text, p_diagnostic jsonb)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl, meta AS $fn$
DECLARE
  r etl.dm_mart_run;
  q etl.dm_mart_run_request;
  v_code text;
  v_status text := CASE WHEN p_ok THEN 'SUCCESS' ELSE 'FAILED' END;
BEGIN
  IF p_ok IS NULL OR (NOT p_ok AND p_error_code IS NULL) OR (p_ok AND p_error_code IS NOT NULL)
     OR (p_error_code IS NOT NULL AND p_error_code !~ '^[A-Z0-9][A-Z0-9_]{0,59}$') THEN
    RAISE EXCEPTION 'Invalid run result';
  END IF;
  SELECT * INTO r FROM etl.dm_mart_run x WHERE x.run_id = p_run_id FOR UPDATE;
  IF NOT FOUND OR r.status <> 'RUNNING' THEN RAISE EXCEPTION 'Running mart run does not exist'; END IF;
  SELECT * INTO q FROM etl.dm_mart_run_request x WHERE x.request_id = r.request_id FOR UPDATE;
  IF q.trigger_kind = 'PUBLISH' THEN
    IF p_ok THEN
      SELECT m.mart_code INTO v_code FROM meta.dm_mart m WHERE m.mart_id = r.mart_id;
      IF to_regclass(format('dm.%I', v_code || '__next')) IS NULL THEN
        RAISE EXCEPTION 'Built table dm.%__next not found', v_code;
      END IF;
      EXECUTE format('DROP TABLE IF EXISTS dm.%I', v_code);
      EXECUTE format('ALTER TABLE dm.%I RENAME TO %I', v_code || '__next', v_code);
      EXECUTE format('ALTER TABLE dm.%I RENAME CONSTRAINT %I TO %I', v_code, v_code || '__next_pk', v_code || '_pk');
      UPDATE meta.dm_mart_version x SET ddl_status = 'APPLIED' WHERE x.version_id = r.version_id;
      PERFORM meta.mart_builder_publish_now(r.version_id, q.requested_by_user_id);
    ELSE
      PERFORM meta.mart_builder_build_failed(r.version_id, coalesce(p_error_code, 'FAILED') || ': '
                                             || coalesce(p_error_message, ''));
    END IF;
  END IF;
  UPDATE etl.dm_mart_run x SET status = v_status, finished_at = clock_timestamp(), rows_deleted = p_rows_deleted,
         rows_inserted = p_rows_inserted, error_code = p_error_code,
         error_message = left(nullif(btrim(coalesce(p_error_message, '')), ''), 4000), diagnostic = p_diagnostic
   WHERE x.run_id = p_run_id;
  UPDATE etl.dm_mart_run_request x SET status = v_status, finished_at = clock_timestamp(), run_id = p_run_id,
         result_code = p_error_code, message = left(nullif(btrim(coalesce(p_error_message, '')), ''), 4000)
   WHERE x.request_id = r.request_id AND x.status = 'RUNNING';
  PERFORM pg_advisory_unlock(hashtext('dwh.dm_mart_run_request'), r.request_id::integer);
  RETURN v_status;
END;
$fn$;

-- State of marts: the 018 columns, then build and deletion.
CREATE OR REPLACE VIEW etl.v_dm_mart_status AS
SELECT m.mart_id, m.mart_code, m.title, m.description, m.required_permission_code, m.ai_enabled, m.auto_after_ods,
  m.is_active, pv.version_id AS published_version_id, pv.version_no AS published_version_no, pv.published_at,
  dv.version_id AS draft_version_id, dv.version_no AS draft_version_no, dv.ddl_status AS draft_ddl_status,
  (to_regclass(format('dm.%I', m.mart_code)) IS NOT NULL) AS table_exists,
  lr.run_id AS last_run_id, lr.status AS last_run_status, lr.started_at AS last_run_started_at,
  lr.finished_at AS last_run_finished_at, lr.rows_inserted AS last_run_rows, lr.error_code AS last_run_error_code,
  lr.error_message AS last_run_error_message, lr.diagnostic AS last_run_diagnostic,
  rq.request_id AS last_request_id, rq.trigger_kind AS last_request_trigger, rq.status AS last_request_status,
  rq.requested_by AS last_request_by, rq.requested_at AS last_request_at, rq.result_code AS last_request_result,
  coalesce(EXISTS (SELECT 1 FROM etl.dm_mart_run_request x WHERE x.mart_id = m.mart_id
                    AND x.status IN ('QUEUED', 'CLAIMED', 'RUNNING')), false) AS has_active_request,
  dv.build_status AS draft_build_status, dv.build_message AS draft_build_message,
  m.deleted_at, (m.deleted_at IS NOT NULL) AS is_deleted
FROM meta.dm_mart m
LEFT JOIN meta.dm_mart_version pv ON pv.mart_id = m.mart_id AND pv.status = 'PUBLISHED'
LEFT JOIN meta.dm_mart_version dv ON dv.mart_id = m.mart_id AND dv.status = 'DRAFT'
LEFT JOIN LATERAL (SELECT * FROM etl.dm_mart_run r WHERE r.mart_id = m.mart_id ORDER BY r.run_id DESC LIMIT 1) lr ON true
LEFT JOIN LATERAL (SELECT * FROM etl.dm_mart_run_request r WHERE r.mart_id = m.mart_id
                    ORDER BY r.request_id DESC LIMIT 1) rq ON true;

-- ------------------------------------------------------------------ privileges

REVOKE ALL ON FUNCTION
  meta.mart_builder_guard(), meta.mart_builder_building_guard(), meta.mart_builder_ddl(bigint, text), meta.mart_builder_changes(bigint),
  meta.mart_builder_impact(bigint, text[]), meta.mart_builder_impact_empty(jsonb),
  meta.mart_builder_publish_now(bigint, bigint), meta.mart_builder_plan(bigint),
  meta.mart_builder_build_failed(bigint, text),
  app.mart_builder_preview(text, bigint), app.mart_builder_publish(text, bigint, boolean),
  app.mart_builder_delete(text, bigint, boolean)
FROM PUBLIC;

DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT 'FUNCTION ' || p.oid::regprocedure::text AS obj, x.grantee
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace, aclexplode(p.proacl) x
     WHERE n.nspname IN ('meta', 'app') AND p.proname LIKE 'mart\_builder\_%' AND x.grantee <> p.proowner
  LOOP
    EXECUTE format('REVOKE ALL ON %s FROM %s', r.obj,
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED % FROM %', r.obj,
                 CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(r.grantee) END;
  END LOOP;
END
$acl$;

GRANT EXECUTE ON FUNCTION
  app.mart_builder_preview(text, bigint), app.mart_builder_publish(text, bigint, boolean),
  app.mart_builder_delete(text, bigint, boolean)
TO dwh_app;

SELECT 'MIGRATION_019_DONE' AS result;
