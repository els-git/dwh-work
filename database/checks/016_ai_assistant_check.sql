-- I17 check of 016: read-only catalog verification of the AI assistant package.
-- SELECT only; pg_catalog (and the whitelist seed in meta.ai_report_object). Runs as dwh_app or as the owner.
-- Expected: 10 results, every one PASS. Checks 001-015 are not affected.
-- AI_* permissions are verified by the test (dwh_app does not read app.permission).
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH rel AS (SELECT c.oid, n.nspname, c.relname, c.relkind, c.relacl, c.relowner
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE (n.nspname = 'meta' AND c.relname IN ('ai_report_object', 'ai_value_column', 'ai_glossary', 'ai_example'))
                 OR (n.nspname = 'app' AND c.relname = 'ai_request_log')),
fn AS (SELECT p.oid, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner
         FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'app' AND p.proname LIKE 'ai\_%'),
ro AS (SELECT * FROM pg_roles WHERE rolname = 'dwh_ai_ro'),
dmrel AS (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'dm' AND c.relkind IN ('r', 'v', 'm', 'p', 'f')),
checks AS (
  SELECT 1 AS n, 'five tables: meta.ai_report_object/ai_value_column/ai_glossary/ai_example, app.ai_request_log' AS check_name,
    (SELECT count(*) FROM rel WHERE relkind = 'r') = 5 AS ok
  UNION ALL SELECT 2, 'ten functions app.ai_*: SECURITY DEFINER with fixed search_path',
    (SELECT count(*) FROM fn) = 10
    AND NOT EXISTS (SELECT 1 FROM fn WHERE NOT (prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')))
  UNION ALL SELECT 3, 'role dwh_ai_ro: login, no elevated attributes, no memberships',
    EXISTS (SELECT 1 FROM ro WHERE rolcanlogin AND NOT (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls))
    AND NOT EXISTS (SELECT 1 FROM pg_auth_members m JOIN ro ON ro.oid = m.member)
  UNION ALL SELECT 4, 'dwh_ai_ro settings: read-only transactions, statement_timeout, search_path dm',
    EXISTS (SELECT 1 FROM ro WHERE 'default_transaction_read_only=on' = ANY (rolconfig)
             AND EXISTS (SELECT 1 FROM unnest(rolconfig) c WHERE c LIKE 'statement_timeout=%')
             AND 'search_path=dm' = ANY (rolconfig))
  UNION ALL SELECT 5, 'dwh_ai_ro: USAGE on dm, SELECT on every dm object, nothing else in dm',
    has_schema_privilege('dwh_ai_ro', 'dm', 'USAGE') AND NOT has_schema_privilege('dwh_ai_ro', 'dm', 'CREATE')
    AND NOT EXISTS (SELECT 1 FROM dmrel WHERE NOT has_table_privilege('dwh_ai_ro', dmrel.oid, 'SELECT'))
    AND NOT EXISTS (SELECT 1 FROM dmrel WHERE has_table_privilege('dwh_ai_ro', dmrel.oid, 'INSERT, UPDATE, DELETE, TRUNCATE'))
  UNION ALL SELECT 6, 'dwh_ai_ro: no USAGE on any other schema of the database',
    NOT EXISTS (SELECT 1 FROM pg_namespace n
                 WHERE n.nspname NOT IN ('dm', 'pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\_%'
                   AND has_schema_privilege('dwh_ai_ro', n.oid, 'USAGE'))
  UNION ALL SELECT 7, 'default privileges: new dm tables readable by dwh_ai_ro',
    EXISTS (SELECT 1 FROM pg_default_acl d JOIN pg_namespace n ON n.oid = d.defaclnamespace, aclexplode(d.defaclacl) x
             WHERE n.nspname = 'dm' AND d.defaclobjtype = 'r' AND d.defaclrole = n.nspowner
               AND x.grantee = (SELECT oid FROM ro) AND x.privilege_type = 'SELECT')
  UNION ALL SELECT 8, 'meta.ai_* tables: SELECT only for owner and dwh_app; request log: owner only',
    NOT EXISTS (SELECT 1 FROM rel, aclexplode(coalesce(rel.relacl, acldefault('r', rel.relowner))) x
                 WHERE x.grantee <> rel.relowner
                   AND (rel.nspname = 'app' OR x.grantee <> (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app')
                        OR x.privilege_type <> 'SELECT'))
    AND (SELECT bool_and(has_table_privilege('dwh_app', oid, 'SELECT')) FROM rel WHERE nspname = 'meta')
  UNION ALL SELECT 9, 'functions: EXECUTE for dwh_app on 8 calls, ai_session_user/ai_user_permissions owner only',
    (SELECT count(*) FROM fn WHERE has_function_privilege('dwh_app', oid, 'EXECUTE')) = 8
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE x.grantee <> fn.proowner
                       AND (fn.proname IN ('ai_session_user', 'ai_user_permissions')
                            OR x.grantee <> (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app')))
  UNION ALL SELECT 10, 'whitelist: dm.v_retail_application_current active with a required permission',
    EXISTS (SELECT 1 FROM meta.ai_report_object
             WHERE schema_name = 'dm' AND object_name = 'v_retail_application_current' AND is_active
               AND required_permission_code <> '')
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
