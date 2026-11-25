-- I12 check of 034: read-only catalog verification, runs as dwh_app. Expected: 7 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH t AS (SELECT c.oid, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE (n.nspname, c.relname) IN (('meta', 'object_file_config'), ('etl', 'object_file')) AND c.relkind = 'r'),
f AS (SELECT p.oid, n.nspname, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
       WHERE (n.nspname, p.proname) IN (
         ('app', 'integration_file_object_create'), ('app', 'integration_file_config_get'),
         ('app', 'integration_file_config_save'), ('app', 'integration_file_object_capture'),
         ('app', 'integration_file_registry'), ('etl', 'object_file_state'), ('etl', 'object_file_record'))),
helpers AS (SELECT p.oid, p.proacl, p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE (n.nspname, p.proname) IN (('app', 'integration_file_object'), ('etl', 'object_file_running'),
                     ('meta', 'object_file_config_guard'), ('meta', 'object_file_reject_truncate'),
                     ('meta', 'object_file_config_copy_to_draft'))),
checks AS (
  SELECT 1 AS n, 'meta.source_object accepts object_type FILE' AS check_name,
    EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'meta.source_object'::regclass
             AND conname = 'source_object_object_type_check' AND pg_get_constraintdef(oid) LIKE '%FILE%') AS ok
  UNION ALL SELECT 2, 'meta.object_file_config with format, mode, mask, no-credential and draft-only guards',
    (SELECT count(*) FROM t WHERE relname = 'object_file_config') = 1
    AND (SELECT count(*) FROM pg_constraint c, t WHERE c.conrelid = t.oid AND t.relname = 'object_file_config'
          AND c.conname IN ('object_file_config_no_secrets', 'object_file_config_mask', 'object_file_config_format',
                            'object_file_config_mode')) = 4
    AND (SELECT count(*) FROM pg_trigger g, t WHERE g.tgrelid = t.oid AND NOT g.tgisinternal
          AND g.tgname IN ('trg_object_file_config_guard', 'trg_object_file_config_no_truncate')) = 2
    AND EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'meta.object_config_version'::regclass
                 AND tgname = 'trg_object_file_config_copy')
  UNION ALL SELECT 3, 'register etl.object_file: one row per content, no TRUNCATE',
    (SELECT count(*) FROM t WHERE relname = 'object_file') = 1
    AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('etl.object_file') AND conname = 'object_file_content'
                 AND contype = 'u')
    AND EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = to_regclass('etl.object_file') AND tgname = 'trg_object_file_no_truncate')
  UNION ALL SELECT 4, '7 functions of 034 present, SECURITY DEFINER with search_path; no EXECUTE for PUBLIC',
    (SELECT count(*) FROM f) = 7
    AND (SELECT count(*) FROM f WHERE prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) = 7
    AND NOT EXISTS (SELECT 1 FROM (SELECT proacl, proowner FROM f UNION ALL SELECT proacl, proowner FROM helpers) x,
                aclexplode(coalesce(x.proacl, acldefault('f', x.proowner))) a WHERE a.grantee = 0)
  UNION ALL SELECT 5, 'dwh_app executes the 5 administration functions only',
    (SELECT count(*) FROM f WHERE nspname = 'app' AND has_function_privilege('dwh_app', oid, 'EXECUTE')) = 5
    AND NOT EXISTS (SELECT 1 FROM f WHERE nspname = 'etl' AND has_function_privilege('dwh_app', oid, 'EXECUTE'))
    AND NOT EXISTS (SELECT 1 FROM helpers WHERE has_function_privilege('dwh_app', oid, 'EXECUTE'))
  UNION ALL SELECT 6, 'dwh_etl executes etl.object_file_state and etl.object_file_record only',
    (SELECT count(*) FROM f WHERE has_function_privilege('dwh_etl', oid, 'EXECUTE')) = 2
    AND NOT EXISTS (SELECT 1 FROM f WHERE nspname = 'app' AND has_function_privilege('dwh_etl', oid, 'EXECUTE'))
    AND NOT EXISTS (SELECT 1 FROM helpers WHERE has_function_privilege('dwh_etl', oid, 'EXECUTE'))
  UNION ALL SELECT 7, 'no rights on the new tables; etl.object_run_begin returns file_config',
    NOT EXISTS (SELECT 1 FROM t, unnest(ARRAY['dwh_app', 'dwh_etl']) r,
                       unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p
                 WHERE has_table_privilege(r, t.oid, p))
    AND position('file_config' IN pg_get_functiondef('etl.object_run_begin(bigint)'::regprocedure)) > 0
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
