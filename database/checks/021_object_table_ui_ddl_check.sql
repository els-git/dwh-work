-- I18 check of 021: STG and ODS tables are created and changed on publication from the UI.
-- SELECT only, catalog only. Runs as dwh_app (dwh_sql_package.py) or as the owner.
-- Expected: 8 results, every one PASS. Check 020 stays 8/8.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH f AS (
  SELECT p.oid, p.proname, n.nspname, p.prosecdef, p.proconfig, p.proowner, p.proacl
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname IN ('meta', 'app') AND p.proname LIKE 'object\_table\_%'
), owner AS (
  SELECT proowner FROM f WHERE nspname = 'app' AND proname = 'object_table_publish'
), checks AS (
  SELECT 1 AS n, 'functions object_table_*: 7 in meta, 2 in app' AS check_name,
    (SELECT count(*) FROM f WHERE nspname = 'meta') = 7 AND (SELECT count(*) FROM f WHERE nspname = 'app') = 2 AS ok
  UNION ALL SELECT 2, 'app.object_table_preview / _publish: SECURITY DEFINER with a fixed search_path',
    NOT EXISTS (SELECT 1 FROM f WHERE nspname = 'app'
                 AND (NOT prosecdef OR NOT EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search\_path=%')))
  UNION ALL SELECT 3, 'no function object_table_* is executable by PUBLIC',
    NOT EXISTS (SELECT 1 FROM f, aclexplode(coalesce(f.proacl, acldefault('f', f.proowner))) x WHERE x.grantee = 0)
  UNION ALL SELECT 4, 'dwh_app may EXECUTE only the two app functions',
    (SELECT count(*) FROM f WHERE has_function_privilege('dwh_app', f.oid, 'EXECUTE')) = 2
    AND NOT EXISTS (SELECT 1 FROM f WHERE nspname = 'meta' AND has_function_privilege('dwh_app', f.oid, 'EXECUTE'))
  UNION ALL SELECT 5, 'publication functions of 008 and 012 are in place',
    to_regprocedure('app.integration_object_config_publish(text, bigint, integer)') IS NOT NULL
    AND to_regprocedure('app.integration_ods_config_publish(text, bigint, integer)') IS NOT NULL
  UNION ALL SELECT 6, 'schemas stg and ods are owned by the owner of the functions',
    (SELECT count(*) FROM pg_namespace n WHERE n.nspname IN ('stg', 'ods') AND n.nspowner = (SELECT proowner FROM owner)) = 2
  UNION ALL SELECT 7, 'every table of stg and ods is owned by the owner of the functions (can be changed from the UI)',
    NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname IN ('stg', 'ods') AND c.relkind IN ('r', 'p') AND c.relowner <> (SELECT proowner FROM owner))
  UNION ALL SELECT 8, 'default privileges of the owner in stg and ods let dwh_etl SELECT, INSERT, UPDATE, DELETE new tables',
    (SELECT count(*) FROM pg_namespace n
      WHERE n.nspname IN ('stg', 'ods')
        AND (SELECT count(DISTINCT x.privilege_type) FROM pg_default_acl d, aclexplode(d.defaclacl) x
              WHERE d.defaclrole = (SELECT proowner FROM owner) AND d.defaclobjtype = 'r'
                AND d.defaclnamespace IN (n.oid, 0) AND x.grantee = 'dwh_etl'::regrole
                AND x.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')) = 4) = 2
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
