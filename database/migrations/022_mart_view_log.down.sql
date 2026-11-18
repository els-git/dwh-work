-- I22 rollback of 022: remove the mart_view_* functions, the column view_detail and the kind MART_VIEW of
-- app.ai_request_log. Refuses and changes nothing if removal would destroy data: any MART_VIEW row in the log.
-- Nothing of 001-021 is touched otherwise. Run as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 022_mart_view_log.down.sql
BEGIN;
LOCK TABLE app.ai_request_log IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM app.ai_request_log WHERE request_kind = 'MART_VIEW') THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: mart views exist in app.ai_request_log';
  END IF;
END
$do$;
DROP FUNCTION app.mart_view_finish(text, bigint, text, text, bigint, text);
DROP FUNCTION app.mart_view_continue(text, bigint);
DROP FUNCTION app.mart_view_begin(text, bigint, jsonb);
DROP FUNCTION app.mart_view_list(text);
DROP FUNCTION app.mart_view_session_user(text);
ALTER TABLE app.ai_request_log DROP CONSTRAINT ai_request_log_kind;
ALTER TABLE app.ai_request_log DROP COLUMN view_detail;
-- The constraint of 017 with its name.
ALTER TABLE app.ai_request_log ADD CONSTRAINT ai_request_log_kind
  CHECK (request_kind IN ('HELP', 'REPORT', 'SAVED_REPORT'));

SELECT 'ROLLBACK_022_DONE' AS result;
COMMIT;
