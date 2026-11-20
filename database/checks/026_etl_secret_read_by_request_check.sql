-- I02-03 check of 026: read-only catalog verification. SELECT only; pg_catalog. Runs as dwh_app or the owner.
-- Expected: 6 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH fn AS (SELECT p.oid, p.prosecdef, p.proconfig, p.proacl, p.proowner, p.prosrc
              FROM pg_proc p WHERE p.oid = to_regprocedure('etl.source_secret_read_begin(text, bigint)')),
old AS (SELECT p.oid FROM pg_proc p WHERE p.oid = to_regprocedure('etl.source_secret_read_begin(text)')),
checks AS (
  SELECT 1 AS n, 'function etl.source_secret_read_begin(text, bigint), SECURITY DEFINER, fixed search_path' AS check_name,
    EXISTS (SELECT 1 FROM fn WHERE prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) AS ok
  UNION ALL SELECT 2, 'EXECUTE of the new function: dwh_etl only (besides the owner), no PUBLIC',
    EXISTS (SELECT 1 FROM fn WHERE has_function_privilege('dwh_etl', oid, 'EXECUTE'))
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE x.grantee <> fn.proowner AND x.grantee <> (SELECT oid FROM pg_roles WHERE rolname = 'dwh_etl'))
  UNION ALL SELECT 3, 'old etl.source_secret_read_begin(text): no EXECUTE for dwh_etl',
    EXISTS (SELECT 1 FROM old) AND NOT EXISTS (SELECT 1 FROM old WHERE has_function_privilege('dwh_etl', oid, 'EXECUTE'))
  UNION ALL SELECT 4, 'the new function checks the request (RUNNING, kind, source, 15 minutes, one read) and records refusals',
    EXISTS (SELECT 1 FROM fn WHERE prosrc LIKE '%REQUEST_NOT_RUNNING%' AND prosrc LIKE '%REQUEST_OF_OTHER_SOURCE%'
             AND prosrc LIKE '%15 minutes%' AND prosrc LIKE '%CREDENTIAL_ALREADY_READ%'
             AND prosrc LIKE '%record_denied_attempt%')
  UNION ALL SELECT 5, 'table etl.object_run_request_secret_read: request_id primary key',
    EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = to_regclass('etl.object_run_request_secret_read')
             AND k.contype = 'p')
  UNION ALL SELECT 6, 'table etl.object_run_request_secret_read: no rights for dwh_app, dwh_etl',
    to_regclass('etl.object_run_request_secret_read') IS NOT NULL
    AND NOT has_table_privilege('dwh_app', 'etl.object_run_request_secret_read', 'SELECT,INSERT,UPDATE,DELETE')
    AND NOT has_table_privilege('dwh_etl', 'etl.object_run_request_secret_read', 'SELECT,INSERT,UPDATE,DELETE')
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
