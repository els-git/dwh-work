-- I23 check of 023: read-only catalog verification of SQL versions of DM marts and of the role dwh_dm_sql_ro.
-- SELECT only; pg_catalog. Runs as dwh_app or as the owner.
-- Expected: 7 results, every one PASS. Check 018 stays 9/9 and check 019 8/8 (new functions are sql_mart_*).
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH fn AS (SELECT p.oid, n.nspname, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname IN ('meta', 'app') AND p.proname LIKE 'sql\_mart\_%'),
src AS (SELECT p.oid::regprocedure::text AS fn, pg_get_functiondef(p.oid) AS def FROM pg_proc p
         WHERE p.oid IN ('meta.dm_mart_version_problems(bigint)'::regprocedure, 'etl.dm_mart_enqueue_after_ods(text)'::regprocedure,
                         'app.dm_mart_version_create(text, bigint)'::regprocedure,
                         'app.dm_mart_version_save(text, bigint, jsonb)'::regprocedure)),
ro AS (SELECT r.* FROM pg_roles r WHERE r.rolname = 'dwh_dm_sql_ro'),
rel AS (SELECT c.oid, n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'v', 'm', 'p', 'f') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname NOT LIKE 'pg\_%'),
checks AS (
  SELECT 1 AS n, 'meta.dm_mart_version: version_kind, sql_text, sql_sha256, sql_tables and constraint dm_mart_version_sql_shape' AS check_name,
    (SELECT count(*) FROM pg_attribute WHERE attrelid = 'meta.dm_mart_version'::regclass AND NOT attisdropped
       AND attname IN ('version_kind', 'sql_text', 'sql_sha256', 'sql_tables')) = 4
    AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'meta.dm_mart_version'::regclass
                 AND conname = 'dm_mart_version_sql_shape') AS ok
  UNION ALL SELECT 2, 'app.sql_mart_save: SECURITY DEFINER, fixed search_path, EXECUTE only for the owner and dwh_app',
    EXISTS (SELECT 1 FROM fn WHERE nspname = 'app' AND proname = 'sql_mart_save' AND prosecdef
             AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%'))
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE fn.nspname = 'app' AND x.grantee <> fn.proowner
                       AND x.grantee <> (SELECT oid FROM pg_roles WHERE rolname = 'dwh_app'))
  UNION ALL SELECT 3, 'meta.sql_mart_problems: not SECURITY DEFINER, owner only; two sql_mart_* functions',
    (SELECT count(*) FROM fn) = 2
    AND EXISTS (SELECT 1 FROM fn WHERE nspname = 'meta' AND proname = 'sql_mart_problems' AND NOT prosecdef)
    AND NOT EXISTS (SELECT 1 FROM fn, aclexplode(coalesce(fn.proacl, acldefault('f', fn.proowner))) x
                     WHERE fn.nspname = 'meta' AND x.grantee <> fn.proowner)
  UNION ALL SELECT 4, '018 functions changed in place: SQL problems, tables of the query after ODS, drafts copy the query, constructor save',
    (SELECT count(*) FROM src WHERE
       (fn LIKE 'meta.dm_mart_version_problems%' AND def LIKE '%meta.sql_mart_problems(p_version_id)%')
       OR (fn LIKE 'etl.dm_mart_enqueue_after_ods%' AND def LIKE '%v_table = ANY (coalesce(v.sql_tables%')
       OR (fn LIKE 'app.dm_mart_version_create%' AND def LIKE '%pub.sql_text, pub.sql_sha256, pub.sql_tables%')
       OR (fn LIKE 'app.dm_mart_version_save%' AND def LIKE '%version_kind = ''BUILDER'', sql_text = NULL%')) = 4
  UNION ALL SELECT 5, 'role dwh_dm_sql_ro: plain login role, NOINHERIT, no memberships, read-only transactions',
    EXISTS (SELECT 1 FROM ro WHERE rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolcreaterole
             AND NOT rolcreatedb AND NOT rolreplication AND NOT rolbypassrls
             AND 'default_transaction_read_only=on' = ANY (rolconfig))
    AND NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = (SELECT oid FROM ro))
  UNION ALL SELECT 6, 'dwh_dm_sql_ro: USAGE on ods and SELECT on every ods table, no write on ods',
    has_schema_privilege('dwh_dm_sql_ro', 'ods', 'USAGE')
    AND NOT has_schema_privilege('dwh_dm_sql_ro', 'ods', 'CREATE')
    AND NOT EXISTS (SELECT 1 FROM rel WHERE nspname = 'ods' AND NOT has_table_privilege('dwh_dm_sql_ro', oid, 'SELECT'))
    AND NOT EXISTS (SELECT 1 FROM rel WHERE nspname = 'ods'
                     AND has_table_privilege('dwh_dm_sql_ro', oid, 'INSERT, UPDATE, DELETE, TRUNCATE'))
  UNION ALL SELECT 7, 'dwh_dm_sql_ro: no privilege on any table outside ods',
    NOT EXISTS (SELECT 1 FROM rel WHERE nspname <> 'ods'
                 AND has_table_privilege('dwh_dm_sql_ro', oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE'))
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
