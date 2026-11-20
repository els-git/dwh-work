-- I03-05: change the schema/name of a source object, or delete it, while it is not used -- package 028
-- (plan docs/DWH_I03_05_OBJECT_IDENTITY_EDIT_PLAN_APPROVED_20261120.md, decisions O1-O4 = A).
--
-- "Used" (O1): at least one row in any table whose foreign key references meta.source_object. The list is read
-- from the catalog at every check, so tables added later count by themselves (snapshots, mapping versions,
-- ODS versions, proposals, runs, requests, jobs, errors, watermarks ...).
--   meta.source_object_usage(id)                          internal: referencing tables that hold rows of the object
--   app.integration_source_object_usage(token, id)        administrators: the same list (empty = not used)
--   app.integration_source_object_rename(token, id, schema, object)   O2: schema and name only, not the system
--   app.integration_source_object_delete(token, id)       O3: delete an unused object
-- Both changes lock the object row first (FOR UPDATE blocks new referencing rows until commit), refuse a used
-- object with the list of tables, refuse a duplicate schema/name of the same system (O4) and write an audit event.
-- Nothing of 001-027 and of the old app.integration_source_object_* functions changes.
-- Run as the owner of meta.source_object (dwh_admin): psql -1 -v ON_ERROR_STOP=1 -f 028_source_object_identity.sql
DO $pre$
DECLARE
  r record;
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regprocedure('app.integration_source_object_rename(text, bigint, text, text)') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 028 is already applied';
  END IF;
  IF to_regprocedure('app.integration_admin_session_user(text)') IS NULL
     OR to_regprocedure('audit.log_security_event(text, text, text, text, text, jsonb)') IS NULL THEN
    RAISE EXCEPTION 'STOP: app.integration_admin_session_user or audit.log_security_event is missing';
  END IF;
  IF NOT has_table_privilege(current_user, 'meta.source_object', 'SELECT,UPDATE,DELETE') THEN
    RAISE EXCEPTION 'STOP: run as the owner of meta.source_object';
  END IF;
  FOR r IN SELECT c.conrelid::regclass AS tbl FROM pg_constraint c
            WHERE c.contype = 'f' AND c.confrelid = 'meta.source_object'::regclass LOOP
    IF NOT has_table_privilege(current_user, r.tbl, 'SELECT') THEN
      RAISE EXCEPTION 'STOP: no SELECT on %, which references meta.source_object', r.tbl;
    END IF;
  END LOOP;
END
$pre$;

CREATE FUNCTION meta.source_object_usage(p_source_object_id bigint)
RETURNS TABLE (table_name text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $fn$
DECLARE
  r record;
  v_used boolean;
BEGIN
  FOR r IN
    SELECT c.conrelid::regclass AS tbl, a.attname AS col
      FROM pg_constraint c
      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
     WHERE c.contype = 'f' AND c.confrelid = 'meta.source_object'::regclass AND array_length(c.conkey, 1) = 1
     ORDER BY 1, 2
  LOOP
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %s WHERE %I = $1)', r.tbl, r.col) INTO v_used USING p_source_object_id;
    IF v_used THEN
      table_name := r.tbl::text;
      RETURN NEXT;
    END IF;
  END LOOP;
END;
$fn$;
REVOKE ALL ON FUNCTION meta.source_object_usage(bigint) FROM PUBLIC;

CREATE FUNCTION app.integration_source_object_usage(p_token_hash text, p_source_object_id bigint)
RETURNS TABLE (table_name text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
BEGIN
  PERFORM app.integration_admin_session_user(p_token_hash);
  IF NOT EXISTS (SELECT 1 FROM meta.source_object o WHERE o.source_object_id = p_source_object_id) THEN
    RAISE EXCEPTION 'Source object does not exist';
  END IF;
  RETURN QUERY SELECT u.table_name FROM meta.source_object_usage(p_source_object_id) u;
END;
$fn$;

CREATE FUNCTION app.integration_source_object_rename(
  p_token_hash text, p_source_object_id bigint, p_source_schema text, p_source_object text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  o meta.source_object;
  v_schema text := btrim(coalesce(p_source_schema, ''));
  v_object text := btrim(coalesce(p_source_object, ''));
  v_used text;
BEGIN
  IF v_schema = '' THEN RAISE EXCEPTION 'Source schema is required'; END IF;
  IF v_object = '' THEN RAISE EXCEPTION 'Source object is required'; END IF;
  SELECT * INTO o FROM meta.source_object x WHERE x.source_object_id = p_source_object_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source object does not exist'; END IF;
  IF o.source_schema = v_schema AND o.source_object = v_object THEN
    RETURN false;
  END IF;
  SELECT string_agg(u.table_name, ', ') INTO v_used FROM meta.source_object_usage(p_source_object_id) u;
  IF v_used IS NOT NULL THEN
    RAISE EXCEPTION 'Source object is in use and keeps its schema and name: %', v_used;
  END IF;
  IF EXISTS (SELECT 1 FROM meta.source_object x
              WHERE x.source_system_id = o.source_system_id AND x.source_schema = v_schema
                AND x.source_object = v_object AND x.source_object_id <> o.source_object_id) THEN
    RAISE EXCEPTION 'Source object already exists in this source system: %.%', v_schema, v_object;
  END IF;
  UPDATE meta.source_object SET source_schema = v_schema, source_object = v_object, updated_at = clock_timestamp()
   WHERE source_object_id = p_source_object_id;
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_OBJECT_RENAMED', 'SOURCE_OBJECT', p_source_object_id::text,
    'UPDATE', 'SUCCESS', jsonb_build_object('source_system_id', o.source_system_id,
      'before', o.source_schema || '.' || o.source_object, 'after', v_schema || '.' || v_object,
      'actor_user_id', v_actor));
  RETURN true;
END;
$fn$;

CREATE FUNCTION app.integration_source_object_delete(p_token_hash text, p_source_object_id bigint)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  o meta.source_object;
  v_used text;
BEGIN
  SELECT * INTO o FROM meta.source_object x WHERE x.source_object_id = p_source_object_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source object does not exist'; END IF;
  SELECT string_agg(u.table_name, ', ') INTO v_used FROM meta.source_object_usage(p_source_object_id) u;
  IF v_used IS NOT NULL THEN
    RAISE EXCEPTION 'Source object is in use and cannot be deleted: %', v_used;
  END IF;
  DELETE FROM meta.source_object WHERE source_object_id = p_source_object_id;
  PERFORM audit.log_security_event('INTEGRATION_SOURCE_OBJECT_DELETED', 'SOURCE_OBJECT', p_source_object_id::text,
    'DELETE', 'SUCCESS', jsonb_build_object('source_system_id', o.source_system_id,
      'source_schema', o.source_schema, 'source_object', o.source_object, 'object_type', o.object_type,
      'actor_user_id', v_actor));
  RETURN true;
END;
$fn$;

REVOKE ALL ON FUNCTION app.integration_source_object_usage(text, bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.integration_source_object_rename(text, bigint, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.integration_source_object_delete(text, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.integration_source_object_usage(text, bigint) TO dwh_app;
GRANT EXECUTE ON FUNCTION app.integration_source_object_rename(text, bigint, text, text) TO dwh_app;
GRANT EXECUTE ON FUNCTION app.integration_source_object_delete(text, bigint) TO dwh_app;

-- result line on stdout (dwh_sql_package.py looks for it there)
SELECT 'MIGRATION_028_DONE' AS result;
