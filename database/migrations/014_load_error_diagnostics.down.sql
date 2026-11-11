-- I13-08 rollback of 014: remove the 12-parameter run-finish variants and their helper.
-- No data is removed: etl.error_log rows (with bad_value / diagnostic_context written by 014) stay.
-- The 10-parameter variants of 010 and 012 are not touched. Run as the owner:
--   psql -v ON_ERROR_STOP=1 -f 014_load_error_diagnostics.down.sql
BEGIN;
DROP FUNCTION etl.object_ods_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text, text, jsonb);
DROP FUNCTION etl.object_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text, text, jsonb);
DROP FUNCTION etl.load_error_diagnostics_valid(boolean, text, jsonb);

SELECT 'ROLLBACK_014_DONE' AS result;
COMMIT;
