-- I07 check of 011: read-only catalog verification of the load strategy model.
-- SELECT only; pg_catalog only. Runs as dwh_app (run_readonly_sql.ps1) or as the owner.
-- Expected: 8 results, every one PASS. The 008 and 010 checks stay valid.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH cols AS (SELECT c.relname, a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
               JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE a.attnum > 0 AND NOT a.attisdropped
                AND ((n.nspname = 'meta' AND c.relname = 'object_config_version')
                     OR (n.nspname = 'etl' AND c.relname = 'object_run'))),
fn AS (SELECT p.oid, p.proname, p.prosrc, p.prosecdef, p.proconfig, p.proacl, p.proowner, n.nspname
         FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE (n.nspname = 'etl' AND p.proname IN ('object_run_begin', 'object_run_record_increment'))
           OR (n.nspname = 'app' AND p.proname IN ('integration_object_config_save_draft', 'integration_object_config_publish'))
           OR (n.nspname = 'meta' AND p.proname = 'object_config_definition')),
checks AS (
  SELECT 1 AS n, 'version: load_strategy, change_column, overlap_minutes, delete_flag_column' AS check_name,
    (SELECT count(*) FROM cols WHERE relname = 'object_config_version'
       AND attname IN ('load_strategy', 'change_column', 'overlap_minutes', 'delete_flag_column')) = 4 AS ok
  UNION ALL SELECT 2, 'object_run: watermark_from, watermark_to, rows_stale, rows_deleted',
    (SELECT count(*) FROM cols WHERE relname = 'object_run'
       AND attname IN ('watermark_from', 'watermark_to', 'rows_stale', 'rows_deleted')) = 4
  UNION ALL SELECT 3, 'strategy check constraint FULL / INCREMENTAL',
    EXISTS (SELECT 1 FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'meta' AND c.relname = 'object_config_version' AND k.contype = 'c'
               AND pg_get_constraintdef(k.oid) LIKE '%INCREMENTAL%')
  UNION ALL SELECT 4, 'record history allows DELETE',
    EXISTS (SELECT 1 FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'etl' AND c.relname = 'object_record_change'
               AND k.conname = 'object_record_change_change_kind_check' AND pg_get_constraintdef(k.oid) LIKE '%DELETE%')
  UNION ALL SELECT 5, 'definition, draft and publish handle the strategy; begin returns the watermark',
    (SELECT prosrc LIKE '%load_strategy%' FROM fn WHERE proname = 'object_config_definition')
    AND (SELECT prosrc LIKE '%load_strategy%' FROM fn WHERE proname = 'integration_object_config_save_draft')
    AND (SELECT prosrc LIKE '%requires a change column%' FROM fn WHERE proname = 'integration_object_config_publish')
    AND (SELECT prosrc LIKE '%watermark_to%' FROM fn WHERE proname = 'object_run_begin')
  UNION ALL SELECT 6, 'record_increment: SECURITY DEFINER with fixed search_path',
    (SELECT prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')
       FROM fn WHERE proname = 'object_run_record_increment')
  UNION ALL SELECT 7, 'record_increment: EXECUTE only for owner and dwh_etl',
    (SELECT has_function_privilege('dwh_etl', oid, 'EXECUTE') FROM fn WHERE proname = 'object_run_record_increment')
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE fn.proname = 'object_run_record_increment'
                       AND x.grantee NOT IN (fn.proowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_etl')))
  UNION ALL SELECT 8, 'replaced functions keep their grants (dwh_app draft/publish, dwh_etl begin)',
    (SELECT has_function_privilege('dwh_app', oid, 'EXECUTE') FROM fn WHERE proname = 'integration_object_config_save_draft')
    AND (SELECT has_function_privilege('dwh_app', oid, 'EXECUTE') FROM fn WHERE proname = 'integration_object_config_publish')
    AND (SELECT has_function_privilege('dwh_etl', oid, 'EXECUTE') FROM fn WHERE proname = 'object_run_begin')
    AND NOT (SELECT has_function_privilege('dwh_app', oid, 'EXECUTE') FROM fn WHERE proname = 'object_run_begin')
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
