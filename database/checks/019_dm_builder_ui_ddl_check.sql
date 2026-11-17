-- I19 check of 019: read-only catalog verification of DM marts managed from the UI.
-- SELECT only; pg_catalog only. Runs as dwh_app (dwh_sql_package.py) or as the owner.
-- Expected: 8 results, every one PASS. Check 018 stays 9/9.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH fn AS (SELECT p.oid, n.nspname, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner, p.prosrc
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname IN ('meta', 'app') AND p.proname LIKE 'mart\_builder\_%'),
eng AS (SELECT p.oid, p.prosrc, p.proacl, p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'etl' AND p.proname IN ('dm_mart_run_request_claim', 'dm_mart_run_begin', 'dm_mart_run_finish')),
col AS (SELECT a.attrelid::regclass::text AS rel, a.attname::text AS name FROM pg_attribute a
         WHERE a.attrelid IN ('meta.dm_mart'::regclass, 'meta.dm_mart_version'::regclass, 'etl.dm_mart_run_request'::regclass,
                              'etl.v_dm_mart_status'::regclass)
           AND a.attnum > 0 AND NOT a.attisdropped),
checks AS (
  SELECT 1 AS n, 'columns: dm_mart.deleted_at, dm_mart_version.build_status, dm_mart_run_request.version_id' AS check_name,
    EXISTS (SELECT 1 FROM col WHERE rel = 'meta.dm_mart' AND name = 'deleted_at')
    AND EXISTS (SELECT 1 FROM col WHERE rel = 'meta.dm_mart_version' AND name = 'build_status')
    AND EXISTS (SELECT 1 FROM col WHERE rel = 'etl.dm_mart_run_request' AND name = 'version_id') AS ok
  UNION ALL SELECT 2, '12 functions mart_builder_*: 9 meta, 3 app',
    (SELECT count(*) FROM fn WHERE nspname = 'meta') = 9 AND (SELECT count(*) FROM fn WHERE nspname = 'app') = 3
  UNION ALL SELECT 3, '5 guard triggers: deleted mart, versions being built',
    (SELECT count(*) FROM pg_trigger t WHERE NOT t.tgisinternal AND t.tgname IN ('dm_mart_deleted_guard',
       'dm_mart_version_deleted_guard', 'dm_mart_source_building_guard', 'dm_mart_field_building_guard',
       'dm_mart_filter_building_guard')) = 5
  UNION ALL SELECT 4, 'app functions: SECURITY DEFINER with fixed search_path; meta helpers: not SECURITY DEFINER',
    NOT EXISTS (SELECT 1 FROM fn WHERE nspname = 'app'
                 AND NOT (prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')))
    AND NOT EXISTS (SELECT 1 FROM fn WHERE nspname = 'meta' AND prosecdef)
  UNION ALL SELECT 5, 'app functions: EXECUTE only for owner and dwh_app; meta helpers: owner only',
    (SELECT bool_and(has_function_privilege('dwh_app', oid, 'EXECUTE')) FROM fn WHERE nspname = 'app')
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE (fn.nspname = 'app' AND x.grantee NOT IN (fn.proowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app')))
                        OR (fn.nspname = 'meta' AND x.grantee <> fn.proowner))
  UNION ALL SELECT 6, 'request kinds MANUAL / AFTER_ODS / PUBLISH',
    EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = 'etl.dm_mart_run_request'::regclass AND k.contype = 'c'
             AND pg_get_constraintdef(k.oid) LIKE '%PUBLISH%' AND pg_get_constraintdef(k.oid) LIKE '%AFTER_ODS%'
             AND k.conname = 'dm_mart_run_request_trigger_kind_check')
  UNION ALL SELECT 7, 'engine functions build and swap PUBLISH requests; EXECUTE only for owner and dwh_etl',
    (SELECT count(*) FROM eng WHERE prosrc LIKE '%PUBLISH%') = 3
    AND NOT EXISTS (SELECT 1 FROM eng, aclexplode(coalesce(eng.proacl, acldefault('f', eng.proowner))) x
                     WHERE x.grantee NOT IN (eng.proowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_etl')))
  UNION ALL SELECT 8, 'status view with build state; SELECT only for owner and dwh_app',
    EXISTS (SELECT 1 FROM col WHERE rel = 'etl.v_dm_mart_status' AND name = 'draft_build_status')
    AND EXISTS (SELECT 1 FROM col WHERE rel = 'etl.v_dm_mart_status' AND name = 'is_deleted')
    AND has_table_privilege('dwh_app', 'etl.v_dm_mart_status', 'SELECT')
    AND NOT EXISTS (SELECT 1 FROM pg_class c, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) x
                     WHERE c.oid = 'etl.v_dm_mart_status'::regclass
                       AND (x.grantee NOT IN (c.relowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app'))
                            OR (x.grantee <> c.relowner AND x.privilege_type <> 'SELECT')))
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
