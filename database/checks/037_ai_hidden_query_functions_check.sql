-- I14-07 check of 037: read-only catalog verification, runs as dwh_app. Expected: 5 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH f AS (SELECT p.oid, p.proname FROM pg_proc p
            WHERE p.pronamespace = 'pg_catalog'::regnamespace
              AND (p.proname ~ '^(query|table|cursor|schema|database)_to_xml(schema|_and_xmlschema)?$'
                   OR p.proname IN ('ts_stat', 'ts_rewrite'))),
roles AS (SELECT oid, rolname FROM pg_roles WHERE rolname IN ('dwh_app', 'dwh_etl', 'dwh_ai_ro', 'dwh_dm_sql_ro')),
checks AS (
  SELECT 1 AS n, 'audit.revoked_grant_037 present' AS check_name,
    EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
             WHERE s.nspname = 'audit' AND c.relname = 'revoked_grant_037') AS ok
  UNION ALL SELECT 2, 'all 16 function names found (to_xml families, ts_stat, ts_rewrite)',
    (SELECT count(DISTINCT proname) FROM f) = 16
  UNION ALL SELECT 3, 'PUBLIC has no EXECUTE on them',
    NOT EXISTS (SELECT 1 FROM f JOIN pg_proc p ON p.oid = f.oid,
                       aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) x
                 WHERE x.grantee = 0 AND x.privilege_type = 'EXECUTE')
  UNION ALL SELECT 4, 'working roles cannot execute them',
    NOT EXISTS (SELECT 1 FROM f, roles r WHERE has_function_privilege(r.oid, f.oid, 'EXECUTE'))
  UNION ALL SELECT 5, 'dwh_app (this session) cannot execute schema_to_xml',
    NOT has_function_privilege('pg_catalog.schema_to_xml(name, boolean, boolean, text)', 'EXECUTE')
)
SELECT n, check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
