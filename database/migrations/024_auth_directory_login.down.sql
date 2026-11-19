-- I20 rollback of 024: removes the two functions. Stored objectGUIDs (app.user_account.external_subject) stay:
-- the column existed before 024 and administrators see it in the user catalog; nothing else is touched.
-- Run as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 024_auth_directory_login.down.sql
BEGIN;
DROP FUNCTION app.auth_bind_external_subject(bigint, text);
DROP FUNCTION app.auth_get_directory_user(text);
SELECT 'ROLLBACK_024_DONE' AS result;
COMMIT;
