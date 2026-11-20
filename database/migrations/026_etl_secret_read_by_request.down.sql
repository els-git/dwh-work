-- Rollback of 026: the ETL reads the password by the source code again (package 007).
-- The rows of etl.object_run_request_secret_read are only a trace of reads; the journal secret_store keeps
-- every READ (ATTEMPT / OUTCOME / DENIED) anyway. Run as the owner (dwh_admin):
--   psql -1 -v ON_ERROR_STOP=1 -f 026_etl_secret_read_by_request.down.sql
DO $pre$
BEGIN
  IF to_regprocedure('etl.source_secret_read_begin(text, bigint)') IS NULL THEN
    RAISE EXCEPTION 'STOP: 026 is not applied';
  END IF;
END
$pre$;
GRANT EXECUTE ON FUNCTION etl.source_secret_read_begin(text) TO dwh_etl;
DROP FUNCTION etl.source_secret_read_begin(text, bigint);
DROP TABLE etl.object_run_request_secret_read;
DO $post$ BEGIN RAISE NOTICE 'ROLLBACK_026_DONE'; END $post$;
