-- I06 check of 010: read-only catalog verification of the object run model.
-- SELECT only; pg_catalog only. Runs as dwh_app (run_readonly_sql.ps1) or as the owner.
-- Expected: 9 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH t AS (SELECT c.oid, c.relname, c.relacl, c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'etl' AND c.relkind IN ('r', 'S')
              AND (c.relname IN ('object_run', 'object_record_change')
                   OR c.relname IN ('object_run_run_id_seq', 'object_record_change_change_id_seq'))),
api AS (SELECT p.oid, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'etl' AND p.proname IN ('object_run_begin', 'object_run_finish')),
helpers AS (SELECT p.oid, p.proacl, p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'etl' AND p.proname IN ('object_run_close', 'object_record_change_guard')),
etl_role AS (SELECT oid FROM pg_roles WHERE rolname = 'dwh_etl'),
v AS (SELECT c.oid, pg_get_viewdef(c.oid) AS def FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE n.nspname = 'etl' AND c.relname = 'v_integration_run_history'),
checks AS (
  SELECT 1 AS n, '2 tables and 2 sequences present' AS check_name, (SELECT count(*) FROM t) = 4 AS ok
  UNION ALL SELECT 2, 'object_run references batch_run, job_run, object version (FK)',
    (SELECT count(DISTINCT confrelid) FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'etl' AND c.relname = 'object_run' AND k.contype = 'f'
        AND k.confrelid::regclass::text IN ('etl.batch_run', 'etl.job_run', 'meta.object_config_version')) = 3
  UNION ALL SELECT 3, 'record history append-only triggers enabled (3)',
    (SELECT count(*) FROM pg_trigger g JOIN pg_class c ON c.oid = g.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'etl' AND c.relname = 'object_record_change' AND NOT g.tgisinternal AND g.tgenabled = 'O') = 3
  UNION ALL SELECT 4, '2 API functions SECURITY DEFINER with fixed search_path',
    (SELECT count(*) FROM api WHERE prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) = 2
  UNION ALL SELECT 5, 'API functions: EXECUTE only for owner and dwh_etl',
    (SELECT count(*) FROM api WHERE has_function_privilege('dwh_etl', oid, 'EXECUTE')) = 2
    AND NOT EXISTS (SELECT 1 FROM api, aclexplode(coalesce(api.proacl, acldefault('f', api.proowner))) x
                     WHERE x.grantee NOT IN (api.proowner, (SELECT oid FROM etl_role)))
  UNION ALL SELECT 6, 'helper functions: no grants beyond owner',
    (SELECT count(*) FROM helpers) = 2
    AND NOT EXISTS (SELECT 1 FROM helpers, aclexplode(coalesce(helpers.proacl, acldefault('f', helpers.proowner))) x
                     WHERE x.grantee <> helpers.proowner)
  UNION ALL SELECT 7, 'tables: only INSERT on object_record_change for dwh_etl, nothing else beyond owner',
    NOT EXISTS (SELECT 1 FROM t, aclexplode(coalesce(t.relacl, acldefault((CASE WHEN t.relname LIKE '%seq' THEN 's' ELSE 'r' END)::"char", t.relowner))) x
                 WHERE x.grantee <> t.relowner
                   AND NOT (t.relname = 'object_record_change' AND x.grantee = (SELECT oid FROM etl_role)
                            AND x.privilege_type = 'INSERT'))
    AND (SELECT has_table_privilege('dwh_etl', oid, 'INSERT') FROM t WHERE relname = 'object_record_change')
  UNION ALL SELECT 8, 'run history view shows ODS_TRANSFORM and OBJECT_LOAD',
    (SELECT def LIKE '%''ODS_TRANSFORM''%' AND def LIKE '%''OBJECT_LOAD''%' FROM v)
  UNION ALL SELECT 9, 'run history view: 21 columns, dwh_app may read',
    (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = (SELECT oid FROM v) AND a.attnum > 0 AND NOT a.attisdropped) = 21
    AND (SELECT has_table_privilege('dwh_app', oid, 'SELECT') FROM v)
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
