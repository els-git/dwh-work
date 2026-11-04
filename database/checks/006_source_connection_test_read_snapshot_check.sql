-- I04 check of 006: read-only catalog verification of the connection test result,
-- credential READ and schema snapshot functions. SELECT only; pg_catalog only.
-- Runs as dwh_app (run_readonly_sql.ps1) or as the owner.
-- Expected: 10 results, every one PASS. After 006 the 005 check gives 10/11:
-- its item 2 counts 7 constraints and 006 adds 4 (test result columns).
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH t AS (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'meta' AND c.relname = 'source_connection' AND c.relkind = 'r'),
cols AS (SELECT a.attname FROM pg_attribute a WHERE a.attrelid = (SELECT oid FROM t) AND a.attnum > 0 AND NOT a.attisdropped),
fn AS (SELECT p.oid, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner FROM pg_proc p
         JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'app' AND p.proname IN ('integration_source_secret_read_begin', 'integration_source_secret_read_finish',
              'integration_source_connection_record_test', 'integration_source_metadata_capture')),
allapi AS (SELECT p.oid, p.proacl, p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'app' AND p.proname IN ('integration_source_connection_get', 'integration_source_connection_save',
              'integration_source_connection_set_status', 'integration_source_secret_begin', 'integration_source_secret_complete',
              'integration_source_secret_revoke', 'integration_source_secret_read_begin', 'integration_source_secret_read_finish',
              'integration_source_connection_record_test', 'integration_source_metadata_capture')),
checks AS (
  SELECT 1 AS n, '5 test result columns on meta.source_connection' AS check_name,
    (SELECT count(*) FROM cols WHERE attname IN ('last_test_at', 'last_test_ok', 'last_test_code', 'last_test_by',
      'last_test_server_version')) = 5 AS ok
  UNION ALL SELECT 2, 'test result complete-or-empty and READY-needs-test constraints validated',
    (SELECT count(*) FROM pg_constraint WHERE conrelid = (SELECT oid FROM t) AND convalidated
      AND conname IN ('source_connection_test_complete', 'source_connection_ready_tested')) = 2
  UNION ALL SELECT 3, 'connection constraints = 11 (7 from 005 + 4)',
    (SELECT count(*) FROM pg_constraint WHERE conrelid = (SELECT oid FROM t)) = 11
  UNION ALL SELECT 4, '4 new API functions present', (SELECT count(*) FROM fn) = 4
  UNION ALL SELECT 5, 'new functions SECURITY DEFINER with fixed search_path',
    (SELECT count(*) FROM fn WHERE prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) = 4
  UNION ALL SELECT 6, 'get returns the last test result',
    EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'app' AND p.proname = 'integration_source_connection_get'
               AND 'last_test_ok' = ANY (p.proargnames))
  UNION ALL SELECT 7, 'dwh_app may EXECUTE all 10 API functions',
    (SELECT count(*) FROM allapi WHERE has_function_privilege('dwh_app', oid, 'EXECUTE')) = 10
  UNION ALL SELECT 8, 'no PUBLIC EXECUTE on the API functions',
    NOT EXISTS (SELECT 1 FROM allapi, aclexplode(coalesce(allapi.proacl, acldefault('f', allapi.proowner))) x WHERE x.grantee = 0)
  UNION ALL SELECT 9, 'API functions: grants only to owner and dwh_app',
    NOT EXISTS (SELECT 1 FROM allapi, aclexplode(coalesce(allapi.proacl, acldefault('f', allapi.proowner))) x
                 WHERE x.grantee NOT IN (allapi.proowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app')))
  UNION ALL SELECT 10, 'dwh_app still has no USAGE on schema secret_store and no write on snapshot tables',
    NOT has_schema_privilege('dwh_app', 'secret_store', 'USAGE')
    AND NOT has_table_privilege('dwh_app', 'meta.metadata_capture', 'INSERT')
    AND NOT has_table_privilege('dwh_app', 'meta.source_object_snapshot', 'INSERT')
    AND NOT has_table_privilege('dwh_app', 'meta.source_column_snapshot', 'INSERT')
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
