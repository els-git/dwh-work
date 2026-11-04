-- I05 check of 008: read-only catalog verification of the object configuration model.
-- SELECT only; pg_catalog only. Runs as dwh_app (run_readonly_sql.ps1) or as the owner.
-- Expected: 10 results, every one PASS. The 006 and 007 checks stay valid.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH t AS (SELECT c.oid, c.relname, c.relacl, c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'meta' AND c.relkind = 'r'
              AND c.relname IN ('object_config_version', 'object_config_field', 'object_config_key_part',
                                'object_config_validation', 'object_config_current', 'target_change_proposal')),
api AS (SELECT p.oid, p.prosecdef, p.proconfig, p.proacl, p.proowner FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'app' AND p.proname IN ('integration_object_config_get', 'integration_object_config_save_draft',
               'integration_object_config_record_validation', 'integration_object_config_publish',
               'integration_object_config_set_drift', 'integration_target_proposal_create',
               'integration_target_proposal_reject')),
helpers AS (SELECT p.oid, p.proacl, p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'meta' AND p.proname IN ('object_config_guard', 'object_config_reject_change',
                   'object_config_definition', 'object_config_hash', 'source_is_legacy')),
checks AS (
  SELECT 1 AS n, '6 configuration tables present' AS check_name, (SELECT count(*) FROM t) = 6 AS ok
  UNION ALL SELECT 2, 'one DRAFT per object (partial unique index)',
    EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'meta' AND indexname = 'object_config_one_draft')
  UNION ALL SELECT 3, 'immutability and TRUNCATE triggers enabled (10)',
    (SELECT count(*) FROM pg_trigger WHERE tgrelid IN (SELECT oid FROM t) AND NOT tgisinternal AND tgenabled = 'O') = 10
  UNION ALL SELECT 4, 'key parts reference the field map (FK)',
    EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'meta.object_config_key_part'::regclass AND contype = 'f'
             AND confrelid = 'meta.object_config_field'::regclass)
  UNION ALL SELECT 5, '7 API functions present, SECURITY DEFINER with fixed search_path',
    (SELECT count(*) FROM api WHERE prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) = 7
  UNION ALL SELECT 6, 'dwh_app may EXECUTE the 7 API functions',
    (SELECT count(*) FROM api WHERE has_function_privilege('dwh_app', oid, 'EXECUTE')) = 7
  UNION ALL SELECT 7, 'API functions: grants only to owner and dwh_app',
    NOT EXISTS (SELECT 1 FROM api, aclexplode(coalesce(api.proacl, acldefault('f', api.proowner))) x
                 WHERE x.grantee NOT IN (api.proowner, (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app')))
  UNION ALL SELECT 8, 'helper functions: no grants beyond owner',
    (SELECT count(*) FROM helpers) = 5
    AND NOT EXISTS (SELECT 1 FROM helpers, aclexplode(coalesce(helpers.proacl, acldefault('f', helpers.proowner))) x
                     WHERE x.grantee <> helpers.proowner)
  UNION ALL SELECT 9, 'configuration tables: no grants beyond owner',
    NOT EXISTS (SELECT 1 FROM t, aclexplode(coalesce(t.relacl, acldefault('r', t.relowner))) x WHERE x.grantee <> t.relowner)
  UNION ALL SELECT 10, 'snapshot function refuses legacy sources (M7)',
    EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'app' AND p.proname = 'integration_source_metadata_capture'
               AND p.prosrc LIKE '%source_is_legacy%')
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
