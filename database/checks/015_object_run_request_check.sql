-- I08 check of 015: read-only catalog verification of the object run request queue.
-- SELECT only; pg_catalog only. Runs as dwh_app (run_readonly_sql.ps1) or as the owner.
-- Expected: 8 results, every one PASS. The 010-014 checks are not affected.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH rel AS (SELECT c.oid, c.relname, c.relkind, c.relacl, c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE n.nspname = 'etl' AND c.relname IN ('object_run_request', 'v_object_load_status')),
fn AS (SELECT p.oid, n.nspname, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner
         FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE (n.nspname = 'app' AND p.proname IN ('integration_object_run_request_create', 'integration_object_run_request_cancel'))
           OR (n.nspname = 'etl' AND p.proname IN ('object_run_request_claim', 'object_run_request_start', 'object_run_request_finish'))),
checks AS (
  SELECT 1 AS n, 'table etl.object_run_request and view etl.v_object_load_status' AS check_name,
    (SELECT count(*) FROM rel WHERE relname = 'object_run_request' AND relkind = 'r') = 1
    AND (SELECT count(*) FROM rel WHERE relname = 'v_object_load_status' AND relkind = 'v') = 1 AS ok
  UNION ALL SELECT 2, 'five functions: create/cancel (app), claim/start/finish (etl)',
    (SELECT count(*) FROM fn) = 5
  UNION ALL SELECT 3, 'one active request per object (partial unique index)',
    EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
             WHERE i.indrelid = 'etl.object_run_request'::regclass AND i.indisunique AND i.indpred IS NOT NULL
               AND c.relname = 'object_run_request_active')
  UNION ALL SELECT 4, 'functions: SECURITY DEFINER with fixed search_path',
    NOT EXISTS (SELECT 1 FROM fn WHERE NOT (prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')))
  UNION ALL SELECT 5, 'create/cancel: EXECUTE only for owner and dwh_app',
    (SELECT bool_and(has_function_privilege('dwh_app', oid, 'EXECUTE')) FROM fn WHERE nspname = 'app')
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE fn.nspname = 'app' AND x.grantee NOT IN (fn.proowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app')))
  UNION ALL SELECT 6, 'claim/start/finish: EXECUTE only for owner and dwh_etl',
    (SELECT bool_and(has_function_privilege('dwh_etl', oid, 'EXECUTE')) FROM fn WHERE nspname = 'etl')
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE fn.nspname = 'etl' AND x.grantee NOT IN (fn.proowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_etl')))
  UNION ALL SELECT 7, 'queue table: no grants except owner',
    NOT EXISTS (SELECT 1 FROM rel, aclexplode(coalesce(rel.relacl, acldefault('r', rel.relowner))) x
                 WHERE rel.relname = 'object_run_request' AND x.grantee <> rel.relowner)
  UNION ALL SELECT 8, 'status view: SELECT only for owner and dwh_app',
    has_table_privilege('dwh_app', 'etl.v_object_load_status', 'SELECT')
    AND NOT EXISTS (SELECT 1 FROM rel, aclexplode(coalesce(rel.relacl, acldefault('r', rel.relowner))) x
                     WHERE rel.relname = 'v_object_load_status'
                       AND (x.grantee NOT IN (rel.relowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app'))
                            OR (x.grantee <> rel.relowner AND x.privilege_type <> 'SELECT')))
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
