-- I20 step 2 (inventory, read-only): how login works in the database today. Catalog only, no user data
-- except counts. Run on the server:
--   cp /opt/dwh/current/src/database/diagnostics/i20_step2_auth_inventory.sql /srv/dwh/incoming/
--   sudo /opt/dwh/current/venv/bin/python /opt/dwh/current/src/tools/server/dwh_readonly_sql.py /srv/dwh/incoming/i20_step2_auth_inventory.sql

-- 1. Columns of the user catalog
SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull AS not_null
  FROM pg_attribute a WHERE a.attrelid = 'app.user_account'::regclass AND a.attnum > 0 AND NOT a.attisdropped
 ORDER BY a.attnum;

-- 2. Check constraints of the user catalog (allowed providers, password rules)
SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint
 WHERE conrelid = 'app.user_account'::regclass AND contype IN ('c', 'u') ORDER BY conname;

-- 3. Login, session and audit functions: signature, SECURITY DEFINER, who may execute
SELECT p.oid::regprocedure AS function_name, p.prosecdef AS security_definer, pg_get_function_result(p.oid) AS result,
       coalesce(array_to_string(p.proacl, ' '), '(default)') AS acl
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'app' AND (p.proname LIKE 'auth\_%' OR p.proname LIKE '%password%' OR p.proname LIKE 'user\_%')
 ORDER BY 1;

-- 4. Text of the functions the login uses
SELECT p.oid::regprocedure AS function_name, pg_get_functiondef(p.oid) AS definition
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'app' AND p.proname IN ('auth_get_local_user', 'auth_create_session', 'auth_log_login_failure',
                                           'auth_log_login_success', 'auth_session_user')
 ORDER BY 1;

-- 5. Users by provider and state (counts only; last: stops the run if dwh_app may not read the table)
SELECT auth_provider, is_active, count(*) AS users, count(*) FILTER (WHERE password_hash IS NOT NULL) AS with_password
  FROM app.user_account GROUP BY 1, 2 ORDER BY 1, 2
