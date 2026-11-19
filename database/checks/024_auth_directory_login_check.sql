-- I20 check of 024: read-only catalog verification of the domain login functions.
-- SELECT only; pg_catalog. Runs as dwh_app or as the owner.
-- Expected: 6 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH fn AS (SELECT p.oid, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner, p.prorettype, p.provolatile
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'app' AND p.proname IN ('auth_get_directory_user', 'auth_bind_external_subject')),
checks AS (
  SELECT 1 AS n, 'two functions auth_get_directory_user(text), auth_bind_external_subject(bigint, text)' AS check_name,
    (SELECT count(*) FROM fn) = 2
    AND to_regprocedure('app.auth_get_directory_user(text)') IS NOT NULL
    AND to_regprocedure('app.auth_bind_external_subject(bigint, text)') IS NOT NULL AS ok
  UNION ALL SELECT 2, 'both SECURITY DEFINER with fixed search_path',
    NOT EXISTS (SELECT 1 FROM fn WHERE NOT (prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')))
  UNION ALL SELECT 3, 'EXECUTE: dwh_app only (besides the owner), no PUBLIC',
    (SELECT count(*) FROM fn WHERE has_function_privilege('dwh_app', oid, 'EXECUTE')) = 2
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE x.grantee <> fn.proowner AND x.grantee <> (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app'))
  UNION ALL SELECT 4, 'auth_get_directory_user: STABLE, returns external_subject, never password_hash',
    EXISTS (SELECT 1 FROM fn WHERE proname = 'auth_get_directory_user' AND provolatile = 's'
             AND pg_get_function_result(oid) LIKE '%external_subject%'
             AND pg_get_function_result(oid) NOT LIKE '%password%')
  UNION ALL SELECT 5, 'auth_bind_external_subject returns text',
    EXISTS (SELECT 1 FROM fn WHERE proname = 'auth_bind_external_subject' AND prorettype = 'text'::regtype)
  UNION ALL SELECT 6, 'auth_get_local_user unchanged: LOCAL only',
    EXISTS (SELECT 1 FROM pg_proc WHERE oid = to_regprocedure('app.auth_get_local_user(text)')
             AND prosrc LIKE '%auth_provider = ''LOCAL''%')
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
