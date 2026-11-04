-- I05 rollback of 008: remove the object configuration model, restore the 006 snapshot function.
-- Refuses and changes nothing if any configuration version or proposal exists (dropping
-- the tables would lose it). Run as the schema owner:
--   psql -v ON_ERROR_STOP=1 -f 008_object_config.down.sql
BEGIN;
LOCK TABLE meta.object_config_version, meta.target_change_proposal IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM meta.object_config_version) OR EXISTS (SELECT 1 FROM meta.target_change_proposal) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: object configurations exist';
  END IF;
END
$do$;
DROP FUNCTION app.integration_target_proposal_reject(text, bigint);
DROP FUNCTION app.integration_target_proposal_create(text, bigint, text, text, text, text);
DROP FUNCTION app.integration_object_config_set_drift(text, bigint, bigint, boolean, jsonb);
DROP FUNCTION app.integration_object_config_publish(text, bigint, integer);
DROP FUNCTION app.integration_object_config_record_validation(text, bigint, integer, boolean, jsonb);
DROP FUNCTION app.integration_object_config_save_draft(text, bigint, integer, jsonb);
DROP FUNCTION app.integration_object_config_get(text, bigint);
DROP TABLE meta.target_change_proposal;
DROP TABLE meta.object_config_current;
DROP TABLE meta.object_config_validation;
DROP TABLE meta.object_config_key_part;
DROP TABLE meta.object_config_field;
DROP TABLE meta.object_config_version;
DROP FUNCTION meta.object_config_hash(bigint);
DROP FUNCTION meta.object_config_definition(bigint);
DROP FUNCTION meta.object_config_reject_change();
DROP FUNCTION meta.object_config_guard();

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

DROP FUNCTION meta.source_is_legacy(bigint);
COMMIT;
