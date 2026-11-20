-- I03-05 check of 028: read-only catalog verification. SELECT only; pg_catalog. Runs as dwh_app or the owner.
-- Expected: 6 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH fn AS (
  SELECT p.oid, p.oid::regprocedure::text AS name, p.prosecdef, p.proconfig, p.proacl, p.proowner, p.prosrc
    FROM pg_proc p
   WHERE p.oid IN (to_regprocedure('app.integration_source_object_usage(text, bigint)'),
                   to_regprocedure('app.integration_source_object_rename(text, bigint, text, text)'),
                   to_regprocedure('app.integration_source_object_delete(text, bigint)'),
                   to_regprocedure('meta.source_object_usage(bigint)'))),
api AS (SELECT * FROM fn WHERE name LIKE 'app.%'),
checks AS (
  SELECT 1 AS n, 'four functions: meta.source_object_usage, app.integration_source_object_usage/_rename/_delete' AS check_name,
    (SELECT count(*) FROM fn) = 4 AS ok
  UNION ALL SELECT 2, 'all four SECURITY DEFINER with a fixed search_path',
    (SELECT count(*) FROM fn WHERE prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) = 4
  UNION ALL SELECT 3, 'EXECUTE of the three app functions: dwh_app only (besides the owner), no PUBLIC',
    (SELECT count(*) FROM api WHERE has_function_privilege('dwh_app', oid, 'EXECUTE')) = 3
    AND NOT EXISTS (SELECT 1 FROM api, aclexplode(coalesce(api.proacl, acldefault('f', api.proowner))) x
                     WHERE x.grantee <> api.proowner AND x.grantee <> (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app'))
  UNION ALL SELECT 4, 'meta.source_object_usage: no EXECUTE for PUBLIC, dwh_app, dwh_etl',
    EXISTS (SELECT 1 FROM fn WHERE name = 'meta.source_object_usage(bigint)'
             AND NOT has_function_privilege('dwh_app', oid, 'EXECUTE')
             AND NOT has_function_privilege('dwh_etl', oid, 'EXECUTE')
             AND NOT EXISTS (SELECT 1 FROM aclexplode(coalesce(proacl, acldefault('f', proowner))) x WHERE x.grantee = 0))
  UNION ALL SELECT 5, 'usage is read from the catalog: foreign keys that reference meta.source_object',
    EXISTS (SELECT 1 FROM fn WHERE name = 'meta.source_object_usage(bigint)'
             AND prosrc LIKE '%confrelid = ''meta.source_object''::regclass%')
  UNION ALL SELECT 6, 'rename and delete lock the object, check the usage and the session, write audit',
    (SELECT count(*) FROM api WHERE name NOT LIKE '%usage%' AND prosrc LIKE '%FOR UPDATE%'
       AND prosrc LIKE '%meta.source_object_usage%' AND prosrc LIKE '%integration_admin_session_user%'
       AND prosrc LIKE '%log_security_event%') = 2
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
