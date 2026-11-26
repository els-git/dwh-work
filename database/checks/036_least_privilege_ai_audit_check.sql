-- I13 check of 036: read-only catalog verification, runs as dwh_app. Expected: 7 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH names(n) AS (VALUES ('dq.issue'), ('dq.result'), ('dq.rule'), ('dq.run'), ('dq.run_rule_snapshot'),
    ('etl.batch_run'), ('etl.error_log'), ('etl.job'), ('etl.job_run'), ('etl.run_request'), ('etl.watermark'),
    ('meta.batch_contract_snapshot'), ('meta.batch_mapping_snapshot'), ('meta.column_contract'), ('meta.mapping_rule'),
    ('meta.mapping_source_ref'), ('meta.metadata_capture'), ('meta.source_column_snapshot'), ('meta.source_object'),
    ('meta.source_object_snapshot'), ('meta.source_system')),
t AS (SELECT c.oid FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace JOIN names ON names.n = s.nspname || '.' || c.relname),
h AS (SELECT c.oid FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
       WHERE s.nspname = 'etl' AND c.relname IN ('object_record_change', 'object_ods_record_change')),
lf AS (SELECT p.oid FROM pg_proc p JOIN pg_namespace s ON s.oid = p.pronamespace
        WHERE (s.nspname = 'app' AND p.proname IN ('integration_contract_create', 'integration_contract_set_active',
                 'integration_contract_version', 'integration_dq_rule_create', 'integration_dq_rule_set_active',
                 'integration_dq_rule_version', 'integration_mapping_create', 'integration_mapping_set_active',
                 'integration_mapping_version', 'integration_run_request_create', 'integration_run_request_cancel'))
           OR (s.nspname = 'etl' AND p.proname IN ('integration_run_request_claim', 'integration_run_request_fail',
                 'integration_run_request_start', 'integration_run_request_success'))),
f AS (SELECT p.oid, p.prosecdef, p.proconfig FROM pg_proc p JOIN pg_namespace s ON s.oid = p.pronamespace
        WHERE s.nspname = 'audit' AND p.proname = 'audit_ai_settings' AND p.pronargs = 0),
checks AS (
  SELECT 1 AS n, 'audit.audit_ai_settings present, SECURITY DEFINER with search_path' AS check_name,
    (SELECT count(*) FROM f WHERE prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) = 1 AS ok
  UNION ALL SELECT 2, 'audit trigger on the 4 assistant settings tables',
    (SELECT count(*) FROM pg_trigger tg WHERE NOT tg.tgisinternal
        AND tg.tgfoid IN (SELECT oid FROM f)
        AND tg.tgrelid IN (to_regclass('meta.ai_report_object'), to_regclass('meta.ai_value_column'),
                           to_regclass('meta.ai_glossary'), to_regclass('meta.ai_example'))) = 4
  UNION ALL SELECT 3, 'dwh_etl cannot insert, update, delete or truncate the 21 old tables',
    NOT EXISTS (SELECT 1 FROM t WHERE has_table_privilege('dwh_etl', t.oid, 'INSERT,UPDATE,DELETE,TRUNCATE'))
  UNION ALL SELECT 4, 'record history: dwh_etl may only INSERT',
    NOT EXISTS (SELECT 1 FROM h WHERE NOT has_table_privilege('dwh_etl', h.oid, 'INSERT')
                                   OR has_table_privilege('dwh_etl', h.oid, 'UPDATE,DELETE,TRUNCATE'))
  UNION ALL SELECT 5, 'functions of the old CRM contour: no EXECUTE for dwh_app, dwh_etl',
    NOT EXISTS (SELECT 1 FROM lf WHERE has_function_privilege('dwh_app', lf.oid, 'EXECUTE')
                                    OR has_function_privilege('dwh_etl', lf.oid, 'EXECUTE'))
  UNION ALL SELECT 6, 'working functions still callable (run request, run begin, file warning)',
    has_function_privilege('dwh_app', 'app.integration_object_run_request_create(text, bigint, text)', 'EXECUTE')
    AND has_function_privilege('dwh_etl', 'etl.object_run_begin(bigint)', 'EXECUTE')
    AND has_function_privilege('dwh_etl', 'etl.object_file_warning(bigint, text, text, text, text)', 'EXECUTE')
  UNION ALL SELECT 7, 'list of taken privileges kept, not readable by dwh_app',
    EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
             WHERE s.nspname = 'audit' AND c.relname = 'revoked_grant_036'
               AND NOT has_table_privilege('dwh_app', c.oid, 'SELECT')))
SELECT n, check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
