-- I02-06 check of 027: read-only catalog verification, runs as dwh_app (which cannot read secret_store: the state
-- of the bindings is verified by the test of 027, run as the owner). Expected: 3 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

-- functions are found by name in pg_proc (dwh_app has no USAGE on the schema, so no regprocedure casts)
WITH f AS (SELECT p.oid, p.proname, p.proacl, p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'secret_store'),
m AS (SELECT * FROM f WHERE proname = 'legacy_bindings_revoked'),
checks AS (
  SELECT 1 AS n, 'marker secret_store.legacy_bindings_revoked() present' AS check_name, EXISTS (SELECT 1 FROM m) AS ok
  UNION ALL SELECT 2, 'marker: no EXECUTE for PUBLIC',
    EXISTS (SELECT 1 FROM m) AND NOT EXISTS (SELECT 1 FROM m, aclexplode(coalesce(m.proacl, acldefault('f', m.proowner))) x
                                               WHERE x.grantee = 0)
  UNION ALL SELECT 3, 'write functions of secret_store still closed for dwh_app and dwh_etl',
    (SELECT count(*) FROM f WHERE proname IN ('begin_operation', 'finish_operation')) = 2
    AND NOT EXISTS (SELECT 1 FROM f WHERE proname IN ('begin_operation', 'finish_operation')
                     AND (has_function_privilege('dwh_app', oid, 'EXECUTE') OR has_function_privilege('dwh_etl', oid, 'EXECUTE')))
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
