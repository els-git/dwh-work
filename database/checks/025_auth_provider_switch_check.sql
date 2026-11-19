-- I20 check of 025: read-only catalog verification of the provider switch function.
-- SELECT only; pg_catalog. Runs as dwh_app or as the owner. Expected: 5 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH fn AS (SELECT p.oid, p.prosecdef, p.proconfig, p.proacl, p.proowner, p.prorettype, p.prosrc
              FROM pg_proc p WHERE p.oid = to_regprocedure('app.user_catalog_set_auth_provider(text, bigint, text, text)')),
checks AS (
  SELECT 1 AS n, 'function app.user_catalog_set_auth_provider(text, bigint, text, text) returns boolean' AS check_name,
    EXISTS (SELECT 1 FROM fn WHERE prorettype = 'boolean'::regtype) AS ok
  UNION ALL SELECT 2, 'SECURITY DEFINER with fixed search_path',
    EXISTS (SELECT 1 FROM fn WHERE prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%'))
  UNION ALL SELECT 3, 'EXECUTE: dwh_app only (besides the owner), no PUBLIC',
    EXISTS (SELECT 1 FROM fn WHERE has_function_privilege('dwh_app', oid, 'EXECUTE'))
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE x.grantee <> fn.proowner AND x.grantee <> (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app'))
  UNION ALL SELECT 4, 'uses the administrator session, can_manage_user and protects system accounts',
    EXISTS (SELECT 1 FROM fn WHERE prosrc LIKE '%user_admin_session_user%' AND prosrc LIKE '%can_manage_user%'
             AND prosrc LIKE '%SUPERADMIN%' AND prosrc LIKE '%is_system%')
  UNION ALL SELECT 5, 'functions of 024 are present',
    to_regprocedure('app.auth_get_directory_user(text)') IS NOT NULL
    AND to_regprocedure('app.auth_bind_external_subject(bigint, text)') IS NOT NULL
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
