-- I19 rollback of 018: remove the DM mart builder (catalog, marts, runs, requests, functions, status view).
-- Refuses and changes nothing if any entity, mart, request or run exists (their removal would destroy the
-- configuration and the run history). Rows of meta.ai_report_object (016) are not touched: they can only
-- exist for published marts, so their presence already refuses the rollback. Nothing of 001-017 is touched.
-- Run as the owner:   psql -v ON_ERROR_STOP=1 -f 018_dm_builder.down.sql
BEGIN;
LOCK TABLE meta.dm_entity, meta.dm_mart, etl.dm_mart_run_request, etl.dm_mart_run IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM meta.dm_entity) OR EXISTS (SELECT 1 FROM meta.dm_mart)
     OR EXISTS (SELECT 1 FROM etl.dm_mart_run_request) OR EXISTS (SELECT 1 FROM etl.dm_mart_run) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: DM builder entities, marts or runs exist';
  END IF;
END
$do$;
DROP VIEW etl.v_dm_mart_status;
DROP FUNCTION etl.dm_mart_run_finish(bigint, boolean, bigint, bigint, text, text, jsonb);
DROP FUNCTION etl.dm_mart_run_begin(bigint, text, bigint);
DROP FUNCTION etl.dm_mart_run_request_claim(text);
DROP FUNCTION etl.dm_mart_enqueue_after_ods(text);
DROP FUNCTION app.dm_mart_run_request_cancel(text, bigint);
DROP FUNCTION app.dm_mart_run_request_create(text, bigint);
DROP FUNCTION app.dm_mart_version_publish(text, bigint);
DROP FUNCTION app.dm_mart_ddl_reject(text, bigint);
DROP FUNCTION app.dm_mart_ddl_propose(text, bigint);
DROP FUNCTION app.dm_mart_version_discard(text, bigint);
DROP FUNCTION app.dm_mart_version_save(text, bigint, jsonb);
DROP FUNCTION app.dm_mart_version_create(text, bigint);
DROP FUNCTION app.dm_mart_save(text, bigint, text, text, text, text, boolean, boolean, boolean);
DROP FUNCTION app.dm_entity_key_save(text, bigint, text, text, boolean);
DROP FUNCTION app.dm_entity_save(text, bigint, text, text, boolean);
DROP FUNCTION meta.dm_mart_ddl_text(bigint);
DROP FUNCTION meta.dm_mart_version_problems(bigint);
DROP FUNCTION meta.dm_mart_sync_ai(bigint, bigint);
DROP FUNCTION meta.dm_mart_table_matches(bigint);
DROP FUNCTION meta.dm_actor_username(bigint);
DROP FUNCTION meta.dm_ods_column_type(text, text);
DROP FUNCTION meta.dm_type_canonical(text);
DROP FUNCTION meta.dm_type_family(regtype);
ALTER TABLE etl.dm_mart_run_request DROP CONSTRAINT dm_mart_run_request_run;
DROP TABLE etl.dm_mart_run;
DROP TABLE etl.dm_mart_run_request;
DROP TABLE meta.dm_mart_filter;
DROP TABLE meta.dm_mart_field_source;
DROP TABLE meta.dm_mart_field;
DROP TABLE meta.dm_mart_source;
DROP TABLE meta.dm_mart_version;
DROP TABLE meta.dm_mart;
DROP TABLE meta.dm_entity_key;
DROP TABLE meta.dm_entity;

SELECT 'ROLLBACK_018_DONE' AS result;
COMMIT;
