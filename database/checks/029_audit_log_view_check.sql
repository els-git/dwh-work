-- I13-05 check of 029: read-only catalog verification, runs as dwh_app. Expected: 6 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH f AS (SELECT p.oid, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner
             FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'app' AND p.proname IN ('audit_admin_session_user', 'audit_mask_details', 'audit_log_page',
                                                      'audit_log_filters', 'secret_journal_page')),
pub AS (SELECT f.proname FROM f, aclexplode(coalesce(f.proacl, acldefault('f', f.proowner))) x WHERE x.grantee = 0),
checks AS (
  SELECT 1 AS n, '5 functions of 029 present' AS check_name, (SELECT count(*) FROM f) = 5 AS ok
  UNION ALL SELECT 2, 'session, page and filter functions are SECURITY DEFINER with search_path',
    (SELECT count(*) FROM f WHERE proname <> 'audit_mask_details' AND prosecdef
        AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) = 4
  UNION ALL SELECT 3, 'no EXECUTE for PUBLIC', NOT EXISTS (SELECT 1 FROM pub)
  UNION ALL SELECT 4, 'dwh_app executes the page and filter functions',
    (SELECT count(*) FROM f WHERE proname IN ('audit_log_page', 'audit_log_filters', 'secret_journal_page')
        AND has_function_privilege('dwh_app', oid, 'EXECUTE')) = 3
  UNION ALL SELECT 5, 'dwh_app does not execute the session and mask functions',
    NOT EXISTS (SELECT 1 FROM f WHERE proname IN ('audit_admin_session_user', 'audit_mask_details')
                 AND has_function_privilege('dwh_app', oid, 'EXECUTE'))
  UNION ALL SELECT 6, 'dwh_app still cannot read the journal tables',
    (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE (n.nspname, c.relname) IN (('audit', 'security_event'), ('secret_store', 'operation_event'))) = 2
    AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE (n.nspname, c.relname) IN (('audit', 'security_event'), ('secret_store', 'operation_event'))
                       AND has_table_privilege('dwh_app', c.oid, 'SELECT'))
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
