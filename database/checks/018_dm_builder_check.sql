-- I19 check of 018: read-only catalog verification of the DM mart builder.
-- SELECT only; pg_catalog only. Runs as dwh_app (run_readonly_sql.ps1 / dwh_sql_package.py) or as the owner.
-- Expected: 9 results, every one PASS. The 015-017 checks are not affected.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH rel AS (SELECT c.oid, n.nspname, c.relname, c.relkind, c.relacl, c.relowner FROM pg_class c
               JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE (n.nspname = 'meta' AND c.relname IN ('dm_entity', 'dm_entity_key', 'dm_mart', 'dm_mart_version',
                       'dm_mart_source', 'dm_mart_field', 'dm_mart_field_source', 'dm_mart_filter'))
                 OR (n.nspname = 'etl' AND c.relname IN ('dm_mart_run_request', 'dm_mart_run', 'v_dm_mart_status'))),
fn AS (SELECT p.oid, n.nspname, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner
         FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE (n.nspname IN ('meta', 'app') AND p.proname LIKE 'dm\_%')
           OR (n.nspname = 'etl' AND p.proname LIKE 'dm\_mart\_%')),
checks AS (
  SELECT 1 AS n, '8 meta tables, 2 etl tables, view etl.v_dm_mart_status' AS check_name,
    (SELECT count(*) FROM rel WHERE nspname = 'meta' AND relkind = 'r') = 8
    AND (SELECT count(*) FROM rel WHERE nspname = 'etl' AND relkind = 'r') = 2
    AND (SELECT count(*) FROM rel WHERE relname = 'v_dm_mart_status' AND relkind = 'v') = 1 AS ok
  UNION ALL SELECT 2, '23 functions: 8 meta helpers, 11 app, 4 etl',
    (SELECT count(*) FROM fn WHERE nspname = 'meta') = 8 AND (SELECT count(*) FROM fn WHERE nspname = 'app') = 11
    AND (SELECT count(*) FROM fn WHERE nspname = 'etl') = 4
  UNION ALL SELECT 3, 'one PUBLISHED and one DRAFT version per mart, one GRAIN source and one key field per version',
    (SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
      WHERE i.indisunique AND i.indpred IS NOT NULL
        AND c.relname IN ('dm_mart_version_one_published', 'dm_mart_version_one_draft', 'dm_mart_source_one_grain',
                          'dm_mart_field_one_key', 'dm_mart_run_request_queued')) = 5
  UNION ALL SELECT 4, 'whitelists: source kinds, aggregates, filter operators, field types',
    (SELECT count(*) FROM pg_constraint k WHERE k.contype = 'c'
      AND k.conrelid IN ('meta.dm_mart_source'::regclass, 'meta.dm_mart_field_source'::regclass,
                         'meta.dm_mart_filter'::regclass, 'meta.dm_mart_field'::regclass)
      AND (pg_get_constraintdef(k.oid) LIKE '%LOOKUP%' OR pg_get_constraintdef(k.oid) LIKE '%COUNT_DISTINCT%'
           OR pg_get_constraintdef(k.oid) LIKE '%IS NOT NULL''%' OR pg_get_constraintdef(k.oid) LIKE '%timestamptz%')) >= 4
  UNION ALL SELECT 5, 'app/etl functions: SECURITY DEFINER with fixed search_path; meta helpers: not SECURITY DEFINER',
    NOT EXISTS (SELECT 1 FROM fn WHERE nspname IN ('app', 'etl')
                 AND NOT (prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')))
    AND NOT EXISTS (SELECT 1 FROM fn WHERE nspname = 'meta' AND prosecdef)
  UNION ALL SELECT 6, 'app functions: EXECUTE only for owner and dwh_app; meta helpers: owner only',
    (SELECT bool_and(has_function_privilege('dwh_app', oid, 'EXECUTE')) FROM fn WHERE nspname = 'app')
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE (fn.nspname = 'app' AND x.grantee NOT IN (fn.proowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app')))
                        OR (fn.nspname = 'meta' AND x.grantee <> fn.proowner))
  UNION ALL SELECT 7, 'etl functions: EXECUTE only for owner and dwh_etl',
    (SELECT bool_and(has_function_privilege('dwh_etl', oid, 'EXECUTE')) FROM fn WHERE nspname = 'etl')
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE fn.nspname = 'etl' AND x.grantee NOT IN (fn.proowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_etl')))
  UNION ALL SELECT 8, 'meta tables: only SELECT for dwh_app and dwh_etl; etl tables: owner only',
    NOT EXISTS (SELECT 1 FROM rel, aclexplode(coalesce(rel.relacl, acldefault('r', rel.relowner))) x
                 WHERE rel.relkind = 'r' AND x.grantee <> rel.relowner
                   AND (rel.nspname = 'etl' OR x.privilege_type <> 'SELECT'
                        OR x.grantee NOT IN (SELECT oid FROM pg_roles WHERE rolname IN ('dwh_app', 'dwh_etl'))))
    AND (SELECT bool_and(has_table_privilege('dwh_app', oid, 'SELECT') AND has_table_privilege('dwh_etl', oid, 'SELECT'))
           FROM rel WHERE nspname = 'meta')
  UNION ALL SELECT 9, 'status view: SELECT only for owner and dwh_app',
    has_table_privilege('dwh_app', 'etl.v_dm_mart_status', 'SELECT')
    AND NOT EXISTS (SELECT 1 FROM rel, aclexplode(coalesce(rel.relacl, acldefault('r', rel.relowner))) x
                     WHERE rel.relname = 'v_dm_mart_status'
                       AND (x.grantee NOT IN (rel.relowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app'))
                            OR (x.grantee <> rel.relowner AND x.privilege_type <> 'SELECT')))
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
