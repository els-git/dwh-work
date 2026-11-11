-- I06-05 rollback of 012: remove the ODS configuration and the ODS step of runs.
-- Refuses and changes nothing if any ODS version, ODS run or ODS change proposal exists
-- (their removal would destroy data). Run as the owner:
--   psql -v ON_ERROR_STOP=1 -f 012_object_ods_config.down.sql
BEGIN;
LOCK TABLE meta.object_ods_version, meta.target_change_proposal, etl.object_ods_run IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM meta.object_ods_version)
     OR EXISTS (SELECT 1 FROM etl.object_ods_run)
     OR EXISTS (SELECT 1 FROM meta.target_change_proposal WHERE ods_version_id IS NOT NULL) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: ODS configurations, ODS runs or ODS change proposals exist';
  END IF;
END
$do$;

DROP FUNCTION etl.object_ods_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text);
DROP FUNCTION etl.object_ods_run_begin(bigint, bigint);
DROP FUNCTION etl.object_ods_run_close(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text);
DROP TABLE etl.object_ods_record_change;
DROP FUNCTION etl.object_ods_record_change_guard();
DROP TABLE etl.object_ods_run;

DROP FUNCTION app.integration_ods_proposal_create(text, bigint, text, text, text);
DROP FUNCTION app.integration_ods_config_publish(text, bigint, integer);
DROP FUNCTION app.integration_ods_config_record_validation(text, bigint, integer, boolean, jsonb);
DROP FUNCTION app.integration_ods_config_save_draft(text, bigint, integer, jsonb);
DROP FUNCTION app.integration_ods_config_get(text, bigint);
DROP FUNCTION meta.object_stg_table(bigint);
DROP FUNCTION meta.object_ods_hash(bigint);
DROP FUNCTION meta.object_ods_definition(bigint);

ALTER TABLE meta.target_change_proposal
  DROP CONSTRAINT target_change_proposal_ods_schema,
  DROP CONSTRAINT target_change_proposal_one_version,
  DROP COLUMN ods_version_id,
  ALTER COLUMN version_id SET NOT NULL;

DROP TABLE meta.object_ods_current;
DROP TABLE meta.object_ods_validation;
DROP TABLE meta.object_ods_key_part;
DROP TABLE meta.object_ods_field;
DROP TABLE meta.object_ods_version;
DROP FUNCTION meta.object_ods_guard();

SELECT 'ROLLBACK_012_DONE' AS result;
COMMIT;
