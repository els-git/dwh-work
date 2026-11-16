-- I17-09 check of 017: read-only catalog verification of saved reports of the AI assistant.
-- SELECT only; pg_catalog. Runs as dwh_app or as the owner.
-- Expected: 8 results, every one PASS. Check 016 stays 10/10 (new functions are not named ai_*).
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH rel AS (SELECT c.oid, c.relname, c.relkind, c.relacl, c.relowner
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE n.nspname = 'app' AND c.relname IN ('report_folder', 'saved_report')),
seq AS (SELECT c.oid, c.relacl, c.relowner FROM pg_class c JOIN pg_depend d ON d.objid = c.oid
         WHERE c.relkind = 'S' AND d.refobjid IN (SELECT oid FROM rel) AND d.deptype IN ('a', 'i')),
fn AS (SELECT p.oid, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner
         FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'app' AND (p.proname LIKE 'saved\_report\_%' OR p.proname LIKE 'report\_folder\_%')),
helpers AS (SELECT unnest(ARRAY['saved_report_session_user', 'saved_report_is_admin', 'saved_report_objects_allowed',
                                'saved_report_folder_visible']) AS proname),
log AS (SELECT 'app.ai_request_log'::regclass AS oid),
checks AS (
  SELECT 1 AS n, 'two tables: app.report_folder, app.saved_report' AS check_name,
    (SELECT count(*) FROM rel WHERE relkind = 'r') = 2 AS ok
  UNION ALL SELECT 2, 'fourteen functions report_folder_*/saved_report_*: SECURITY DEFINER with fixed search_path',
    (SELECT count(*) FROM fn) = 14
    AND NOT EXISTS (SELECT 1 FROM fn WHERE NOT (prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')))
  UNION ALL SELECT 3, 'tables and their sequences: no privileges except the owner',
    NOT EXISTS (SELECT 1 FROM rel, aclexplode(coalesce(rel.relacl, acldefault('r', rel.relowner))) x WHERE x.grantee <> rel.relowner)
    AND NOT EXISTS (SELECT 1 FROM seq, aclexplode(coalesce(seq.relacl, acldefault('s', seq.relowner))) x WHERE x.grantee <> seq.relowner)
  UNION ALL SELECT 4, 'functions: EXECUTE for dwh_app on 10 calls, 4 helpers owner only, nothing for others',
    (SELECT count(*) FROM fn WHERE has_function_privilege('dwh_app', oid, 'EXECUTE')) = 10
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE x.grantee <> fn.proowner
                       AND (fn.proname IN (SELECT proname FROM helpers)
                            OR x.grantee <> (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app')))
  UNION ALL SELECT 5, 'dwh_app and dwh_ai_ro cannot read the tables directly',
    NOT EXISTS (SELECT 1 FROM rel WHERE has_table_privilege('dwh_app', rel.oid, 'SELECT, INSERT, UPDATE, DELETE'))
    AND NOT EXISTS (SELECT 1 FROM rel WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dwh_ai_ro')
                                        AND has_table_privilege('dwh_ai_ro', rel.oid, 'SELECT'))
  UNION ALL SELECT 6, 'request log: kinds HELP/REPORT/SAVED_REPORT, saved_report_id with its constraint',
    EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = (SELECT oid FROM log) AND conname = 'ai_request_log_kind'
             AND pg_get_constraintdef(oid) LIKE '%SAVED_REPORT%')
    AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = (SELECT oid FROM log) AND conname = 'ai_request_log_saved_report')
    AND (SELECT count(*) FROM pg_constraint WHERE conrelid = (SELECT oid FROM log) AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%request_kind%') = 2
  UNION ALL SELECT 7, 'foreign keys: report -> folder, report -> request, request -> report (RESTRICT)',
    (SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND confdeltype = 'r'
       AND ((conrelid = 'app.saved_report'::regclass AND confrelid IN ('app.report_folder'::regclass, 'app.ai_request_log'::regclass))
            OR (conrelid = (SELECT oid FROM log) AND confrelid = 'app.saved_report'::regclass))) = 3
  UNION ALL SELECT 8, 'unique active folder names: shared, and personal per owner',
    (SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
      WHERE i.indrelid = 'app.report_folder'::regclass AND i.indisunique AND i.indpred IS NOT NULL
        AND c.relname IN ('report_folder_shared_name', 'report_folder_personal_name')) = 2
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
