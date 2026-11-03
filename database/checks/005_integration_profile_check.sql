-- I03 check of 005: read-only catalog verification of the profile catalog,
-- its API functions and grants. Plain SQL; SELECT only; pg_catalog only.
-- Runs as dwh_app (run_readonly_sql.ps1) or as the owner.
-- Expected: 12 results, every one PASS. The 004 check stays valid (23/23).
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH t AS (SELECT c.oid, c.relacl, c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'meta' AND c.relname = 'integration_profile' AND c.relkind = 'r'),
api AS (SELECT p.oid, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'app' AND p.proname IN ('integration_profile_list', 'integration_profile_create',
               'integration_profile_update', 'integration_profile_set_status', 'integration_profile_secret_begin',
               'integration_profile_secret_complete', 'integration_profile_secret_revoke')),
scope AS (SELECT p.oid, p.proacl, p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = 'app' AND p.proname = 'integration_profile_secret_scope'),
mf AS (SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'meta' AND p.proname IN ('integration_config_has_secret_key', 'integration_profile_guard',
              'integration_profile_reject_truncate')),
checks AS (
  SELECT 1 AS n, 'table meta.integration_profile exists' AS check_name, (SELECT count(*) FROM t) = 1 AS ok
  UNION ALL SELECT 2, 'profile constraints = 10',
    (SELECT count(*) FROM pg_constraint WHERE conrelid = (SELECT oid FROM t)) = 10
  UNION ALL SELECT 3, 'unique (source_system_id, profile_code) and FK to meta.source_system',
    EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = (SELECT oid FROM t) AND conname = 'integration_profile_code_per_system' AND contype = 'u')
    AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = (SELECT oid FROM t) AND contype = 'f'
                 AND confrelid = to_regclass('meta.source_system'))
  UNION ALL SELECT 4, 'settings may not contain secrets (CHECK)',
    EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = (SELECT oid FROM t) AND conname = 'integration_profile_config_no_secrets' AND convalidated)
  UNION ALL SELECT 5, 'guard and TRUNCATE triggers enabled',
    (SELECT count(*) FROM pg_trigger WHERE tgrelid = (SELECT oid FROM t) AND NOT tgisinternal AND tgenabled = 'O'
       AND tgname IN ('integration_profile_guard', 'integration_profile_truncate_guard')) = 2
  UNION ALL SELECT 6, '3 meta helper functions present', (SELECT count(*) FROM mf) = 3
  UNION ALL SELECT 7, '7 API functions + scope check present', (SELECT count(*) FROM api) = 7 AND (SELECT count(*) FROM scope) = 1
  UNION ALL SELECT 8, 'API functions SECURITY DEFINER with fixed search_path',
    (SELECT count(*) FROM api WHERE prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) = 7
  UNION ALL SELECT 9, 'dwh_app may EXECUTE exactly the 7 API functions',
    (SELECT count(*) FROM api WHERE has_function_privilege('dwh_app', oid, 'EXECUTE')) = 7
  UNION ALL SELECT 10, 'no PUBLIC EXECUTE on API functions; scope check not granted',
    NOT EXISTS (SELECT 1 FROM api, aclexplode(coalesce(api.proacl, acldefault('f', api.proowner))) x WHERE x.grantee = 0)
    AND NOT EXISTS (SELECT 1 FROM scope, aclexplode(coalesce(scope.proacl, acldefault('f', scope.proowner))) x WHERE x.grantee <> scope.proowner)
  UNION ALL SELECT 11, 'profile table: no grants beyond owner',
    NOT EXISTS (SELECT 1 FROM t, aclexplode(coalesce(t.relacl, acldefault('r', t.relowner))) x WHERE x.grantee <> t.relowner)
  UNION ALL SELECT 12, 'dwh_app has no USAGE on schema secret_store',
    NOT has_schema_privilege('dwh_app', 'secret_store', 'USAGE')
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
