-- I13-08 check of 014: read-only catalog verification of the run-finish variants with diagnostics.
-- SELECT only; pg_catalog only. Runs as dwh_app (run_readonly_sql.ps1) or as the owner.
-- Expected: 7 results, every one PASS. The 010 and 012 checks stay valid.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH fn AS (SELECT p.oid, p.proname, p.pronargs, p.prosecdef, p.proconfig, p.proacl, p.proowner
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'etl'
               AND p.proname IN ('object_run_finish', 'object_ods_run_finish', 'load_error_diagnostics_valid')),
new_fn AS (SELECT * FROM fn WHERE pronargs = 12 OR proname = 'load_error_diagnostics_valid'),
checks AS (
  SELECT 1 AS n, 'two new 12-parameter finish variants and one helper' AS check_name,
    (SELECT count(*) FROM fn WHERE pronargs = 12) = 2
    AND (SELECT count(*) FROM fn WHERE proname = 'load_error_diagnostics_valid') = 1 AS ok
  UNION ALL SELECT 2, '10-parameter finish variants of 010 and 012 still present',
    (SELECT count(*) FROM fn WHERE pronargs = 10) = 2
  UNION ALL SELECT 3, 'new finish variants: SECURITY DEFINER with fixed search_path',
    NOT EXISTS (SELECT 1 FROM new_fn WHERE pronargs = 12
                  AND NOT (prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')))
  UNION ALL SELECT 4, 'new finish variants: EXECUTE only for owner and dwh_etl; helper for nobody',
    (SELECT bool_and(has_function_privilege('dwh_etl', oid, 'EXECUTE')) FROM new_fn WHERE pronargs = 12)
    AND NOT EXISTS (SELECT 1 FROM new_fn, aclexplode(coalesce(new_fn.proacl, acldefault('f', new_fn.proowner))) x
                     WHERE x.grantee <> new_fn.proowner
                       AND NOT (new_fn.pronargs = 12 AND x.grantee = (SELECT oid FROM pg_roles WHERE rolname = 'dwh_etl')))
  UNION ALL SELECT 5, 'dwh_app cannot finish runs (old or new variants)',
    NOT EXISTS (SELECT 1 FROM fn WHERE proname <> 'load_error_diagnostics_valid'
                  AND has_function_privilege('dwh_app', oid, 'EXECUTE'))
  UNION ALL SELECT 6, 'etl.error_log: bad_value text, diagnostic_context jsonb',
    (SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
      WHERE a.attrelid = 'etl.error_log'::regclass AND a.attname = 'bad_value' AND NOT a.attisdropped) = 'text'
    AND (SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
          WHERE a.attrelid = 'etl.error_log'::regclass AND a.attname = 'diagnostic_context' AND NOT a.attisdropped) = 'jsonb'
  UNION ALL SELECT 7, 'etl.error_log has no UPDATE trigger',
    NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = 'etl.error_log'::regclass AND NOT t.tgisinternal
                  AND (t.tgtype & 16) <> 0)
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
