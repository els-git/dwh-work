-- I06 rollback of 010: remove the object run model and restore the run history view.
-- Refuses and changes nothing if any object run or object job exists (dropping the tables
-- would lose run history). Run as the owner of schemas etl and meta:
--   psql -v ON_ERROR_STOP=1 -f 010_object_run.down.sql
BEGIN;
LOCK TABLE etl.object_run, etl.object_record_change IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM etl.object_run) OR EXISTS (SELECT 1 FROM etl.object_record_change)
     OR EXISTS (SELECT 1 FROM etl.job WHERE code LIKE 'OBJECT\_LOAD\_%')
     OR EXISTS (SELECT 1 FROM etl.batch_run WHERE pipeline_code = 'OBJECT_LOAD') THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: object runs exist';
  END IF;
END
$do$;
DO $view$
DECLARE
  v_def text;
  v_old text := '= ANY (ARRAY[''ODS_TRANSFORM''::text, ''OBJECT_LOAD''::text])';
  v_new text := '= ''ODS_TRANSFORM''::text';
BEGIN
  SET LOCAL search_path = pg_catalog;
  v_def := pg_get_viewdef('etl.v_integration_run_history'::regclass, true);
  IF (length(v_def) - length(replace(v_def, v_old, ''))) / length(v_old) <> 1 THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: etl.v_integration_run_history is not the 010 definition';
  END IF;
  EXECUTE 'CREATE OR REPLACE VIEW etl.v_integration_run_history AS ' || replace(v_def, v_old, v_new);
END
$view$;
DROP FUNCTION etl.object_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text);
DROP FUNCTION etl.object_run_begin(bigint);
DROP TABLE etl.object_record_change;
DROP FUNCTION etl.object_record_change_guard();
DROP FUNCTION etl.object_run_close(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text);
DROP TABLE etl.object_run;
COMMIT;
