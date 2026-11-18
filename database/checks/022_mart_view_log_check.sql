-- I22 check of 022: read-only catalog verification of the mart view log.
-- SELECT only; pg_catalog. Runs as dwh_app or as the owner.
-- Expected: 6 results, every one PASS. Check 016 stays 10/10, check 017 stays 8/8.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH fn AS (SELECT p.oid, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'app' AND p.proname LIKE 'mart\_view\_%'),
log AS (SELECT 'app.ai_request_log'::regclass AS oid),
checks AS (
  SELECT 1 AS n, 'five functions mart_view_*: SECURITY DEFINER with fixed search_path' AS check_name,
    ((SELECT count(*) FROM fn) = 5
    AND NOT EXISTS (SELECT 1 FROM fn WHERE NOT (prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')))) AS ok
  UNION ALL SELECT 2, 'functions: EXECUTE for dwh_app on 4 calls, the session helper owner only, nothing for others',
    (SELECT count(*) FROM fn WHERE has_function_privilege('dwh_app', oid, 'EXECUTE')) = 4
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE x.grantee <> fn.proowner
                       AND (fn.proname = 'mart_view_session_user'
                            OR x.grantee <> (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app')))
  UNION ALL SELECT 3, 'request log: kinds HELP/REPORT/SAVED_REPORT/MART_VIEW under the name ai_request_log_kind',
    EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = (SELECT oid FROM log) AND conname = 'ai_request_log_kind'
             AND pg_get_constraintdef(oid) LIKE '%SAVED_REPORT%' AND pg_get_constraintdef(oid) LIKE '%MART_VIEW%')
    AND (SELECT count(*) FROM pg_constraint WHERE conrelid = (SELECT oid FROM log) AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%request_kind%') = 2
  UNION ALL SELECT 4, 'request log: column view_detail jsonb with its size check',
    EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = (SELECT oid FROM log) AND attname = 'view_detail'
             AND NOT attisdropped AND atttypid = 'jsonb'::regtype)
    AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = (SELECT oid FROM log) AND contype = 'c'
                 AND pg_get_constraintdef(oid) LIKE '%view_detail%' AND pg_get_constraintdef(oid) LIKE '%8000%')
  UNION ALL SELECT 5, 'dwh_app cannot read or change the request log directly',
    NOT has_table_privilege('dwh_app', (SELECT oid FROM log), 'SELECT, INSERT, UPDATE, DELETE')
  UNION ALL SELECT 6, 'dwh_ai_ro (if present) has no access to the log and no EXECUTE on mart_view_*',
    NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dwh_ai_ro')
    OR (NOT has_table_privilege('dwh_ai_ro', (SELECT oid FROM log), 'SELECT')
        AND NOT EXISTS (SELECT 1 FROM fn WHERE has_function_privilege('dwh_ai_ro', oid, 'EXECUTE')))
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
