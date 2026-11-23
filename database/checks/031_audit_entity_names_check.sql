-- I13-15 check of 031: read-only catalog verification, runs as dwh_app. Expected: 6 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH f AS (SELECT p.oid, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner, p.prosrc
             FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'app' AND p.proname IN ('audit_entity_name', 'audit_entity_search', 'audit_log_page')),
pub AS (SELECT f.proname FROM f, aclexplode(coalesce(f.proacl, acldefault('f', f.proowner))) x WHERE x.grantee = 0),
checks AS (
  SELECT 1 AS n, 'name helpers of 031 present (audit_entity_name, audit_entity_search)' AS check_name,
    (SELECT count(*) FROM f WHERE proname IN ('audit_entity_name', 'audit_entity_search')) = 2 AS ok
  UNION ALL SELECT 2, 'audit_log_page is one function, SECURITY DEFINER with search_path',
    (SELECT count(*) FROM f WHERE proname = 'audit_log_page' AND prosecdef
        AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) = 1
    AND (SELECT count(*) FROM f WHERE proname = 'audit_log_page') = 1
  UNION ALL SELECT 3, 'audit_log_page uses the name helpers',
    (SELECT count(*) FROM f WHERE proname = 'audit_log_page' AND prosrc LIKE '%app.audit_entity_name(%'
        AND prosrc LIKE '%app.audit_entity_search(%') = 1
  UNION ALL SELECT 4, 'no EXECUTE for PUBLIC', NOT EXISTS (SELECT 1 FROM pub)
  UNION ALL SELECT 5, 'dwh_app executes audit_log_page, not the name helpers',
    (SELECT count(*) FROM f WHERE proname = 'audit_log_page' AND has_function_privilege('dwh_app', oid, 'EXECUTE')) = 1
    AND NOT EXISTS (SELECT 1 FROM f WHERE proname IN ('audit_entity_name', 'audit_entity_search')
                     AND has_function_privilege('dwh_app', oid, 'EXECUTE'))
  UNION ALL SELECT 6, 'dwh_app still cannot read the journal tables',
    (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE (n.nspname, c.relname) IN (('audit', 'security_event'), ('secret_store', 'operation_event'))) = 2
    AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE (n.nspname, c.relname) IN (('audit', 'security_event'), ('secret_store', 'operation_event'))
                       AND has_table_privilege('dwh_app', c.oid, 'SELECT'))
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
