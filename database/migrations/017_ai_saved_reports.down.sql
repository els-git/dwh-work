-- I17-09 rollback of 017: remove saved-report folders, reports, their functions and the SAVED_REPORT kind of
-- app.ai_request_log. Refuses and changes nothing if removal would destroy data: any folder, any saved report
-- or any run of a saved report in the log. Nothing of 001-016 is touched otherwise. Run as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 017_ai_saved_reports.down.sql
BEGIN;
LOCK TABLE app.ai_request_log, app.saved_report, app.report_folder IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM app.report_folder) OR EXISTS (SELECT 1 FROM app.saved_report) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: saved-report folders or reports exist';
  END IF;
  IF EXISTS (SELECT 1 FROM app.ai_request_log WHERE request_kind = 'SAVED_REPORT') THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: runs of saved reports exist in app.ai_request_log';
  END IF;
END
$do$;
DROP FUNCTION app.saved_report_run_finish(text, bigint, text, text, bigint, text);
DROP FUNCTION app.saved_report_run_begin(text, bigint);
DROP FUNCTION app.saved_report_allowed_objects(text);
DROP FUNCTION app.saved_report_list(text);
DROP FUNCTION app.saved_report_delete(text, bigint);
DROP FUNCTION app.saved_report_update(text, bigint, bigint, text);
DROP FUNCTION app.saved_report_save(text, bigint, bigint, text);
DROP FUNCTION app.report_folder_delete(text, bigint);
DROP FUNCTION app.report_folder_save(text, bigint, text, text);
DROP FUNCTION app.report_folder_list(text);
DROP FUNCTION app.saved_report_folder_visible(bigint, bigint);
DROP FUNCTION app.saved_report_objects_allowed(bigint, bigint[]);
DROP FUNCTION app.saved_report_is_admin(bigint);
DROP FUNCTION app.saved_report_session_user(text, text);
ALTER TABLE app.ai_request_log DROP CONSTRAINT ai_request_log_saved_report;
ALTER TABLE app.ai_request_log DROP CONSTRAINT ai_request_log_kind;
ALTER TABLE app.ai_request_log DROP COLUMN saved_report_id;
-- The constraint of 016 with its original name.
ALTER TABLE app.ai_request_log ADD CONSTRAINT ai_request_log_request_kind_check
  CHECK (request_kind IN ('HELP', 'REPORT'));
DROP TABLE app.saved_report;
DROP TABLE app.report_folder;

SELECT 'ROLLBACK_017_DONE' AS result;
COMMIT;
