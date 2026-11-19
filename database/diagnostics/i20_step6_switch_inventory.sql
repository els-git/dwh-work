-- I20 provider switch (inventory, read-only): rules of the user catalog functions and the triggers of
-- app.user_account, to build the switch LOCAL <-> domain on them. Catalog only, no user data. Run on the server:
--   cp /opt/dwh/current/src/database/diagnostics/i20_step6_switch_inventory.sql /srv/dwh/incoming/
--   sudo /opt/dwh/current/venv/bin/python /opt/dwh/current/src/tools/server/dwh_readonly_sql.py /srv/dwh/incoming/i20_step6_switch_inventory.sql

-- 1. Text of the administrator functions of the user catalog (session check, who may manage whom, password reset)
SELECT p.oid::regprocedure AS function_name, pg_get_functiondef(p.oid) AS definition
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'app' AND p.proname IN ('user_admin_session_user', 'user_catalog_reset_password',
                                           'user_catalog_set_active', 'user_catalog_set_account_level')
 ORDER BY 1;

-- 2. Triggers of app.user_account and the text of their functions
SELECT t.tgname, t.tgenabled, pg_get_triggerdef(t.oid) AS trigger_definition, pg_get_functiondef(t.tgfoid) AS function_definition
  FROM pg_trigger t
 WHERE t.tgrelid = 'app.user_account'::regclass AND NOT t.tgisinternal
 ORDER BY t.tgname;

-- 3. Other tables that store a password or a provider of a user (history of identity changes)
SELECT c.oid::regclass AS table_name, a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type
  FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname IN ('app', 'audit') AND c.relkind = 'r' AND NOT a.attisdropped AND a.attnum > 0
   AND (a.attname LIKE '%password%' OR a.attname LIKE '%auth_provider%' OR a.attname LIKE '%external_subject%')
 ORDER BY 1, 2
