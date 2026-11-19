-- I20 rollback of 025: removes the switch function. Users already switched keep their login method (the columns
-- existed before 025); nothing else is touched. Run as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 025_auth_provider_switch.down.sql
BEGIN;
DROP FUNCTION app.user_catalog_set_auth_provider(text, bigint, text, text);
SELECT 'ROLLBACK_025_DONE' AS result;
COMMIT;
