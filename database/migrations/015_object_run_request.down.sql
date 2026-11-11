-- I08 rollback of 015: remove the queue of object run requests, its functions and the status view.
-- Refuses and changes nothing if any request exists (its removal would destroy the request history).
-- Nothing of 001-014 and of the CRM queue is touched. Run as the owner:
--   psql -v ON_ERROR_STOP=1 -f 015_object_run_request.down.sql
BEGIN;
LOCK TABLE etl.object_run_request IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM etl.object_run_request) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: object run requests exist';
  END IF;
END
$do$;
DROP VIEW etl.v_object_load_status;
DROP FUNCTION etl.object_run_request_finish(bigint, boolean, bigint, bigint, text, text);
DROP FUNCTION etl.object_run_request_start(bigint, text);
DROP FUNCTION etl.object_run_request_claim(text);
DROP FUNCTION app.integration_object_run_request_cancel(text, bigint);
DROP FUNCTION app.integration_object_run_request_create(text, bigint, text);
DROP TABLE etl.object_run_request;

SELECT 'ROLLBACK_015_DONE' AS result;
COMMIT;
