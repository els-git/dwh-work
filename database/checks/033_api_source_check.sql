-- I11 check of 033: read-only catalog verification, runs as dwh_app. Expected: 7 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH t AS (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'meta' AND c.relname = 'object_api_request' AND c.relkind = 'r'),
f AS (SELECT p.oid, n.nspname, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
       WHERE (n.nspname, p.proname) IN (
         ('app', 'integration_api_object_create'), ('app', 'integration_api_request_get'),
         ('app', 'integration_api_request_save'), ('app', 'integration_object_secret_begin'),
         ('app', 'integration_object_secret_complete'), ('app', 'integration_object_secret_revoke'),
         ('app', 'integration_object_secret_read_begin'), ('app', 'integration_object_secret_read_finish'),
         ('app', 'integration_api_object_capture'), ('etl', 'object_secret_read_begin'))),
helpers AS (SELECT p.oid, p.proacl, p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE (n.nspname, p.proname) IN (('app', 'integration_api_object'), ('meta', 'object_api_request_guard'),
                     ('meta', 'object_api_request_reject_truncate'), ('meta', 'object_api_request_copy_to_draft'))),
checks AS (
  SELECT 1 AS n, 'meta.source_object accepts object_type API' AS check_name,
    EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'meta.source_object'::regclass
             AND conname = 'source_object_object_type_check' AND pg_get_constraintdef(oid) LIKE '%API%') AS ok
  UNION ALL SELECT 2, 'table meta.object_api_request with no-credential and draft-only guards',
    (SELECT count(*) FROM t) = 1
    AND EXISTS (SELECT 1 FROM pg_constraint c, t WHERE c.conrelid = t.oid AND c.conname = 'object_api_request_no_secrets')
    AND (SELECT count(*) FROM pg_trigger g, t WHERE g.tgrelid = t.oid AND NOT g.tgisinternal
          AND g.tgname IN ('trg_object_api_request_guard', 'trg_object_api_request_no_truncate')) = 2
    AND EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'meta.object_config_version'::regclass
                 AND tgname = 'trg_object_api_request_copy')
  UNION ALL SELECT 3, '10 functions of 033 present, SECURITY DEFINER with search_path',
    (SELECT count(*) FROM f) = 10
    AND (SELECT count(*) FROM f WHERE prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) = 10
  UNION ALL SELECT 4, 'no EXECUTE for PUBLIC on the new functions',
    NOT EXISTS (SELECT 1 FROM (SELECT proacl, proowner FROM f UNION ALL SELECT proacl, proowner FROM helpers) x,
                aclexplode(coalesce(x.proacl, acldefault('f', x.proowner))) a WHERE a.grantee = 0)
  UNION ALL SELECT 5, 'dwh_app executes the 9 administration functions only',
    (SELECT count(*) FROM f WHERE nspname = 'app' AND has_function_privilege('dwh_app', oid, 'EXECUTE')) = 9
    AND NOT EXISTS (SELECT 1 FROM f WHERE nspname = 'etl' AND has_function_privilege('dwh_app', oid, 'EXECUTE'))
    AND NOT EXISTS (SELECT 1 FROM helpers WHERE has_function_privilege('dwh_app', oid, 'EXECUTE'))
  UNION ALL SELECT 6, 'dwh_etl executes etl.object_secret_read_begin and no administration function',
    (SELECT count(*) FROM f WHERE has_function_privilege('dwh_etl', oid, 'EXECUTE')) = 1
    AND EXISTS (SELECT 1 FROM f WHERE nspname = 'etl' AND has_function_privilege('dwh_etl', oid, 'EXECUTE'))
  UNION ALL SELECT 7, 'no rights on meta.object_api_request; etl.object_run_begin returns api_request',
    NOT EXISTS (SELECT 1 FROM t, unnest(ARRAY['dwh_app', 'dwh_etl']) r, unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) p
                 WHERE has_table_privilege(r, t.oid, p))
    AND position('api_request' IN pg_get_functiondef('etl.object_run_begin(bigint)'::regprocedure)) > 0
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
