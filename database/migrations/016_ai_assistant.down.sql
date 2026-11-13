-- I17 rollback of 016: remove the AI assistant tables, functions, permissions and the grants of dwh_ai_ro.
-- Refuses and changes nothing if removal would destroy data: requests in the log, configuration changed by
-- an administrator, or an AI_* permission given to a role.
-- The role dwh_ai_ro itself is kept (roles are shared by the databases of the server); it loses every
-- privilege in this database. Nothing of 001-015 is touched. Run as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 016_ai_assistant.down.sql
BEGIN;
LOCK TABLE app.ai_request_log, meta.ai_report_object, meta.ai_value_column, meta.ai_glossary, meta.ai_example,
  app.permission IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM app.ai_request_log) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: AI requests exist in app.ai_request_log';
  END IF;
  IF EXISTS (SELECT 1 FROM meta.ai_report_object WHERE updated_by_user_id IS NOT NULL)
     OR EXISTS (SELECT 1 FROM meta.ai_value_column WHERE updated_by_user_id IS NOT NULL)
     OR EXISTS (SELECT 1 FROM meta.ai_glossary) OR EXISTS (SELECT 1 FROM meta.ai_example) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: AI configuration was changed by an administrator';
  END IF;
  IF EXISTS (SELECT 1 FROM app.role_permission_history rp JOIN app.permission p ON p.permission_id = rp.permission_id
              WHERE p.permission_code IN ('AI_ASSISTANT_USE', 'AI_REPORT_RUN', 'AI_ADMIN_MANAGE')) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: AI_* permissions were given to roles';
  END IF;
END
$do$;
DROP FUNCTION app.ai_example_save(text, bigint, bigint, text, text, boolean);
DROP FUNCTION app.ai_glossary_save(text, bigint, text, text, bigint, boolean);
DROP FUNCTION app.ai_value_column_save(text, bigint, text, integer, boolean);
DROP FUNCTION app.ai_report_object_save(text, text, text, text, text, text, boolean);
DROP FUNCTION app.ai_request_log_list(text, integer);
DROP FUNCTION app.ai_request_finish(text, bigint, text, text, text, bigint[], bigint, text);
DROP FUNCTION app.ai_request_begin(text, text, text, boolean, text);
DROP FUNCTION app.ai_object_access(text);
DROP FUNCTION app.ai_session_user(text, text);
DROP FUNCTION app.ai_user_permissions(bigint);
DROP TABLE app.ai_request_log;
DROP TABLE meta.ai_example;
DROP TABLE meta.ai_glossary;
DROP TABLE meta.ai_value_column;
DROP TABLE meta.ai_report_object;
DELETE FROM app.permission WHERE permission_code IN ('AI_ASSISTANT_USE', 'AI_REPORT_RUN', 'AI_ADMIN_MANAGE');
DO $dmdef$
BEGIN
  EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA dm REVOKE SELECT ON TABLES FROM dwh_ai_ro',
                 (SELECT pg_get_userbyid(n.nspowner) FROM pg_namespace n WHERE n.nspname = 'dm'));
END
$dmdef$;
REVOKE ALL ON ALL TABLES IN SCHEMA dm FROM dwh_ai_ro;
REVOKE ALL ON SCHEMA dm FROM dwh_ai_ro;

SELECT 'ROLLBACK_016_DONE' AS result;
COMMIT;
