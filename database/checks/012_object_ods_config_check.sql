-- I06-05 check of 012: read-only catalog verification of the ODS configuration and the ODS step.
-- SELECT only; pg_catalog only. Runs as dwh_app (run_readonly_sql.ps1) or as the owner.
-- Expected: 10 results, every one PASS. The 008, 010 and 011 checks stay valid.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH rel AS (SELECT c.oid, n.nspname, c.relname, c.relacl, c.relowner FROM pg_class c
               JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE c.relkind = 'r'
                AND ((n.nspname = 'meta' AND c.relname IN ('object_ods_version', 'object_ods_field', 'object_ods_key_part',
                                                           'object_ods_validation', 'object_ods_current'))
                     OR (n.nspname = 'etl' AND c.relname IN ('object_ods_run', 'object_ods_record_change')))),
fn AS (SELECT p.oid, n.nspname, p.proname, p.prosrc, p.prosecdef, p.proconfig, p.proacl, p.proowner
         FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE (n.nspname = 'app' AND p.proname IN ('integration_ods_config_get', 'integration_ods_config_save_draft',
                 'integration_ods_config_record_validation', 'integration_ods_config_publish',
                 'integration_ods_proposal_create'))
           OR (n.nspname = 'etl' AND p.proname IN ('object_ods_run_begin', 'object_ods_run_finish', 'object_ods_run_close',
                 'object_ods_record_change_guard'))
           OR (n.nspname = 'meta' AND p.proname IN ('object_ods_guard', 'object_ods_definition', 'object_ods_hash',
                 'object_stg_table'))),
checks AS (
  SELECT 1 AS n, 'seven new tables: five meta.object_ods_*, etl.object_ods_run, etl.object_ods_record_change' AS check_name,
    (SELECT count(*) FROM rel) = 7 AS ok
  UNION ALL SELECT 2, 'thirteen new functions',
    (SELECT count(*) FROM fn) = 13
  UNION ALL SELECT 3, 'white list of transformations and technical fields INSERT_ONLY',
    EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = 'meta.object_ods_field'::regclass AND k.contype = 'c'
              AND pg_get_constraintdef(k.oid) LIKE '%DATE_FROM_TEXT%' AND pg_get_constraintdef(k.oid) LIKE '%LOADED_AT%')
    AND EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = 'meta.object_ods_field'::regclass
                  AND k.conname = 'object_ods_field_technical')
  UNION ALL SELECT 4, 'published ODS versions immutable (guard triggers), validation append-only',
    (SELECT count(*) FROM pg_trigger t WHERE NOT t.tgisinternal
       AND t.tgrelid IN ('meta.object_ods_version'::regclass, 'meta.object_ods_field'::regclass,
                         'meta.object_ods_key_part'::regclass, 'meta.object_ods_validation'::regclass,
                         'meta.object_ods_current'::regclass)) = 9
  UNION ALL SELECT 5, 'change proposal belongs to one LOAD or ODS version',
    EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = 'meta.target_change_proposal'::regclass
              AND a.attname = 'ods_version_id' AND NOT a.attisdropped)
    AND EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = 'meta.target_change_proposal'::regclass
                  AND k.conname = 'target_change_proposal_one_version')
  UNION ALL SELECT 6, 'ODS record history append-only (insert/update/delete/truncate triggers)',
    (SELECT count(*) FROM pg_trigger t WHERE NOT t.tgisinternal
       AND t.tgrelid = 'etl.object_ods_record_change'::regclass) = 3
  UNION ALL SELECT 7, 'callable functions: SECURITY DEFINER with fixed search_path',
    NOT EXISTS (SELECT 1 FROM fn WHERE (nspname = 'app' OR proname IN ('object_ods_run_begin', 'object_ods_run_finish'))
                  AND NOT (prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')))
  UNION ALL SELECT 8, 'tables: no grants except owner, INSERT on ODS history for dwh_etl',
    NOT EXISTS (SELECT 1 FROM rel, aclexplode(coalesce(rel.relacl, acldefault('r', rel.relowner))) x
                 WHERE x.grantee <> rel.relowner
                   AND NOT (rel.relname = 'object_ods_record_change' AND x.privilege_type = 'INSERT'
                            AND x.grantee = (SELECT oid FROM pg_roles WHERE rolname = 'dwh_etl')))
    AND has_table_privilege('dwh_etl', 'etl.object_ods_record_change', 'INSERT')
    AND NOT has_table_privilege('dwh_etl', 'etl.object_ods_record_change', 'SELECT')
  UNION ALL SELECT 9, 'app wrappers: EXECUTE only for owner and dwh_app',
    (SELECT bool_and(has_function_privilege('dwh_app', oid, 'EXECUTE')) FROM fn WHERE nspname = 'app')
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE fn.nspname = 'app'
                       AND x.grantee NOT IN (fn.proowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app')))
  UNION ALL SELECT 10, 'ODS run begin/finish: EXECUTE only for owner and dwh_etl; internal functions for nobody',
    (SELECT bool_and(has_function_privilege('dwh_etl', oid, 'EXECUTE')) FROM fn
      WHERE proname IN ('object_ods_run_begin', 'object_ods_run_finish'))
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE fn.nspname IN ('etl', 'meta') AND x.grantee <> fn.proowner
                       AND NOT (fn.proname IN ('object_ods_run_begin', 'object_ods_run_finish')
                                AND x.grantee = (SELECT oid FROM pg_roles WHERE rolname = 'dwh_etl')))
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
