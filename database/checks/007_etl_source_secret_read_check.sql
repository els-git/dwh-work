-- I04 check of 007: read-only catalog verification of the ETL credential READ functions.
-- SELECT only; pg_catalog only. Runs as dwh_app (run_readonly_sql.ps1) or as the owner.
-- Expected: 7 results, every one PASS. The 006 check stays valid (10/10).
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH fn AS (SELECT p.oid, p.prosecdef, p.proconfig, p.proacl, p.proowner FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'etl' AND p.proname IN ('source_secret_read_begin', 'source_secret_read_finish')),
checks AS (
  SELECT 1 AS n, '2 ETL READ functions present' AS check_name, (SELECT count(*) FROM fn) = 2 AS ok
  UNION ALL SELECT 2, 'SECURITY DEFINER with fixed search_path',
    (SELECT count(*) FROM fn WHERE prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) = 2
  UNION ALL SELECT 3, 'dwh_etl may EXECUTE both',
    (SELECT count(*) FROM fn WHERE has_function_privilege('dwh_etl', oid, 'EXECUTE')) = 2
  UNION ALL SELECT 4, 'dwh_app may not EXECUTE them',
    (SELECT count(*) FROM fn WHERE has_function_privilege('dwh_app', oid, 'EXECUTE')) = 0
  UNION ALL SELECT 5, 'grants only to owner and dwh_etl; no PUBLIC',
    NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                 WHERE x.grantee NOT IN (fn.proowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_etl')))
  UNION ALL SELECT 6, 'dwh_etl has USAGE on schema etl',
    has_schema_privilege('dwh_etl', 'etl', 'USAGE')
  UNION ALL SELECT 7, 'dwh_etl has no USAGE on schema secret_store',
    NOT has_schema_privilege('dwh_etl', 'secret_store', 'USAGE')
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
