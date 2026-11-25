-- I12 check of 035: read-only catalog verification, runs as dwh_app. Expected: 5 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH f AS (SELECT p.oid, p.prosecdef, p.proconfig, p.proacl, p.proowner FROM pg_proc p
            WHERE p.oid = to_regprocedure('etl.object_file_warning(bigint, text, text, text, text)')),
v AS (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE n.nspname = 'etl' AND c.relname = 'v_object_load_status' AND c.relkind = 'v'),
checks AS (
  SELECT 1 AS n, 'etl.object_file_warning present, SECURITY DEFINER with search_path' AS check_name,
    (SELECT count(*) FROM f WHERE prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) = 1 AS ok
  UNION ALL SELECT 2, 'EXECUTE on the function: dwh_etl only (not PUBLIC, not dwh_app)',
    EXISTS (SELECT 1 FROM f, aclexplode(f.proacl) a WHERE a.grantee = 'dwh_etl'::regrole AND a.privilege_type = 'EXECUTE')
    AND NOT EXISTS (SELECT 1 FROM f, aclexplode(coalesce(f.proacl, acldefault('f', f.proowner))) a
                     WHERE a.grantee IN (0, 'dwh_app'::regrole))
  UNION ALL SELECT 3, 'etl.v_object_load_status has file_errors as the last column',
    (SELECT a.attname FROM pg_attribute a, v WHERE a.attrelid = v.oid AND a.attnum > 0 AND NOT a.attisdropped
      ORDER BY a.attnum DESC LIMIT 1) = 'file_errors'
  UNION ALL SELECT 4, 'STG error message of the view ignores FILE_CHECK warnings',
    (SELECT pg_get_viewdef(v.oid) LIKE '%FILE_CHECK%' FROM v)
  UNION ALL SELECT 5, 'view readable by dwh_app; register function of 034 still present',
    has_table_privilege('dwh_app', 'etl.v_object_load_status', 'SELECT')
    AND to_regprocedure('etl.object_file_record(bigint, text, bigint, text, timestamp with time zone, text, bigint, text)') IS NOT NULL)
SELECT n, check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
